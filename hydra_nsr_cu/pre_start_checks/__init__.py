"""Pre-start check system.

Activities and direct gestures declare pre-start checks they require:
read-only verifications of hardware state that must hold before
the operation is allowed to proceed. Two non-pass outcome kinds:

* ``REFUSE`` — a precondition the operation cannot run without
  (e.g., GIS deposition requires Z linked to free working
  distance). The orchestrator refuses the start and the user
  must fix the underlying condition before retrying.
* ``ASK_CONFIRM`` — an advisory warning (e.g., stage in an
  unusual position). The orchestrator surfaces the warning in a
  dialog; the user has final say.

Architecture
------------
Checks are pure functions of a :class:`HardwareSnapshot` — a
frozen capture of the hardware state at the moment of evaluation.
The snapshot is gathered once per orchestrator pass; checks never
read hardware directly. This keeps the check classes trivially
testable against constructed snapshot fixtures.

Activities declare their checks via the
:meth:`hydra_nsr_cu.activities.base.ActivityService.pre_start_checks`
classmethod hook. Workflows gather checks from all enabled
activities, run them through :func:`run_pre_start_checks`, and
use the resulting :class:`PreStartCheckSummary` to either
proceed, refuse, or prompt for confirmation.

Direct gestures (e.g.,
:meth:`hydra_nsr_cu.stage_scan.controller.StageScanController.start_rotation`)
follow the same pattern but build their check list directly
rather than going through activities.

See :mod:`.base` for the core types, :mod:`.snapshot` for the
hardware snapshot, :mod:`.orchestrator` for the evaluation
logic, and :mod:`.checks` for concrete check implementations.
"""
from .base import (
    PreStartCheck,
    PreStartCheckOutcome,
    PreStartCheckResult,
)
from .orchestrator import (
    PendingStart,
    PreStartCheckSummary,
    run_pre_start_checks,
    to_dialog_items,
)

from .snapshot import HardwareSnapshot, gather_snapshot

__all__ = [
    "PreStartCheck",
    "PreStartCheckOutcome",
    "PreStartCheckResult",
    "PreStartCheckSummary",
    "PendingStart",
    "HardwareSnapshot",
    "gather_snapshot",
    "run_pre_start_checks",
    "to_dialog_items",
]