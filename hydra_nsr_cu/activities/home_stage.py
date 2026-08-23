"""Home Stage activity.

Procedure:
    1. Home all stage axes via ``microscope.specimen.stage.home()``.
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

    * **before** calling ``home()`` (so a stop click before homing
      starts is honored immediately), and
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

from ..microscope.stage_ops import StageOpsLike
from ..pre_start_checks import PreStartCheck
from ..pre_start_checks.checks import StagePositionWithinSafeRangeCheck
from .base import (
    ActivityResult,
    ActivityService,
    ProgressCallback,
    StatusCallback,
    report_indeterminate,
)

logger = logging.getLogger(__name__)


class HomeStageService(ActivityService):
    """Home all stage axes."""

    activity_id = "home_stage"

    def __init__(
        self,
        stage: StageOpsLike,
    ) -> None:
        super().__init__()
        self._stage = stage

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

        # --- Stop check before the uninterruptible home() call ---
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
        """Provenance summary — Home Stage has no parameters.

        Homing takes no configuration (stage restore is a
        workflow-level concern), so an empty summary is correct.
        """
        return {}