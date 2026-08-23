"""Records and restores the ion beam's user-visible state.

Captures ``plasma_gas``, ``high_voltage``, ``beam_current``, and
``is_on`` — the PFIB *conditions* a sputter or deposition activity
mutates during setup. Nothing else (patterning, scan rotation, FOV,
detectors, ...) is saved.

Restore order is gas → high voltage → beam current → on/off. A plasma
gas change can shift the valid HV / beam-current ranges, so those are
written only after the gas; on/off goes last because the beam can be
toggled regardless of the other values. Capture is always whole;
restore is selective via two required flags, ``restore_species``
(plasma gas) and ``restore_beam_electrical`` (HV, beam current, on/off),
with the order preserved whichever groups are enabled. Restoring only
the electrical group writes the captured HV / current against the
current gas; AutoScript snaps beam current to the nearest valid preset.

AutoScript calls block until the xT server reports the requested
state, but the server's own wait can give up first (e.g. "Wait for
beam to turn on timed out") while a species switch is still completing.
Server errors carry no stable taxonomy, so this module never branches
on exception text; it verifies by *readback* over a bounded settle
window instead. Policy: ``plasma_gas`` is never re-issued (verify only;
re-striking a source mid-transition is unsafe); ``turn_on`` gets at
most one retry, and only once the beam is verified off and the species
readback matched; HV / beam current are single idempotent writes with
no readback compare. No cleanup writes on failure — hardware stays as
the last successful write left it, and failures come back in the
returned list.

:meth:`PFIBConditionsRecorder.restore` also takes a ``should_abandon``
predicate, checked between writes and between settle polls. When it
returns True all remaining work is skipped and an :data:`ABANDONED_FIELD`
marker is appended; an AutoScript call already in flight can't be
interrupted, so abandon takes effect when it returns.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Callable, List, Optional

from ..ion_beam_ops import IonBeamOpsLike, PlasmaGasValue

logger = logging.getLogger(__name__)


# --- Settle-window tuning ------------------------------------------------

# Species switch: gas purge + source re-strike + conditioning. The
# vendor envelope for a Hydra multi-species switch is single-digit
# minutes; the xT server's own wait has been observed to give up
# before the source finishes. 10 min covers the envelope at one cheap
# readback every 10 s.
PLASMA_GAS_SETTLE_POLL_S: float = 10.0
PLASMA_GAS_SETTLE_DEADLINE_S: float = 600.0

# Beam strike on a settled source is much faster (tens of seconds to
# a couple of minutes). 5 min is generous without hanging the worker
# thread indefinitely.
BEAM_ON_SETTLE_POLL_S: float = 5.0
BEAM_ON_SETTLE_DEADLINE_S: float = 300.0

# Shorter re-verify window after the single turn_on retry — if the
# first strike plus its full settle window didn't do it, the retry
# either works quickly or the source needs an operator.
BEAM_ON_RETRY_SETTLE_DEADLINE_S: float = 120.0

# ``RestoreFailure.field`` value marking a user abandon rather than a
# hardware failure. Callers branch on it to word their reporting
# ("restore abandoned" vs "restore failed").
ABANDONED_FIELD: str = "abandoned"


@dataclass(frozen=True)
class RestoreFailure:
    """One field a best-effort restore could not set.

    ``field`` is a short display name ("ion species", "high voltage",
    "beam current", "beam on/off"), or :data:`ABANDONED_FIELD` when
    the restore was abandoned by the user rather than failing.
    ``error`` is ``"<ExcType>: <message>"`` — per the AutoScript
    convention that ``str(exc)`` is human-readable — or a synthesized
    description when no exception was raised (e.g. a readback that
    never reached its target within the settle window).
    """
    field: str
    error: str


def _exc_str(exc: BaseException) -> str:
    """The user-facing rendering of an exception: ``"<Type>: <message>"``."""
    return f"{type(exc).__name__}: {exc}"


def _settle_poll(
    check: Callable[[], bool],
    *,
    poll_interval_s: float,
    deadline_s: float,
    should_abandon: Callable[[], bool],
    description: str,
    sleep: Callable[[float], None] = time.sleep,
) -> bool:
    """Poll ``check()`` until True, a deadline passes, or user abandon.

    The first check is immediate (no initial sleep), so an operation
    that already completed costs one readback and zero waiting. A
    ``check()`` that raises counts as False for that iteration — the
    server can be transiently unresponsive mid-switch — and is logged
    at DEBUG. ``time.monotonic`` throughout, so the window survives a
    clock change.

    Returns True iff the check passed. On a False return the caller
    distinguishes abandon from deadline by calling ``should_abandon()``
    itself.
    """
    deadline = time.monotonic() + deadline_s
    while True:
        if should_abandon():
            logger.info(
                "PFIB restore: settle wait for %s abandoned", description,
            )
            return False
        try:
            if check():
                return True
        except Exception:
            logger.debug(
                "PFIB restore: settle check for %s raised; treating as "
                "not-yet-settled",
                description,
                exc_info=True,
            )
        if time.monotonic() >= deadline:
            logger.warning(
                "PFIB restore: %s not reached within %.0f s",
                description, deadline_s,
            )
            return False
        # Sleep in short slices, re-checking abandon between them, so
        # a Stop press or app shutdown is honored within ~1 s instead
        # of a full poll interval.
        remaining = poll_interval_s
        while remaining > 0:
            step = min(1.0, remaining)
            sleep(step)
            remaining -= step
            if remaining > 0 and should_abandon():
                break


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
    """Captures and restores ion beam conditions.

    The keyword timing arguments exist for tests (millisecond
    deadlines, no-op ``sleep``); production callers use the
    module-constant defaults.
    """

    def __init__(
        self,
        ion_beam: IonBeamOpsLike,
        *,
        plasma_settle_poll_s: float = PLASMA_GAS_SETTLE_POLL_S,
        plasma_settle_deadline_s: float = PLASMA_GAS_SETTLE_DEADLINE_S,
        beam_on_settle_poll_s: float = BEAM_ON_SETTLE_POLL_S,
        beam_on_settle_deadline_s: float = BEAM_ON_SETTLE_DEADLINE_S,
        beam_on_retry_deadline_s: float = BEAM_ON_RETRY_SETTLE_DEADLINE_S,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._ion_beam = ion_beam
        self._plasma_settle_poll_s = plasma_settle_poll_s
        self._plasma_settle_deadline_s = plasma_settle_deadline_s
        self._beam_on_settle_poll_s = beam_on_settle_poll_s
        self._beam_on_settle_deadline_s = beam_on_settle_deadline_s
        self._beam_on_retry_deadline_s = beam_on_retry_deadline_s
        self._sleep = sleep

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
        should_abandon: Callable[[], bool] = lambda: False,
    ) -> List[RestoreFailure]:
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
        the enabled groups still attempt. Failures don't raise; they
        come back in the returned list (empty on full success) so the
        caller can surface them to the user. The one exception is
        ``should_abandon`` returning True: that skips ALL remaining
        work and appends an :data:`ABANDONED_FIELD` marker.

        Exceptions from the SDK are recovered where recovery is safe —
        readback verification for the species switch and beam-on, one
        gated ``turn_on`` retry — per the module-docstring policy.

        Called from the workflow runner's ``_after_run`` after a fully
        successful run — a stopped, failed, or aborted run skips the
        restore entirely (see :meth:`CPWorkflow._after_run`) — with
        the flags mapped from the run's settings snapshot.
        """
        logger.info(
            "PFIB conditions restoring "
            "(species=%s, beam_electrical=%s): %r",
            restore_species, restore_beam_electrical, snapshot,
        )

        failures: List[RestoreFailure] = []

        if not restore_species and not restore_beam_electrical:
            # The recorder shouldn't have been constructed with both
            # groups off, but guard anyway so a caller mistake is a
            # logged no-op rather than a silent surprise.
            logger.info(
                "PFIB restore: both groups disabled; nothing to restore"
            )
            return failures

        # True when the plasma gas is known to be settled at its
        # target: either this restore verified the switch by readback,
        # or no switch was requested (the source has been steady since
        # the last activity finished). Gates the turn_on retry — never
        # re-strike a source that may be mid-transition.
        species_verified = True

        # Plasma gas first — see module docstring for rationale.
        if restore_species:
            if self._abandoned(should_abandon, failures,
                               "before ion species"):
                return failures
            species_verified = self._restore_plasma_gas(
                snapshot.plasma_gas, should_abandon, failures,
            )

        if restore_beam_electrical:
            # High voltage. Single attempt, no readback verify — see
            # module docstring.
            if self._abandoned(should_abandon, failures,
                               "before high voltage"):
                return failures
            try:
                if self._ion_beam.high_voltage != snapshot.high_voltage:
                    self._ion_beam.high_voltage = snapshot.high_voltage
            except Exception as exc:
                logger.exception(
                    "PFIB restore: failed to set high_voltage to %.0f V",
                    snapshot.high_voltage,
                )
                failures.append(RestoreFailure(
                    field="high voltage", error=_exc_str(exc),
                ))

            # Beam current. Single attempt, no readback verify.
            if self._abandoned(should_abandon, failures,
                               "before beam current"):
                return failures
            try:
                if self._ion_beam.beam_current != snapshot.beam_current:
                    self._ion_beam.beam_current = snapshot.beam_current
            except Exception as exc:
                logger.exception(
                    "PFIB restore: failed to set beam_current to %.3e A",
                    snapshot.beam_current,
                )
                failures.append(RestoreFailure(
                    field="beam current", error=_exc_str(exc),
                ))

            # On/off state last.
            if self._abandoned(should_abandon, failures,
                               "before beam on/off"):
                return failures
            self._restore_beam_on_off(
                snapshot.is_on, should_abandon, species_verified, failures,
            )

        if failures:
            logger.warning(
                "PFIB conditions restore finished with %d failure(s): %s",
                len(failures),
                "; ".join(f"{f.field} — {f.error}" for f in failures),
            )
        else:
            logger.info("PFIB conditions restore complete")
        return failures

    # --- Internals ---------------------------------------------------------

    @staticmethod
    def _abandoned(
        should_abandon: Callable[[], bool],
        failures: List[RestoreFailure],
        where: str,
    ) -> bool:
        """Check the abandon predicate at a step boundary.

        Appends the abandoned marker at most once no matter how many
        boundaries observe it (a mid-poll abandon is noticed both by
        the poll's own check and by the next field boundary).
        """
        if not should_abandon():
            return False
        if not any(f.field == ABANDONED_FIELD for f in failures):
            logger.warning(
                "PFIB restore: abandoned by user (%s); skipping all "
                "remaining fields", where,
            )
            failures.append(RestoreFailure(
                field=ABANDONED_FIELD,
                error=f"abandoned by user ({where})",
            ))
        return True

    def _restore_plasma_gas(
        self,
        target: PlasmaGasValue,
        should_abandon: Callable[[], bool],
        failures: List[RestoreFailure],
    ) -> bool:
        """Set the plasma gas back to ``target``; verify by readback.

        Returns True iff the readback verified at target. The switch
        is never re-issued: on an exception (typically the server's
        own wait giving up while the source is still conditioning) or
        a readback that doesn't yet match, the recovery is to WAIT —
        poll the readback for a bounded settle window — not to write
        again.
        """
        # The skip-if-equal read is guarded SEPARATELY from the write:
        # a transient readback glitch must not silently skip the
        # switch (setting the gas to its current value is a benign
        # no-op request, so when in doubt, write).
        try:
            if self._ion_beam.plasma_gas == target:
                return True
        except Exception:
            logger.warning(
                "PFIB restore: initial plasma_gas readback raised; "
                "proceeding to command the switch regardless",
                exc_info=True,
            )

        set_error: Optional[str] = None
        try:
            self._ion_beam.plasma_gas = target
        except Exception as exc:
            set_error = _exc_str(exc)
            logger.exception(
                "PFIB restore: setting plasma_gas to %r raised; "
                "polling readback in case the switch completes late",
                target,
            )

        verified = _settle_poll(
            lambda: self._ion_beam.plasma_gas == target,
            poll_interval_s=self._plasma_settle_poll_s,
            deadline_s=self._plasma_settle_deadline_s,
            should_abandon=should_abandon,
            description=f"plasma_gas == {target!r}",
            sleep=self._sleep,
        )
        if verified:
            if set_error is not None:
                logger.info(
                    "PFIB restore: plasma_gas reached %r after the set "
                    "raised — recovered (operation completed after the "
                    "server stopped waiting)", target,
                )
            return True
        if self._abandoned(should_abandon, failures,
                           "during ion species settle wait"):
            return False
        failures.append(RestoreFailure(
            field="ion species",
            error=set_error if set_error is not None else (
                f"readback did not reach {target!r} within "
                f"{self._plasma_settle_deadline_s:.0f} s"
            ),
        ))
        return False

    def _restore_beam_on_off(
        self,
        want_on: bool,
        should_abandon: Callable[[], bool],
        species_verified: bool,
        failures: List[RestoreFailure],
    ) -> None:
        """Restore the beam's on/off state; verify + one gated retry for on.

        ``turn_off`` is a single attempt (it is fast and doesn't
        strike the source). ``turn_on`` gets the
        full treatment: verify by ``is_on`` readback after an
        exception (or a call that returns with the beam still off),
        then — only if the beam is verified genuinely off AND the
        species readback verified at its target — a single retry, the
        operator-equivalent of clicking Beam On once more after a
        server wait timeout. Hard cap: two ``turn_on`` calls per
        restore, structurally guaranteed below.
        """
        try:
            currently_on = self._ion_beam.is_on
        except Exception as exc:
            logger.exception("PFIB restore: reading is_on raised")
            failures.append(RestoreFailure(
                field="beam on/off", error=_exc_str(exc),
            ))
            return

        if want_on == currently_on:
            return

        if not want_on:
            try:
                self._ion_beam.turn_off()
            except Exception as exc:
                logger.exception("PFIB restore: failed to turn beam off")
                failures.append(RestoreFailure(
                    field="beam on/off", error=_exc_str(exc),
                ))
            return

        # --- Turn on, verify, at most one gated retry -------------------
        last_error = self._attempt_turn_on(attempt=1)
        if _settle_poll(
            lambda: self._ion_beam.is_on,
            poll_interval_s=self._beam_on_settle_poll_s,
            deadline_s=self._beam_on_settle_deadline_s,
            should_abandon=should_abandon,
            description="is_on == True",
            sleep=self._sleep,
        ):
            if last_error is not None:
                logger.info(
                    "PFIB restore: beam came on after turn_on raised — "
                    "recovered (operation completed after the server "
                    "stopped waiting)"
                )
            return
        if self._abandoned(should_abandon, failures,
                           "during beam-on wait"):
            return

        # A False poll return can mean "observed off" OR "every read
        # raised for the whole window". The retry must only ever fire
        # on a GENUINE off observation, so re-check with one explicit
        # read. A read that raises leaves the beam state unknown — no
        # retry; a read that returns True means the beam actually came
        # on and the restore recovered after all.
        try:
            if self._ion_beam.is_on:
                logger.info(
                    "PFIB restore: beam reads on after the settle "
                    "window — recovered"
                )
                return
        except Exception as exc:
            logger.warning(
                "PFIB restore: beam state unreadable after the settle "
                "window; suppressing the turn_on retry", exc_info=True,
            )
            failures.append(RestoreFailure(
                field="beam on/off",
                error=last_error if last_error is not None else _exc_str(exc),
            ))
            return

        # Beam verified genuinely off after the full settle window.
        if not species_verified:
            logger.warning(
                "PFIB restore: beam is off and the species readback "
                "never verified; suppressing the turn_on retry (won't "
                "strike a source that may be mid-transition)"
            )
            failures.append(RestoreFailure(
                field="beam on/off",
                error=last_error if last_error is not None else (
                    f"beam did not turn on within "
                    f"{self._beam_on_settle_deadline_s:.0f} s "
                    "(retry suppressed: ion species unverified)"
                ),
            ))
            return

        logger.warning(
            "PFIB restore: beam verified off after the settle window; "
            "retrying turn_on once"
        )
        retry_error = self._attempt_turn_on(attempt=2)
        if retry_error is not None:
            last_error = retry_error
        if _settle_poll(
            lambda: self._ion_beam.is_on,
            poll_interval_s=self._beam_on_settle_poll_s,
            deadline_s=self._beam_on_retry_deadline_s,
            should_abandon=should_abandon,
            description="is_on == True (after retry)",
            sleep=self._sleep,
        ):
            logger.info("PFIB restore: beam on after retry")
            return
        if self._abandoned(should_abandon, failures,
                           "during beam-on retry wait"):
            return
        failures.append(RestoreFailure(
            field="beam on/off",
            error=last_error if last_error is not None else (
                f"beam did not turn on within "
                f"{self._beam_on_settle_deadline_s:.0f} s, nor within "
                f"{self._beam_on_retry_deadline_s:.0f} s after one retry"
            ),
        ))

    def _attempt_turn_on(self, *, attempt: int) -> Optional[str]:
        """Call ``turn_on``; return its error string if it raised."""
        try:
            self._ion_beam.turn_on()
            return None
        except Exception as exc:
            logger.exception(
                "PFIB restore: turn_on raised (attempt %d); polling "
                "is_on in case the beam comes on late", attempt,
            )
            return _exc_str(exc)
