"""Concrete pre-start check implementations.

Each check is a thin :class:`PreStartCheck` subclass — parameterless
from the activity's perspective. Checks are pure functions of a
:class:`HardwareSnapshot`; see
:mod:`hydra_nsr_cu.pre_start_checks.snapshot`.

Two checks today:

* :class:`ZLinkedToFreeWorkingDistanceCheck` — hard precondition
  (REFUSE on failure). Required by GIS Deposition.
* :class:`StagePositionWithinSafeRangeCheck` — soft warning
  (ASK_CONFIRM on failure). Required by GIS Deposition, Sputter
  Coat, Home Stage, and the direct Stage Rotation gesture.
"""
from __future__ import annotations

import math

from .. import defaults
from .base import (
    PreStartCheck,
    PreStartCheckOutcome,
    PreStartCheckResult,
)
from .snapshot import HardwareSnapshot


def is_within_safe_range(stage_x_m: float, stage_y_m: float) -> bool:
    """Return True if the stage is within the safe radial range.

    The single definition of "within safe range": the stage's radial
    distance from chamber center, ``sqrt(x² + y²)``, compared against
    ``defaults.STAGE_SAFE_RADIAL_RANGE_M``. The endpoint is inclusive — a
    position sitting exactly at the limit is within range (see the
    boundary-semantics note on :class:`StagePositionWithinSafeRangeCheck`).

    Shared by :class:`StagePositionWithinSafeRangeCheck` and the
    Stage / Scan Z-slider safety gate so both speak of the same range:
    change the radius in one place
    (:data:`hydra_nsr_cu.defaults.STAGE_SAFE_RADIAL_RANGE_M`) and both
    follow. Tilt and Z are deliberately excluded — only XY radial position.
    """
    radial_m = math.sqrt(stage_x_m ** 2 + stage_y_m ** 2)
    return radial_m <= defaults.STAGE_SAFE_RADIAL_RANGE_M


def describe_out_of_range(stage_x_m: float, stage_y_m: float) -> str:
    """Human-readable "why" for an out-of-range stage position.

    The single source of the out-of-range wording, shared by
    :class:`StagePositionWithinSafeRangeCheck` (the ASK_CONFIRM dialog
    shown for Stage Rotation, Move To, and the gated activities) and the
    Stage / Scan Z-slider lock overlay (via
    ``StageScanController.zSafetyMessage``). Both surfaces report the same
    live radial distance and the same limit, so the two safety gates read
    identically and can never drift apart.

    Reports the current radial distance from chamber center and the safe
    limit, both in millimetres. Assumes the caller has already decided the
    position is out of range (via :func:`is_within_safe_range`); the text
    reads as a violation message.
    """
    radial_mm = math.sqrt(stage_x_m ** 2 + stage_y_m ** 2) * 1e3
    limit_mm = defaults.STAGE_SAFE_RADIAL_RANGE_M * 1e3
    return (
        f"Stage is {radial_mm:.1f} mm from center (0, 0).\n"
        f"Safe range: within {limit_mm:.0f} mm."
    )


class ZLinkedToFreeWorkingDistanceCheck(PreStartCheck):
    """Verify that stage Z is linked to free working distance.

    Required for GIS Deposition: the deposition position's Z value
    is meaningful only when linked, and inserting the GIS needle
    against an incorrect Z risks collision or a no-deposition
    no-op. Hard precondition — returns REFUSE on failure rather
    than asking for confirmation, since there is no scenario in
    which proceeding without a link produces a correct result.

    The user resolves a REFUSE by linking Z in the Microscope
    Control application (xT) and clicking Start again. The
    codebase deliberately does not auto-link from a "Fix it"
    button — see the pre-start check design notes for the
    rationale (linking against an incorrect FWD masks the
    verification step that linking is meant to embody).
    """

    def evaluate(self, snapshot: HardwareSnapshot) -> PreStartCheckResult:
        if snapshot.stage_is_linked:
            return PreStartCheckResult(outcome=PreStartCheckOutcome.PASS)
        return PreStartCheckResult(
            outcome=PreStartCheckOutcome.REFUSE,
            title="Z not linked to free working distance\n",
            message=(
                "GIS Deposition requires the stage Z is linked to "
                "the free working distance. Link Z FWD in the Microscope "
                "Control application and try again."
            ),
        )


class StagePositionWithinSafeRangeCheck(PreStartCheck):
    """Warn when the stage is in an operationally unusual position.

    Triggers ASK_CONFIRM (not REFUSE) — the check exists to
    surface possibly-unintended setups, not to block legitimate
    work. The user has final say.

    The threshold is defined in :mod:`hydra_nsr_cu.defaults`:

    * The stage's radial distance from chamber center
      (``sqrt(x² + y²)``) must be within
      ``STAGE_SAFE_RADIAL_RANGE_M``.

    The violation message reports the current radial distance
    and the safe range, so the user can decide whether the
    position was deliberate (e.g., a custom liftout geometry)
    or an accident (e.g., still set up from yesterday's
    experiment).

    Tilt is intentionally not part of this check: legitimate
    operating positions (such as the GIS deposition default at
    60°) routinely exceed any conservative tilt threshold, and
    the radial check catches the operationally unusual positions
    where tilt would correlate with concern. If a future need
    for a tilt-aware check appears, ``snapshot.stage_t_rad``
    is still available.

    Boundary semantics
    ------------------
    The comparison is inclusive ``<=`` (see
    :func:`is_within_safe_range`). A position sitting exactly
    at the limit (radial = 8 mm with an 8 mm range) passes — the
    safe range is inclusive of its endpoint. This is consistent
    with how the user reads the threshold in
    :mod:`hydra_nsr_cu.defaults` ("8 mm" includes the endpoint)
    and avoids floating-point misery at the boundary.
    """

    def evaluate(self, snapshot: HardwareSnapshot) -> PreStartCheckResult:
        if is_within_safe_range(snapshot.stage_x_m, snapshot.stage_y_m):
            return PreStartCheckResult(outcome=PreStartCheckOutcome.PASS)

        # The pass/fail decision lives in is_within_safe_range and the
        # message wording in describe_out_of_range — both shared with the
        # Stage / Scan Z-slider gate so the two surfaces read identically.
        return PreStartCheckResult(
            outcome=PreStartCheckOutcome.ASK_CONFIRM,
            title="Stage position outside safe range\n",
            message=describe_out_of_range(
                snapshot.stage_x_m, snapshot.stage_y_m
            ),
        )