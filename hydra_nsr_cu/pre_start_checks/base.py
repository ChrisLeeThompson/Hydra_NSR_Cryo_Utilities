"""Pre-start check primitives.

A :class:`PreStartCheck` examines a :class:`HardwareSnapshot` and
returns a :class:`PreStartCheckResult` describing one of three
outcomes:

* :data:`PreStartCheckOutcome.PASS` — silent green light.
* :data:`PreStartCheckOutcome.REFUSE` — hard veto with a user-facing
  title and message; the start is refused outright.
* :data:`PreStartCheckOutcome.ASK_CONFIRM` — soft warning; surface
  the title and message to the user and let them decide whether to
  proceed.

Concrete checks live in :mod:`hydra_nsr_cu.pre_start_checks.checks`.
Orchestration (running a collection of checks against a snapshot,
deduplicating by type, categorizing results) lives in
:mod:`hydra_nsr_cu.pre_start_checks.orchestrator`.

No Qt, no microscope, no I/O — these are pure pieces that consume an
already-gathered snapshot.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from typing import Optional, Type


class PreStartCheckOutcome(Enum):
    """Outcome of a single :class:`PreStartCheck`.

    Values are short string discriminators; serialization-friendly
    if a future feature wants to log check results as data.
    """
    PASS = "pass"
    REFUSE = "refuse"
    ASK_CONFIRM = "ask_confirm"


class PreStartCheck(ABC):
    """Abstract base for a single pre-start check.

    Stateless by convention — concrete subclasses hold only the
    parameters they need to evaluate (typically none today). All
    hardware data comes in via :meth:`evaluate`'s
    :class:`HardwareSnapshot` argument; the check returns a fresh
    :class:`PreStartCheckResult`.

    Subclasses MUST NOT have side effects in :meth:`evaluate` — the
    orchestrator deduplicates instances by type and only invokes
    the first instance of each class within a pass.
    """

    @abstractmethod
    def evaluate(self, snapshot) -> "PreStartCheckResult":
        """Run the check against ``snapshot`` and return a result.

        Implementations should be cheap and pure — no I/O, no
        microscope contact, no state mutation. All required data
        is in the snapshot.
        """
        raise NotImplementedError


@dataclass(frozen=True)
class PreStartCheckResult:
    """One check's verdict.

    Attributes:
        outcome: One of the :class:`PreStartCheckOutcome` values.
        title: Short user-facing label (e.g. ``"Stage position out
            of range"``). Used as the bold header in the dialog's
            bulleted list. Empty for PASS results.
        message: Longer detail line (e.g. ``"Stage is 9.0 mm from
            center (0, 0). Safe range: within 8 mm."``). Empty for
            PASS results.
        check_type: The :class:`PreStartCheck` subclass that
            produced this result. Populated by the orchestrator
            (not by the check itself) so callers can correlate
            results back to their originating type, e.g. to decide
            whether an ASK_CONFIRM was already approved earlier in
            the run. ``None`` on results constructed outside the
            orchestrator; callers must treat None as "unknown type"
            and therefore "not pre-confirmed."
    """
    outcome: PreStartCheckOutcome
    title: str = ""
    message: str = ""
    check_type: Optional[Type[PreStartCheck]] = None