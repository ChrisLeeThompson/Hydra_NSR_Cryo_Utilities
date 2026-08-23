"""Pre-start check orchestration.

Pure functions that walk a list of :class:`PreStartCheck`
instances, deduplicate them by type, evaluate each unique check
against a shared :class:`HardwareSnapshot`, and categorize the
results into a :class:`PreStartCheckSummary`.

No Qt, no microscope, no I/O — testable end-to-end with
constructed snapshots and mock check classes.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field, replace
from typing import Dict, FrozenSet, Iterable, List, Set, Type

from .base import (
    PreStartCheck,
    PreStartCheckOutcome,
    PreStartCheckResult,
)
from .snapshot import HardwareSnapshot

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PreStartCheckSummary:
    """Categorized result of one orchestrator pass.

    Only non-PASS results are surfaced — PASS results carry no
    user-facing copy and don't influence the start decision.

    Attributes:
        refused: All ``REFUSE`` results from this pass, in
            evaluation order. Non-empty means the start is
            refused outright; any ``needs_confirmation`` results
            in the same pass are not surfaced.
        needs_confirmation: All ``ASK_CONFIRM`` results from
            this pass. Non-empty (and ``refused`` empty) means
            the start is paused pending the confirm dialog.
        needs_confirmation_types: The check classes that produced
            the :attr:`needs_confirmation` entries. Consumers carry
            this set forward on the accept path so a later
            mid-run re-check of the same types proceeds silently.
            A :class:`FrozenSet` because callers only need
            membership tests.
    """
    refused: List[PreStartCheckResult] = field(default_factory=list)
    needs_confirmation: List[PreStartCheckResult] = field(default_factory=list)
    needs_confirmation_types: FrozenSet[Type[PreStartCheck]] = frozenset()


@dataclass(frozen=True)
class PendingStart:
    """Resume state for the two-step Start of an ASK_CONFIRM gate.

    Populated by a consumer (workflow runner or direct gesture
    controller) when :func:`run_pre_start_checks` returns a
    summary whose ``needs_confirmation`` list is non-empty; held
    on the consumer while the user's response to the confirm
    dialog is awaited; consumed (and cleared) on either branch
    of the consumer's ``respondToConfirmation`` slot.

    Frozen so the resume path can't accidentally mutate the
    captured state mid-flight. Shared by the workflow runner, the
    stage scan controller, and the stage positions controller.

    Attributes:
        confirmed_check_types: Set of check types the user is
            being asked to confirm — typically populated from
            :attr:`PreStartCheckSummary.needs_confirmation_types`.
            On the accept path, consumers carry this set forward
            into their ``_confirmed_check_types`` field so any
            subsequent mid-run re-check of the same types
            proceeds silently rather than reprompting.
    """
    confirmed_check_types: FrozenSet[Type[PreStartCheck]] = frozenset()


def run_pre_start_checks(
    checks: Iterable[PreStartCheck],
    snapshot: HardwareSnapshot,
) -> PreStartCheckSummary:
    """Evaluate a collection of checks against a snapshot.

    Deduplicates by ``type(check)``: the first instance of any
    given check class is evaluated; subsequent instances of the
    same class are silently dropped, so several activities can
    contribute the same check without double evaluation.

    Sets each result's ``check_type`` field so callers can
    correlate results back to their originating type.

    Args:
        checks: Iterable of :class:`PreStartCheck` instances to
            evaluate. Order matters only for the order results
            appear in the summary lists.
        snapshot: Hardware snapshot shared across all checks in
            this pass.

    Returns:
        A :class:`PreStartCheckSummary` with the REFUSE results,
        ASK_CONFIRM results, and the set of check types that
        produced ASK_CONFIRM. PASS results are silently consumed.
    """
    seen: Set[Type[PreStartCheck]] = set()
    refused: List[PreStartCheckResult] = []
    needs_confirmation: List[PreStartCheckResult] = []
    needs_confirmation_types: Set[Type[PreStartCheck]] = set()
    for check in checks:
        check_type = type(check)
        if check_type in seen:
            continue
        seen.add(check_type)
        result = check.evaluate(snapshot)
        # Patch in the check_type back-reference. The check itself
        # doesn't set this — it's the orchestrator's job, so callers
        # can correlate any result back to its originating type
        # without the check writer needing to remember to do it.
        result = replace(result, check_type=check_type)
        if result.outcome is PreStartCheckOutcome.REFUSE:
            refused.append(result)
        elif result.outcome is PreStartCheckOutcome.ASK_CONFIRM:
            needs_confirmation.append(result)
            needs_confirmation_types.add(check_type)
        # PASS is silently consumed.
    return PreStartCheckSummary(
        refused=refused,
        needs_confirmation=needs_confirmation,
        needs_confirmation_types=frozenset(needs_confirmation_types),
    )


def to_dialog_items(
    results: Iterable[PreStartCheckResult],
) -> List[Dict[str, str]]:
    """Convert check results to the dialog's structured payload.

    Consumers build the payload passed in the
    ``preStartCheckRefused`` / ``preStartCheckNeedsConfirmation``
    signals from this; QML assigns it to
    ``ConfirmDialog.checkItems``. The output is data, not markup —
    QML owns every presentation decision.

    Items with both ``title`` and ``message`` empty are skipped
    (PASS results shouldn't reach this function in practice).
    Either field may be empty on its own.

    Args:
        results: The results to convert. Typically the
            ``refused`` or ``needs_confirmation`` list from a
            :class:`PreStartCheckSummary`.

    Returns:
        A list of ``{"title": ..., "message": ...}`` dicts with
        plain-string values, in input order. Empty list if no
        items contribute copy.
    """
    items: List[Dict[str, str]] = []
    for result in results:
        if not result.title and not result.message:
            continue
        items.append({"title": result.title, "message": result.message})
    return items