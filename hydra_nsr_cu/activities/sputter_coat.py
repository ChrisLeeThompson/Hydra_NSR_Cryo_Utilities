"""Sputter Coat activity service.

Hardware-side implementation of the Sputter Coat activity used in
Cryo Prep workflows on **Hydra NSR** systems. The NSR sputter coater is
not a dedicated MicroSputter device — instead, NSR sputters by applying
a saved FEI pattern file (``.ptf``) through the ion-beam patterning
subsystem, with the coater exposed as a GIS port (the *µCoater*).

Sequence
--------

1. Validate the configured pattern file (exists, is a file, ``.ptf``).
2. Resolve the coater GIS port (looked up by name from settings).
3. Verify Z is linked to the free working distance.
4. Tilt the stage to zero, then move to the saved sputter-coat position.
5. Set ion species (plasma gas), turn on the ion beam, set high voltage
   and beam current.
6. Insert the coater GIS needle. Always retracted before the activity
   returns (stop, completion, exception).
7. Set the ion-beam HFW.
8. Parse the ``.ptf`` into polygon vertices, pitch, and application file.
9. Clear existing patterns, create the polygon pattern, configure it
   (pitch, application file, ion beam type), zero the working distance,
   set the patterning default application file.
10. Run the pattern for the configured duration (interruptible
    per-second loop). Patterning is always stopped and patterns cleared
    before the activity returns.
11. Retract the coater GIS.
12. Per-second interruptible chamber recovery.

Cancellation and cleanup
------------------------

Mirrors :class:`GISDepositionService`'s nested-try/finally model. The
outer try owns the GIS needle (insert/retract); an inner try owns the
patterning job (start → stop + clear). A stop or exception at any point
after insert unwinds both: patterning is stopped and cleared, then the
needle is retracted. Stop checks bracket every uninterruptible step.

PFIB conditions
---------------

Sputter Coat mutates plasma gas, high voltage, beam current, HFW, and
beam-on state. Capture and restore of the beam-electrical / ion-species
state is handled at the workflow level (see
:class:`CPWorkflow._on_commit_to_run` and the PFIBConditionsRecorder),
gated on the two "Restore PFIB ..." settings. The activity itself does
not undo its mutations: in a multi-coat sequence, restoring between
consecutive Sputter Coats would revert state we're about to mutate
again.

Position resolution
-------------------

Like GIS Deposition, the activity receives target coordinates already
resolved to a :class:`StagePosition` object (plus the position name for
status messages); the workflow runner does the position-id → coordinate
resolution at Start, so a deleted position is caught before any work.
"""
from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import Any, Dict, List

from .. import defaults
from ..microscope.gis_ops import GISOpsLike
from ..microscope.ion_beam_ops import IonBeamOpsLike, resolve_plasma_gas_enum
from ..microscope.patterning_ops import PatterningOpsLike
from ..microscope.stage_ops import StageOpsLike
from ..pre_start_checks import PreStartCheck
from ..pre_start_checks.checks import (
    StagePositionWithinSafeRangeCheck,
    ZLinkedToFreeWorkingDistanceCheck,
)
from .base import (
    ActivityResult,
    ActivityService,
    ProgressCallback,
    StatusCallback,
    report_activity_exception,
    report_indeterminate,
)
from .pattern_file import (
    PatternFileError,
    parse_pattern_file,
    validate_pattern_file,
)

logger = logging.getLogger(__name__)


# Polygon depth (metres) handed to create_polygon. Intentionally large —
# the pattern would take a very long time to reach this depth, so the
# sputter is bounded by the configured duration (we stop patterning when
# the timer expires), not by the pattern completing. Ported from v2's
# create_polygon(vertices, 1000e-6).
_PATTERN_DEPTH_M = 1000e-6


def _format_ion_current(value_a: float) -> str:
    """Format an ion beam current value for user-facing display.

    * ``value >= 1 µA``            → ``"0.12 µA"`` etc., two decimal places.
    * ``1 nA <= value <  1 µA``    → ``"70.0 nA"`` etc., one decimal place.
    * ``value < 1 nA``             → ``"1.00e-10 A"`` (scientific) as a
                                     defensive fallback.

    Used in the user-facing status emit just before the activity sets
    the beam current. Log lines and JSONL records continue to use the
    raw amperes value for precision.
    """
    if value_a >= 1e-6:
        return f"{value_a * 1e6:.2f} µA"
    if value_a >= 1e-9:
        return f"{value_a * 1e9:.1f} nA"
    return f"{value_a:.2e} A"


class SputterCoatService(ActivityService):
    """Activity that sputter-coats at a saved position on a Hydra NSR system."""

    activity_id = "sputter_coat"

    def __init__(
        self,
        ion_beam_ops: IonBeamOpsLike,
        gis_ops: GISOpsLike,
        patterning_ops: PatterningOpsLike,
        stage_ops: StageOpsLike,
        gis_port_name: str,
        target_position: Any,
        position_name: str,
        ion_species_index: int,
        ion_current_a: float,
        hfw_um: int,
        pattern_path: Path,
        duration_s: int,
        chamber_recovery_s: int,
        instance_id: str,
    ) -> None:
        """Construct the activity.

        Per-activity parameters (position, ion species, current,
        duration, recovery) come from the :class:`SputterCoatRecord`;
        the coater ``gis_port_name``, ``hfw_um``, and ``pattern_path``
        come from the workflow settings snapshot. High voltage is fixed
        at :data:`defaults.SPUTTER_HIGH_VOLTAGE_V` (not user-set — see
        that constant). ``target_position`` / ``position_name`` are
        resolved from ``position_id`` against the StagePositionsController
        by the runner, which refuses to start if the resolution fails.
        """
        super().__init__()
        self._ion_beam = ion_beam_ops
        self._gis = gis_ops
        self._patterning = patterning_ops
        self._stage = stage_ops

        self._gis_port_name = str(gis_port_name)
        self._target_position = target_position
        self._position_name = str(position_name)
        self._ion_species_index = int(ion_species_index)
        self._ion_current_a = float(ion_current_a)
        self._hfw_um = int(hfw_um)
        self._pattern_path = Path(pattern_path)
        self._duration_s = int(duration_s)
        self._chamber_recovery_s = int(chamber_recovery_s)
        # Override the class default with this instance's id. Read by
        # the workflow runner for per-instance status routing.
        self.instance_id = str(instance_id)

    def run(
        self,
        stop_event: threading.Event,
        on_progress: ProgressCallback,
        on_status: StatusCallback,
    ) -> ActivityResult:
        log_prefix = f"Sputter Coat [{self.instance_id}]"
        status_prefix = "Sputter coat"

        # ---------------------------------------------------------------
        # Validate parameters and resolve translations.
        # ---------------------------------------------------------------
        if not (0 <= self._ion_species_index
                < len(defaults.PLASMA_GAS_SPECIES_NAMES)):
            logger.error(
                "%s: ion_species_index %d out of range; aborting",
                log_prefix, self._ion_species_index,
            )
            on_status("Sputter coat: invalid ion species selection")
            return ActivityResult.EXCEPTION

        species_name = defaults.PLASMA_GAS_SPECIES_NAMES[self._ion_species_index]
        species_value = resolve_plasma_gas_enum(species_name)

        logger.info(
            "%s: starting (position=%r, port=%r, species=%s, "
            "current=%.3e A, hv=%dkV, hfw=%dµm, pattern=%s, "
            "duration=%ds, recovery=%ds)",
            log_prefix, self._position_name, self._gis_port_name,
            species_name, self._ion_current_a,
            defaults.SPUTTER_HIGH_VOLTAGE_V // 1000,
            self._hfw_um, self._pattern_path.name, self._duration_s,
            self._chamber_recovery_s,
        )

        # ---------------------------------------------------------------
        # Validate the pattern file before touching hardware, so a bad
        # configuration fails before we move the stage.
        # ---------------------------------------------------------------
        try:
            validate_pattern_file(self._pattern_path)
        except PatternFileError as exc:
            logger.error("%s: %s", log_prefix, exc)
            on_status(f"Sputter coat: {exc}")
            return ActivityResult.EXCEPTION

        # ---------------------------------------------------------------
        # Resolve the coater GIS port. A bad name produces an AutoScript
        # exception, which we treat as a fatal error for the activity.
        # ---------------------------------------------------------------
        try:
            port = self._gis.get_port(self._gis_port_name)
        except Exception as exc:
            report_activity_exception(
                on_status, log_prefix, status_prefix, "port lookup", exc,
            )
            return ActivityResult.EXCEPTION

        if stop_event.is_set():
            return ActivityResult.STOP

        # ---------------------------------------------------------------
        # Verify Z is linked. An unlinked Z makes the position's Z value
        # meaningless and risks needle collision. The pre-start check
        # also covers this at Start; this is the in-activity guard
        # mirroring v2's is_linked check (defensive for mid-run adds).
        # ---------------------------------------------------------------
        try:
            is_linked = self._stage.is_linked
        except Exception as exc:
            report_activity_exception(
                on_status, log_prefix, status_prefix, "reading Z-link state", exc,
            )
            return ActivityResult.EXCEPTION
        if not is_linked:
            logger.error("%s: stage Z is not linked; aborting", log_prefix)
            on_status("Sputter coat: stage Z is not linked to free working distance")
            return ActivityResult.EXCEPTION

        # ---------------------------------------------------------------
        # Tilt to zero, then move to the sputter-coat position.
        # ---------------------------------------------------------------
        on_status("Tilting stage to zero...")
        report_indeterminate(on_progress)
        try:
            self._stage.absolute_move(self._stage.make_position(t=0.0))
        except Exception as exc:
            report_activity_exception(
                on_status, log_prefix, status_prefix, "tilt to zero", exc,
            )
            return ActivityResult.EXCEPTION
        if stop_event.is_set():
            return ActivityResult.STOP

        on_status(f"Moving stage to {self._position_name}...")
        report_indeterminate(on_progress)
        try:
            self._stage.absolute_move(self._target_position)
        except Exception as exc:
            report_activity_exception(
                on_status, log_prefix, status_prefix, "stage move", exc,
            )
            return ActivityResult.EXCEPTION
        if stop_event.is_set():
            return ActivityResult.STOP

        # ---------------------------------------------------------------
        # Set ion species, beam on, high voltage, beam current.
        # ---------------------------------------------------------------
        on_status(f"Setting PFIB species to {species_name}...")
        try:
            self._ion_beam.plasma_gas = species_value
        except Exception as exc:
            report_activity_exception(
                on_status, log_prefix, status_prefix,
                "setting ion species", exc,
            )
            return ActivityResult.EXCEPTION
        if stop_event.is_set():
            return ActivityResult.STOP

        on_status("Turning on ion beam...")
        try:
            self._ion_beam.turn_on()
        except Exception as exc:
            report_activity_exception(
                on_status, log_prefix, status_prefix,
                "turning on ion beam", exc,
            )
            return ActivityResult.EXCEPTION
        if stop_event.is_set():
            return ActivityResult.STOP

        on_status(
            f"Setting PFIB high voltage to "
            f"{defaults.SPUTTER_HIGH_VOLTAGE_V // 1000} kV..."
        )
        try:
            self._ion_beam.high_voltage = defaults.SPUTTER_HIGH_VOLTAGE_V
        except Exception as exc:
            report_activity_exception(
                on_status, log_prefix, status_prefix,
                "setting high voltage", exc,
            )
            return ActivityResult.EXCEPTION
        if stop_event.is_set():
            return ActivityResult.STOP

        on_status(f"Setting beam current to {_format_ion_current(self._ion_current_a)}...")
        try:
            self._ion_beam.beam_current = self._ion_current_a
        except Exception as exc:
            report_activity_exception(
                on_status, log_prefix, status_prefix,
                "setting beam current", exc,
            )
            return ActivityResult.EXCEPTION
        if stop_event.is_set():
            return ActivityResult.STOP

        # ---------------------------------------------------------------
        # Insert the coater GIS needle. Nested try/finally guarantees
        # retraction on every exit path — see module docstring.
        # ---------------------------------------------------------------
        on_status(f"Inserting {self._gis_port_name} sputter coater...")
        try:
            port.insert()
        except Exception as exc:
            report_activity_exception(
                on_status, log_prefix, status_prefix,
                "inserting sputter coater", exc,
            )
            return ActivityResult.EXCEPTION

        try:
            return self._run_with_inserted_coater(
                stop_event, on_progress, on_status, log_prefix, status_prefix,
            )
        finally:
            # The retract always runs — stop, completion, exception.
            try:
                on_status(f"Retracting {self._gis_port_name} sputter coater...")
                port.retract()
            except Exception:
                logger.exception(
                    "%s: sputter coater retract failed in cleanup; "
                    "original operation may have already failed",
                    log_prefix,
                )

    def _run_with_inserted_coater(
        self,
        stop_event: threading.Event,
        on_progress: ProgressCallback,
        on_status: StatusCallback,
        log_prefix: str,
        status_prefix: str,
    ) -> ActivityResult:
        """Run the sputter and recovery, with the coater GIS inserted.

        Caller's ``finally`` retracts the needle on every exit path.
        This method's own ``finally`` (around the patterning phase)
        guarantees the patterning job is stopped and patterns cleared.
        """
        # Set ion HFW.
        on_status("Setting PFIB HFW...")
        try:
            self._ion_beam.horizontal_field_width = self._hfw_um * 1e-6
        except Exception as exc:
            report_activity_exception(
                on_status, log_prefix, status_prefix, "setting HFW", exc,
            )
            return ActivityResult.EXCEPTION
        if stop_event.is_set():
            return ActivityResult.STOP

        # Parse the pattern file (validated already in run()).
        on_status("Parsing pattern file...")
        try:
            vertices, pitch_x, pitch_y, application_file = parse_pattern_file(
                self._pattern_path
            )
        except PatternFileError as exc:
            logger.error("%s: %s", log_prefix, exc)
            on_status(f"Sputter coat: {exc}")
            return ActivityResult.EXCEPTION
        if stop_event.is_set():
            return ActivityResult.STOP

        # Build the polygon pattern. Once created, the finally below
        # guarantees the patterning job is stopped and patterns cleared.
        try:
            self._patterning.clear_patterns()
            polygon = self._patterning.create_polygon(vertices, _PATTERN_DEPTH_M)
            polygon.pitch_x = pitch_x
            polygon.pitch_y = pitch_y
            polygon.application_file = application_file
            polygon.beam_type = self._patterning.ion_beam_type
            self._ion_beam.working_distance = 0.0
            self._patterning.application_file = application_file
        except Exception as exc:
            report_activity_exception(
                on_status, log_prefix, status_prefix, "building pattern", exc,
            )
            try:
                self._patterning.clear_patterns()
            except Exception:
                logger.exception(
                    "%s: clear_patterns failed in cleanup", log_prefix,
                )
            return ActivityResult.EXCEPTION

        try:
            return self._pattern_and_recover(
                stop_event, on_progress, on_status, log_prefix, status_prefix,
            )
        finally:
            try:
                self._patterning.stop()
                self._patterning.clear_patterns()
            except Exception:
                logger.exception(
                    "%s: patterning stop/clear failed in cleanup", log_prefix,
                )

    def _pattern_and_recover(
        self,
        stop_event: threading.Event,
        on_progress: ProgressCallback,
        on_status: StatusCallback,
        log_prefix: str,
        status_prefix: str,
    ) -> ActivityResult:
        """Start patterning, run for the duration, then chamber recovery.

        The caller's ``finally`` stops patterning and clears patterns on
        every exit path, so this method just drives the timed loops.
        """
        # Start the patterning job, then run the interruptible
        # per-second loop. Mirrors GIS Deposition's deposition loop.
        on_status("Patterning...")
        try:
            self._patterning.start()
        except Exception as exc:
            report_activity_exception(
                on_status, log_prefix, status_prefix, "starting patterning", exc,
            )
            return ActivityResult.EXCEPTION

        for elapsed in range(self._duration_s):
            if stop_event.is_set():
                logger.info("%s: cancelled during patterning", log_prefix)
                return ActivityResult.STOP
            on_progress(elapsed, self._duration_s)
            on_status(f"Sputter coat: {elapsed}/{self._duration_s} s")
            time.sleep(1)
        on_progress(self._duration_s, self._duration_s)
        on_status(f"Sputter coat: {self._duration_s}/{self._duration_s} s")
        time.sleep(1)

        # Chamber recovery — only reached on a successful sputter. Same
        # loop structure as GIS Deposition's recovery phase.
        if self._chamber_recovery_s > 0:
            if stop_event.is_set():
                return ActivityResult.STOP
            for elapsed in range(self._chamber_recovery_s):
                if stop_event.is_set():
                    logger.info(
                        "%s: cancelled during chamber recovery", log_prefix,
                    )
                    return ActivityResult.STOP
                on_progress(elapsed, self._chamber_recovery_s)
                on_status(
                    f"Chamber recovery: {elapsed}/{self._chamber_recovery_s} s"
                )
                time.sleep(1)
            on_progress(self._chamber_recovery_s, self._chamber_recovery_s)
            on_status(
                f"Chamber recovery: {self._chamber_recovery_s}/{self._chamber_recovery_s} s"
            )
            time.sleep(1)

        on_status(f"Sputter coat at {self._position_name} complete")
        logger.info("%s: complete", log_prefix)
        return ActivityResult.COMPLETE

    @classmethod
    def pre_start_checks(cls) -> List[PreStartCheck]:
        """Sputter Coat requires Z linked to FWD and verifies the stage
        is in a safe position before moving.

        Mirrors GIS Deposition: the Z-link requirement is a hard
        precondition (REFUSE on failure) — the sputter now moves the
        stage to a saved position whose Z is meaningless when unlinked.
        The stage-position check is advisory (ASK_CONFIRM on failure).
        """
        return [
            ZLinkedToFreeWorkingDistanceCheck(),
            StagePositionWithinSafeRangeCheck(),
        ]

    def parameter_summary(self) -> Dict[str, Any]:
        """Provenance summary — labels for enum choices, raw SI for numerics.

        Defensive on the species index lookup: an out-of-range
        ``ion_species_index`` returns a tagged sentinel string rather
        than raising, so a bad parameter that :meth:`run` would catch
        and report as an EXCEPTION result doesn't also crash the log
        capture.

        The coater port name, HFW, and pattern file come from the
        workflow settings snapshot at commit time, so the recorded
        values reflect what the activity actually used. The fixed 12 kV
        high voltage is intentionally excluded — the log records the
        user's choices, not constants applied by the activity.
        """
        species = (
            defaults.PLASMA_GAS_SPECIES_NAMES[self._ion_species_index]
            if 0 <= self._ion_species_index < len(defaults.PLASMA_GAS_SPECIES_NAMES)
            else f"<invalid ion_species_index {self._ion_species_index}>"
        )
        return {
            "position_name": self._position_name,
            "ion_species": species,
            "ion_current_a": self._ion_current_a,
            "hfw_um": self._hfw_um,
            "pattern_file": self._pattern_path.name,
            "duration_s": self._duration_s,
            "chamber_recovery_s": self._chamber_recovery_s,
        }
