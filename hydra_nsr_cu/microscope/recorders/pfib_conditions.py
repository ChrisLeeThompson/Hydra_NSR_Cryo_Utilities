"""Records and restores the ion beam's user-visible state.

Captured fields:

* ``plasma_gas`` — the current plasma gas (ion species).
* ``high_voltage`` — accelerating voltage in volts.
* ``beam_current`` — beam current in amperes.
* ``is_on`` — whether the beam is currently on.

Not captured:

* Patterning state, scan rotation, FOV, magnification, dwell time,
  detector configuration, etc. The recorder is named after PFIB
  *conditions* — i.e. the parameters a sputter or deposition activity
  would mutate as part of its setup. State that activities don't
  touch isn't worth saving and restoring.

Restore order matters
---------------------
On real hardware, plasma gas changes can affect the valid ranges for
HV and beam current. Setting HV to 30 kV before the gas is settled
could clip to a lower allowed value and silently leave the system at
the wrong voltage. So the restore sequence is:

    1. Plasma gas
    2. High voltage
    3. Beam current
    4. Beam on/off

Beam-on-off is restored last because the beam can be safely toggled
regardless of the other parameter values. Toggling first might
require the parameters to be re-validated.

Beam-on-off semantics
---------------------
If the beam was *off* at capture and is *on* at restore: turn off.
If the beam was *on* at capture and is *off* at restore: turn on.
If the state matches at restore, no-op.

We don't try to handle every transient state (e.g. a beam that's
"warming up" or "cooling down") — those are AutoScript's concern,
and turn_on/off is blocking until the device reports the requested
state.

Selective restore
-----------------
:meth:`PFIBConditionsRecorder.restore` takes two required keyword
flags so a caller can restore the two state groups independently:

* ``restore_species`` — plasma gas (ion species).
* ``restore_beam_electrical`` — high voltage, beam current, and
  on/off. On/off is grouped with HV/current because it's part of the
  beam's electrical operating state; ion species (gas) is the
  orthogonal axis.

Capture is always whole (all four fields); only restore is selective.
The canonical gas → HV → current → on/off order is preserved
regardless of which groups are enabled, so a both-groups restore
still sets gas before HV/current.

Restoring only ``restore_beam_electrical`` (the common "revert
voltage/current but keep the sputter species" case) sets the captured
HV/current against the *current* gas. AutoScript snaps beam_current to
the nearest preset valid for that gas — the expected "or nearest
current" behavior.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from ..ion_beam_ops import IonBeamOpsLike, PlasmaGasValue

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PFIBConditionsSnapshot:
    """Opaque snapshot of the ion beam's user-visible state.

    Constructed by :meth:`PFIBConditionsRecorder.capture`. Activities
    hold this value across their parameter changes and pass it back
    to :meth:`PFIBConditionsRecorder.restore` to revert.
    """
    plasma_gas: PlasmaGasValue
    high_voltage: float
    beam_current: float
    is_on: bool

    def __repr__(self) -> str:
        # Custom repr keeps log lines compact and unambiguous about
        # which units the captured numerics are in.
        return (
            f"PFIBConditionsSnapshot("
            f"plasma_gas={self.plasma_gas!r}, "
            f"high_voltage={self.high_voltage:.0f}V, "
            f"beam_current={self.beam_current:.3e}A, "
            f"is_on={self.is_on})"
        )


class PFIBConditionsRecorder:
    """Captures and restores ion beam conditions."""

    def __init__(self, ion_beam: IonBeamOpsLike) -> None:
        self._ion_beam = ion_beam

    def capture(self) -> PFIBConditionsSnapshot:
        """Read current ion beam state into a snapshot.

        Each property read goes through the ops layer, which on the
        real client is a thin wrapper over an AutoScript call.
        Failures propagate to the caller — the activity decides
        whether to surface them as a workflow failure or to skip
        the save/restore and proceed without it.
        """
        snapshot = PFIBConditionsSnapshot(
            plasma_gas=self._ion_beam.plasma_gas,
            high_voltage=self._ion_beam.high_voltage,
            beam_current=self._ion_beam.beam_current,
            is_on=self._ion_beam.is_on,
        )
        logger.info("PFIB conditions captured: %r", snapshot)
        return snapshot

    def restore(
        self,
        snapshot: PFIBConditionsSnapshot,
        *,
        restore_species: bool,
        restore_beam_electrical: bool,
    ) -> None:
        """Restore selected ion beam state from a captured snapshot.

        Selective per group (both flags required — the caller must
        state intent, so a forgotten flag can't silently restore a
        group):

        * ``restore_species`` — restore ``plasma_gas`` (ion species).
        * ``restore_beam_electrical`` — restore ``high_voltage``,
          ``beam_current``, and ``is_on`` (the beam's electrical
          state; on/off is grouped here — see the module docstring).

        The canonical restore order (gas → HV → current → on/off) is
        preserved regardless of which groups are enabled: gas (if
        enabled) is always set before HV/current, since a gas change
        shifts their valid ranges.

        Best-effort — if a single field's restore fails, the others in
        the enabled groups still attempt (logged with the failure). A
        partial restore is more useful than no restore.

        Called from the workflow runner's ``_after_run`` on a committed
        run, with the flags mapped from the run's settings snapshot.
        """
        logger.info(
            "PFIB conditions restoring "
            "(species=%s, beam_electrical=%s): %r",
            restore_species, restore_beam_electrical, snapshot,
        )

        if not restore_species and not restore_beam_electrical:
            # The recorder shouldn't have been constructed with both
            # groups off, but guard anyway so a caller mistake is a
            # logged no-op rather than a silent surprise.
            logger.info(
                "PFIB restore: both groups disabled; nothing to restore"
            )
            return

        # Plasma gas first — see module docstring for rationale.
        if restore_species:
            try:
                if self._ion_beam.plasma_gas != snapshot.plasma_gas:
                    self._ion_beam.plasma_gas = snapshot.plasma_gas
            except Exception:
                logger.exception(
                    "PFIB restore: failed to set plasma_gas to %r",
                    snapshot.plasma_gas,
                )

        if restore_beam_electrical:
            # High voltage.
            try:
                if self._ion_beam.high_voltage != snapshot.high_voltage:
                    self._ion_beam.high_voltage = snapshot.high_voltage
            except Exception:
                logger.exception(
                    "PFIB restore: failed to set high_voltage to %.0f V",
                    snapshot.high_voltage,
                )

            # Beam current.
            try:
                if self._ion_beam.beam_current != snapshot.beam_current:
                    self._ion_beam.beam_current = snapshot.beam_current
            except Exception:
                logger.exception(
                    "PFIB restore: failed to set beam_current to %.3e A",
                    snapshot.beam_current,
                )

            # On/off state last.
            try:
                currently_on = self._ion_beam.is_on
                if snapshot.is_on and not currently_on:
                    self._ion_beam.turn_on()
                elif not snapshot.is_on and currently_on:
                    self._ion_beam.turn_off()
            except Exception:
                logger.exception(
                    "PFIB restore: failed to set beam on/off to %s",
                    snapshot.is_on,
                )

        logger.info("PFIB conditions restore complete")