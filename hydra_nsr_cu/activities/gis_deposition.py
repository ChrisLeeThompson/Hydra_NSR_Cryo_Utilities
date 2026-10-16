"""GIS Deposition activity service.

Hardware-side implementation of the GIS Deposition activity used in
Cryo Prep workflows. Sequence:

1. Resolve the GIS port (looked up by name from settings).
2. Select the xT holder the position needs (``required_holder``: the
   holder the position was saved with, else the Start holder), verified
   by readback — Microscope Control refuses moves to positions outside
   the selected holder's limits. With a staged move the stage first
   tilts to 0° under the holder selected now, so the holder changes
   while the stage is flat.
3. Move the stage to the deposition position: a staged move (tilt to
   0°, move X/Y/Z/R, tilt to the position's tilt) when ``staged`` is
   set, one five-axis move otherwise. Every stage ends with an arrival
   check; a shortfall ends the activity before the needle moves. See
   ``microscope/stage_moves.py``.
4. Insert the GIS needle.
5. Open the valve.
6. Per-second interruptible deposition loop.
7. Close the valve — always, including on stop.
8. Retract the needle — always, including on stop and exception.
9. Per-second interruptible chamber recovery.

The valve and needle cleanup guarantees are implemented with nested
try/finally: the outer block owns the needle (insert/retract), the
inner block owns the valve (open/close). Stop checks happen at every
safe point (around the stage moves, before insert, before valve open,
and between seconds of the timed loops); the retract itself is never
skipped.

The activity receives target coordinates already resolved to a
:class:`StageTarget`, plus the position name for status messages;
the workflow runner resolves the position id before the run starts.
GIS Deposition does not modify ion beam state.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any, Dict, List

from ..microscope.gis_ops import GISOpsLike
from ..microscope.holder import SHORTFALL_HOLDER_HINT, select_holder
from ..microscope.stage_moves import (
    StageMoveShortfall,
    StageTarget,
    staged_move,
    tilt_to_zero,
)
from ..microscope.stage_ops import StageOpsLike
from ..pre_start_checks import PreStartCheck
from ..pre_start_checks.checks import (
    StagePositionWithinSafeRangeCheck,
    ZLinkedToFreeWorkingDistanceCheck
)
from ..status_text import SEE_LOG, exception_detail
from .base import (
    ActivityResult,
    ActivityService,
    ProgressCallback,
    StatusCallback,
    report_activity_exception,
    report_indeterminate,
)

logger = logging.getLogger(__name__)


class GISDepositionService(ActivityService):
    """Activity that performs a single-site GIS deposition."""

    activity_id = "gis_deposition"

    def __init__(
        self,
        gis_ops: GISOpsLike,
        stage_ops: StageOpsLike,
        gis_port_name: str,
        target_position: StageTarget,
        position_name: str,
        duration_s: int,
        chamber_recovery_s: int,
        staged: bool,
        instance_id: str,
        required_holder: str = "",
    ) -> None:
        """Construct the activity.

        Parameters mirror the :class:`GISDepositionRecord` fields,
        with two enrichments done by the workflow runner:

        * ``gis_port_name`` — pulled from
          :attr:`SettingsController.gisGasPortName`.
        * ``target_position`` / ``position_name`` — resolved from
          ``position_id`` against the StagePositionsController. The
          runner refuses to start if the resolution fails, so the
          activity gets a guaranteed-valid position.

        ``staged`` comes from
        :attr:`SettingsController.zeroTiltBeforeStageMoves` (via the
        workflow settings snapshot). When True, the move to
        ``target_position`` is a staged move — see the module
        docstring.

        ``required_holder`` is the xT holder to select before the move
        to the position (the holder the position was saved with, else
        the Start holder); empty means "leave the holder alone". When
        set and ``staged``, the stage tilts to 0° under the holder
        selected now before the holder changes.
        """
        super().__init__()
        self._gis = gis_ops
        self._stage = stage_ops

        self._gis_port_name = str(gis_port_name)
        self._target_position = target_position
        self._position_name = str(position_name)
        self._duration_s = int(duration_s)
        self._chamber_recovery_s = int(chamber_recovery_s)
        self._staged = bool(staged)
        self._required_holder = str(required_holder)
        # Override the class default with this instance's id. Read by
        # the workflow runner for per-instance status routing.
        self.instance_id = str(instance_id)

    def run(
        self,
        stop_event: threading.Event,
        on_progress: ProgressCallback,
        on_status: StatusCallback,
    ) -> ActivityResult:
        log_prefix = f"GIS Deposition [{self.instance_id}]"
        # Distinct from log_prefix: omits the instance_id (which the
        # user can't see and doesn't need) and uses the user-facing
        # capitalization. Threaded through report_activity_exception
        # to compose tooltip-ready status messages on failure.
        status_prefix = "GIS deposition"

        logger.info(
            "%s: starting (port=%r, position=%r, duration=%ds, "
            "recovery=%ds)",
            log_prefix,
            self._gis_port_name,
            self._position_name,
            self._duration_s,
            self._chamber_recovery_s,
        )

        # ---------------------------------------------------------------
        # Resolve the GIS port. A bad name produces an AutoScript
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

        report_indeterminate(on_progress)

        # ---------------------------------------------------------------
        # Leave under the holder selected now, arrive under the
        # position's: with a staged move, tilt to 0° first so the holder
        # changes while the stage is flat (the staged move's own tilt
        # stage is then skipped). Verified by an arrival check.
        # ---------------------------------------------------------------
        if self._required_holder and self._staged:
            try:
                tilt_to_zero(self._stage, on_status=on_status)
            except StageMoveShortfall as exc:
                return self._stopped_short(
                    on_status, log_prefix, status_prefix, exc,
                )
            except Exception as exc:
                report_activity_exception(
                    on_status, log_prefix, status_prefix, "tilt to zero", exc,
                )
                return ActivityResult.EXCEPTION
            if stop_event.is_set():
                return ActivityResult.STOP

        # ---------------------------------------------------------------
        # Select the holder the position needs before moving there:
        # Microscope Control refuses positions outside the selected
        # holder's limits. Verified by readback (see
        # microscope/holder.py); a failed or overridden switch ends the
        # activity before the stage moves to the position. No-op when
        # the holder is already selected.
        # ---------------------------------------------------------------
        if self._required_holder:
            try:
                select_holder(
                    self._stage, self._required_holder,
                    should_abort=stop_event.is_set,
                    on_status=on_status,
                )
            except Exception as exc:
                report_activity_exception(
                    on_status, log_prefix, status_prefix,
                    "holder selection", exc,
                )
                return ActivityResult.EXCEPTION
            if stop_event.is_set():
                return ActivityResult.STOP

        # ---------------------------------------------------------------
        # Move the stage to the deposition position: a staged move (tilt
        # to 0°, move X/Y/Z/R, tilt to the position's tilt) when the Zero
        # Tilt Before Stage Moves setting is on, one move otherwise. Each
        # stage is read back; a shortfall (xT clamping the move at the
        # holder limits) ends the activity before the needle moves.
        # AutoScript stage moves are uninterruptible; stop requests are
        # honoured between stages. Indeterminate progress throughout.
        # ---------------------------------------------------------------
        try:
            arrived = staged_move(
                self._stage, self._target_position,
                staged=self._staged, name=self._position_name,
                on_status=on_status, should_stop=stop_event.is_set,
            )
        except StageMoveShortfall as exc:
            return self._stopped_short(
                on_status, log_prefix, status_prefix, exc,
            )
        except Exception as exc:
            report_activity_exception(
                on_status, log_prefix, status_prefix, "stage move", exc,
            )
            return ActivityResult.EXCEPTION

        if not arrived or stop_event.is_set():
            return ActivityResult.STOP

        # ---------------------------------------------------------------
        # Insert needle, open valve, deposit, close valve, retract
        # needle. Nested try/finally guarantees cleanup on every exit
        # path — see module docstring for the unwinding model.
        # ---------------------------------------------------------------
        on_status(f"Inserting {self._gis_port_name} GIS needle...")
        try:
            port.insert()
        except Exception as exc:
            report_activity_exception(
                on_status, log_prefix, status_prefix,
                "inserting GIS needle", exc,
            )
            return ActivityResult.EXCEPTION

        try:
            return self._run_deposition_with_inserted_needle(
                port, stop_event, on_progress, on_status, log_prefix,
                status_prefix,
            )
        finally:
            # The retract always runs — stop, completion, exception.
            try:
                on_status(f"Retracting {self._gis_port_name} GIS needle...")
                port.retract()
            except Exception:
                logger.exception(
                    "%s: GIS retract failed in cleanup; original "
                    "operation may have already failed",
                    log_prefix,
                )

    @staticmethod
    def _stopped_short(
        on_status: StatusCallback,
        log_prefix: str,
        status_prefix: str,
        exc: StageMoveShortfall,
    ) -> ActivityResult:
        """Report a shortfall before the needle moves; called in ``except``."""
        logger.exception("%s: stage move stopped short", log_prefix)
        logger.warning("%s: %s", log_prefix, SHORTFALL_HOLDER_HINT)
        on_status(
            f"{status_prefix}: stage did not reach position {SEE_LOG}",
            exception_detail(exc),
        )
        return ActivityResult.EXCEPTION

    def _run_deposition_with_inserted_needle(
        self,
        port: Any,
        stop_event: threading.Event,
        on_progress: ProgressCallback,
        on_status: StatusCallback,
        log_prefix: str,
        status_prefix: str,
    ) -> ActivityResult:
        """Run the deposition and recovery, with the GIS already inserted.

        Caller's ``finally`` block handles needle retraction on every
        exit path. This method's own ``finally`` handles valve closure.
        """
        on_status(f"Opening {self._gis_port_name} valve...")
        try:
            port.open()
        except Exception as exc:
            report_activity_exception(
                on_status, log_prefix, status_prefix, "opening valve", exc,
            )
            return ActivityResult.EXCEPTION

        try:
            # Deposition loop. Mirrors the GIS Purge pattern:
            # iterate elapsed from 0 to N-1, emit progress+status
            # before each sleep, then after the loop emit the
            # terminal N/N frame and hold for one second so the
            # ProgressBar visibly displays 100% before the next
            # phase begins. Status format is "GIS deposition:
            # {elapsed}/{total} s" so it matches GIS Purge's
            # rolling counter.
            for elapsed in range(self._duration_s):
                if stop_event.is_set():
                    logger.info(
                        "%s: cancelled during deposition", log_prefix,
                    )
                    return ActivityResult.STOP
                on_progress(elapsed, self._duration_s)
                on_status(
                    f"GIS deposition: {elapsed}/{self._duration_s} s"
                )
                time.sleep(1)
            on_progress(self._duration_s, self._duration_s)
            on_status(
                f"GIS deposition: {self._duration_s}/{self._duration_s} s"
            )
            time.sleep(1)
        finally:
            try:
                on_status(f"Closing {self._gis_port_name} valve...")
                port.close()
            except Exception:
                logger.exception(
                    "%s: GIS valve close failed in cleanup", log_prefix,
                )

        # Chamber recovery — only reached on successful deposition.
        # Same loop structure as the deposition phase above; status
        # message is "Chamber recovery: {elapsed}/{total} s" to
        # match GIS Purge's recovery format (the two activities
        # share this conceptual phase, so they should look identical
        # in the StatusBar).
        if self._chamber_recovery_s > 0:
            if stop_event.is_set():
                return ActivityResult.STOP

            for elapsed in range(self._chamber_recovery_s):
                if stop_event.is_set():
                    logger.info(
                        "%s: cancelled during chamber recovery",
                        log_prefix,
                    )
                    return ActivityResult.STOP
                on_progress(elapsed, self._chamber_recovery_s)
                on_status(
                    f"Chamber recovery: {elapsed}/{self._chamber_recovery_s} s"
                )
                time.sleep(1)
            on_progress(
                self._chamber_recovery_s, self._chamber_recovery_s
            )
            on_status(
                f"Chamber recovery: {self._chamber_recovery_s}/{self._chamber_recovery_s} s"
            )
            time.sleep(1)

        on_status(
            f"GIS deposition at {self._position_name} complete"
        )
        logger.info("%s: complete", log_prefix)
        return ActivityResult.COMPLETE
    
    @classmethod
    def pre_start_checks(cls) -> List[PreStartCheck]:
        """GIS Deposition requires Z linked to FWD and verifies the
        stage is in a safe position before moving.

        The Z-link requirement is a hard precondition (REFUSE on
        failure): an unlinked Z makes the deposition position's Z
        value meaningless and risks needle collision. The stage
        position check is advisory (ASK_CONFIRM on failure) —
        operationally unusual positions are surfaced for user
        confirmation, not blocked.
        """
        return [
            ZLinkedToFreeWorkingDistanceCheck(),
            StagePositionWithinSafeRangeCheck(),
        ]

    def parameter_summary(self) -> Dict[str, Any]:
        """Provenance summary — captured from the activity as constructed.

        ``gis_port_name`` comes from the workflow's settings snapshot at
        commit time, so the recorded value reflects what the activity
        actually used — not whatever the user might set later. The
        ``position_name`` is the saved-position name captured at
        construction; if the user renames that saved position after
        the run, the log still records the name as it was at run time,
        which is the lab-notebook-correct behavior. ``position_id`` is
        intentionally not surfaced — the service doesn't carry it, and
        widening the constructor just for logging would violate the
        "activity self-describes from its constructor state" principle.
        """
        return {
            "gis_port_name": self._gis_port_name,
            "position_name": self._position_name,
            "duration_s": self._duration_s,
            "chamber_recovery_s": self._chamber_recovery_s,
            "staged_move": self._staged,
            # The holder selected for the position (its saved holder,
            # else the Start holder); empty = the holder was left alone.
            "holder": self._required_holder,
        }