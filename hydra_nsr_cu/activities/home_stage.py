"""Home Stage activity.

Procedure:
    1. Tilt the stage to 0° under the holder selected at that moment,
       verified by an arrival check (see ``microscope/stage_moves.py``).
       Skipped when no holder was given.
    2. Select the holder the stage homes under (``required_holder``,
       the AutoGrid shuttle holder in the Cryo Prep workflow), verified
       by readback — a preceding GIS Deposition or Sputter Coat may
       have left another holder selected. No-op when already selected;
       skipped when no holder was given.
    3. Home all stage axes via ``microscope.specimen.stage.home()``.
       This is a blocking, uninterruptible AutoScript call; the stage
       is in motion until it returns.

After homing, the stage is left at its homed position. Returning the
stage to where the session began is a *workflow-level* concern driven
by the "Move Stage To Original Position" setting and handled by the
workflow runner's stage-position recorder (see
:class:`hydra_nsr_cu.microscope.recorders.stage_position.StageRecorder`)
— not by this activity.

Stop semantics
--------------
The home() call cannot be interrupted mid-flight — once started, the
activity waits for the stage to finish homing. The activity checks the
stop event:

    * **before** the tilt, the holder selection and the ``home()``
      call (so a stop click before homing starts is honored
      immediately), and
    * **after** ``home()`` returns (so a stop click during the home
      is reported as a STOP result once home completes).

Reporting STOP on the post-home check matters for the restore policy:
a STOP result propagates to the workflow runner as a non-successful
exit, which suppresses the workflow-level stage restore — the desired
behavior, since the user interrupted the run.
"""
from __future__ import annotations

import logging
import threading
from typing import Any, Dict, List

from ..microscope.holder import select_holder
from ..microscope.stage_moves import tilt_to_zero
from ..microscope.stage_ops import StageOpsLike
from ..pre_start_checks import PreStartCheck
from ..pre_start_checks.checks import StagePositionWithinSafeRangeCheck
from .base import (
    ActivityResult,
    ActivityService,
    ProgressCallback,
    StatusCallback,
    report_activity_exception,
    report_indeterminate,
)

logger = logging.getLogger(__name__)


class HomeStageService(ActivityService):
    """Home all stage axes."""

    activity_id = "home_stage"

    def __init__(
        self,
        stage: StageOpsLike,
        required_holder: str = "",
    ) -> None:
        """Construct the activity.

        ``required_holder`` is the xT holder to home under
        (``defaults.HOME_STAGE_HOLDER`` in the Cryo Prep workflow); the
        activity tilts the stage to 0° before selecting it. Empty means
        "home as is": no tilt, no holder change.
        """
        super().__init__()
        self._stage = stage
        self._required_holder = str(required_holder)

    def run(
        self,
        stop_event: threading.Event,
        on_progress: ProgressCallback,
        on_status: StatusCallback,
    ) -> ActivityResult:
        # No timed loop — progress reporting is via on_status only.
        # The bar stays at whatever value it was last set to (the
        # workflow runner's default behavior); we don't artificially
        # animate it during a non-timed activity.

        # --- Stop check before the stage moves ---
        if stop_event.is_set():
            on_status("Home stage: stopped before homing")
            return ActivityResult.STOP

        if self._required_holder:
            # --- Tilt to 0° ---
            # Flat first, under the holder selected now: a preceding
            # GIS Deposition or Sputter Coat may have left the stage at
            # a tilt the holder selected below does not permit. Verified
            # by an arrival check — see microscope/stage_moves.py.
            report_indeterminate(on_progress)
            try:
                tilt_to_zero(self._stage, on_status=on_status)
            except Exception as exc:
                report_activity_exception(
                    on_status, "Home Stage", "Home stage",
                    "tilt to zero", exc,
                )
                return ActivityResult.EXCEPTION
            if stop_event.is_set():
                on_status("Home stage: stopped before homing")
                return ActivityResult.STOP

            # --- Holder ---
            # Home under the activity's holder so a holder a preceding
            # GIS Deposition or Sputter Coat position needed doesn't
            # carry into the homing motion. Selected while flat; verified
            # by readback — see microscope/holder.py. No-op (no status)
            # when it is still selected.
            try:
                select_holder(
                    self._stage, self._required_holder,
                    should_abort=stop_event.is_set,
                    on_status=on_status,
                )
            except Exception as exc:
                report_activity_exception(
                    on_status, "Home Stage", "Home stage",
                    "holder selection", exc,
                )
                return ActivityResult.EXCEPTION
            if stop_event.is_set():
                on_status("Home stage: stopped before homing")
                return ActivityResult.STOP

        # --- Home (blocking, uninterruptible) ---
        #
        # AutoScript's home() doesn't expose progress, so we tell the
        # StatusBar progress bar to go indeterminate (animated stripes)
        # for the duration of the call. The activity container's busy
        # spinner provides a parallel cue at the per-activity level.
        on_status("Home stage: homing all axes...")
        report_indeterminate(on_progress)
        self._stage.home()
        on_status("Home stage: homing complete")
        logger.info("Home stage: home() returned")

        # --- Stop check after home() ---
        # If the user clicked Stop during the (uninterruptible) home,
        # report it now. Returning STOP propagates to the workflow as
        # a non-successful exit, which suppresses the workflow-level
        # stage restore — the right behavior for an interrupted run.
        if stop_event.is_set():
            on_status("Home stage: stopped")
            return ActivityResult.STOP

        on_status("Home stage: complete (left at homed position)")
        logger.info("Home stage: complete")
        return ActivityResult.COMPLETE

    @classmethod
    def pre_start_checks(cls) -> List[PreStartCheck]:
        """Home Stage verifies the stage is in a safe position
        before homing.

        Advisory (ASK_CONFIRM on failure). The homing motion path
        can be longer and less predictable from an unusual
        starting position (steep tilt, far from center), so we
        surface the situation for user confirmation rather than
        blocking.
        """
        return [StagePositionWithinSafeRangeCheck()]

    def parameter_summary(self) -> Dict[str, Any]:
        """Provenance summary — the holder the stage homed under, if any.

        Homing itself takes no configuration (stage restore is a
        workflow-level concern); ``holder`` is empty when the stage
        homed as is.
        """
        return {"holder": self._required_holder}
