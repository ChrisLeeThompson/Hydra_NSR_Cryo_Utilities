"""Activity base class and shared types.

An :class:`ActivityService` represents one procedure (GIS purge, sputter
coat, etc.) parameterized at construction and executed via :meth:`run`.
Activities are intended to be:

* **Single-use.** Each workflow run constructs fresh activity instances
  from current parameter values. This keeps activities stateless beyond
  their constructor arguments and avoids the need to "reset" them.
* **Synchronous.** :meth:`run` blocks until the activity finishes.
  Threading is the workflow runner's concern, not the activity's.
* **Cooperative on stop.** Activities are passed a :class:`threading.Event`
  and check it at safe interruption points — typically inside any timing
  loop they use, between discrete hardware-setup steps, etc. Operations
  that can't be interrupted mid-call (sputter coater, home stage) simply
  check before invoking and complete fully before the workflow stops.

The threading model is documented in detail in :mod:`workflows.runner`.
"""
from __future__ import annotations

import logging
import threading
from abc import ABC, abstractmethod
from enum import Enum
from typing import Any, Callable, Dict, List

from ..pre_start_checks import PreStartCheck
from ..status_text import exception_detail, failure_status

logger = logging.getLogger(__name__)


# Type aliases for the callbacks an activity receives. These are
# concretely just python callables; aliases here let the activity
# signatures stay narrow and self-documenting.
ProgressCallback = Callable[[int, int], None]   # (current_seconds, total_seconds)
# ``on_status(text)`` — short, user-facing status text. The workflow
# runner's callback also accepts an optional second argument,
# ``on_status(text, detail)``, carrying a longer summary for the
# activity's status-icon tooltip; only :func:`report_activity_exception`
# uses it.
StatusCallback = Callable[..., None]

# Sentinel value for indeterminate progress. Used by activities that
# enter an uninterruptible phase whose progress isn't observable from
# our code (e.g. AutoScript stage home(), a stage absolute_move()). The
# ``current`` and ``total`` are both -1 so the QML side can recognize
# this case unambiguously — distinct from the (0, 0) case which is
# silently ignored as a degenerate report (zero-length phase).
_INDETERMINATE_SENTINEL = -1


def report_indeterminate(on_progress: ProgressCallback) -> None:
    """Tell the StatusBar progress bar to display indeterminate stripes.

    Used immediately before invoking an opaque uninterruptible
    operation (homing, sputter run) — gives the user visual feedback
    that something is happening even when we can't report progress.

    The companion call to clear the indeterminate state happens
    naturally when the next ``on_progress(current, total)`` with
    ``total > 0`` arrives, or when the activity completes and the
    next activity (or the workflow's reset timer) takes over.
    """
    on_progress(_INDETERMINATE_SENTINEL, _INDETERMINATE_SENTINEL)


def report_activity_exception(
    on_status: StatusCallback,
    log_prefix: str,
    status_prefix: str,
    phase: str,
    exc: BaseException,
) -> None:
    """Standardized "operation failed" reporting for activity ``except`` blocks.

    Logs the traceback (``"<log_prefix>: <phase> failed"``) and sends a
    short status — ``"<status_prefix>: <phase> failed (see console
    log)"`` — through ``on_status``, with a one-line
    ``"<Type>: <message>"`` summary of ``exc`` as the detail argument
    for the activity's status-icon tooltip.

    Typical use::

        try:
            self._stage.absolute_move(target)
        except Exception as exc:
            report_activity_exception(
                on_status, log_prefix, "GIS deposition", "stage move", exc,
            )
            return ActivityResult.EXCEPTION

    Args:
        on_status: The activity's status callback.
        log_prefix: Log-side prefix, typically including ``instance_id``
            (e.g. ``"GIS Deposition [abc]"``), kept out of the
            user-facing text.
        status_prefix: User-facing prefix (e.g. ``"GIS deposition"``).
        phase: Short label for what was being attempted (e.g.
            ``"stage move"``, ``"setting ion species"``).
        exc: The caught exception.
    """
    logger.exception("%s: %s failed", log_prefix, phase)
    on_status(failure_status(status_prefix, phase), exception_detail(exc))


class ActivityResult(str, Enum):
    """Outcome of a single activity's execution.

    Inherits from ``str`` so the values can be logged, compared, or used
    as model data without unwrapping.
    """

    COMPLETE = "complete"
    """Activity finished its full procedure normally."""

    STOP = "stop"
    """Activity exited early because the user requested stop. The activity
    cleaned up before returning (e.g. closed any valves it had opened)."""

    EXCEPTION = "exception"
    """Activity hit an unrecoverable failure. The activity logs the
    exception with traceback, performs any hardware cleanup it can
    inside an ``except`` block, and returns this value. The workflow
    runner treats it as a stop signal for the rest of the workflow."""

    def __str__(self) -> str:
        return self.value


class ActivityService(ABC):
    """Abstract base for a workflow activity.

    Subclasses encapsulate one activity type (one per concrete subclass).
    Construction binds the activity to its hardware-ops dependencies and
    parameter values; :meth:`run` executes the procedure.

    The :attr:`activity_id` class attribute is a stable, lowercase string
    identifying the activity *type*. It's used as a key in workflow
    status signals so QML can route per-activity-type state changes to
    the right container.

    For workflows that can host multiple instances of the same activity
    type (e.g. Cryo Prep, with multiple Sputter Coats), the
    :attr:`instance_id` instance attribute disambiguates which row a
    status signal targets. RT-style workflows with one instance per
    type leave :attr:`instance_id` empty.
    """

    # Subclasses MUST override this with a stable identifier. A class
    # attribute (rather than a constructor argument) so we can refer to
    # the id without instantiating — useful for the runner's signal
    # plumbing and for QML page wiring.
    activity_id: str = ""

    # Per-instance identifier. Defaults to empty string for activities
    # without per-instance identity. Subclasses that need it (Cryo
    # activities) set it in ``__init__`` from the activity record's
    # instance_id.
    instance_id: str = ""

    def __init__(self) -> None:
        if not self.activity_id:
            raise TypeError(
                f"{type(self).__name__} must define a non-empty class "
                f"attribute 'activity_id'"
            )

    # --- Execution --------------------------------------------------------

    @abstractmethod
    def run(
        self,
        stop_event: threading.Event,
        on_progress: ProgressCallback,
        on_status: StatusCallback,
    ) -> ActivityResult:
        """Execute the activity synchronously.

        Args:
            stop_event: Set by the workflow runner when the user clicks
                Stop. Activities check ``stop_event.is_set()`` at every
                safe interruption point. When set, the activity should
                clean up any partial hardware state (e.g. close open
                valves), emit a final status, and return
                :attr:`ActivityResult.STOP`.
            on_progress: Called periodically with ``(current_s, total_s)``
                so the workflow runner can drive the StatusBar progress
                bar. Activities that don't have a meaningful progress
                value (no timed loop) may simply not call this.
            on_status: Called whenever the activity produces a new
                human-readable status string for the UI / log.

        Returns:
            :attr:`ActivityResult.COMPLETE` if the procedure finished
            normally, :attr:`ActivityResult.STOP` if the user stopped it.
            On unrecoverable failure, log with traceback, emit a status, 
            and return :attr:`ActivityResult.EXCEPTION`.

        Notes:
            Subclasses must not catch and swallow the stop_event check —
            once set, the event remains set for the entire workflow run.
            Returning STOP from one activity propagates the stop to the
            runner, which then skips remaining activities.
        """

    # --- Pre-start check hook ---------------------------------------------
    #
    # Activities declare the pre-start checks they require — verifications
    # of hardware state that must hold before the activity can run (e.g.
    # GIS Deposition requires Z linked to free working distance). The
    # workflow runner gathers checks from all enabled activities at Start
    # time, deduplicates by check type, and runs them through the
    # orchestrator in :mod:`hydra_nsr_cu.pre_start_checks`.
    #
    # @classmethod, not an instance method: workflows walk records (not
    # constructed activity instances) at pre-start gather time, so the
    # check list must be discoverable without building an activity.
    # Promote to an instance method if a future check ever needs
    # per-instance parameters; none do today.
    #
    # Default: empty list. Subclasses override only when they require
    # checks; the default makes the no-checks case explicit (e.g. GIS
    # Purge on the RT page, which deliberately has none).

    @classmethod
    def pre_start_checks(cls) -> List[PreStartCheck]:
        """Pre-start checks required for activities of this type.

        Called by the workflow runner during ``_validate_pre_start``
        (and during the mid-run ``_next_pending_activity`` fetch for
        activities added mid-run) to gather the hardware-state
        preconditions this activity type requires. The orchestrator
        deduplicates across activities and evaluates each unique
        check once against a shared
        :class:`~hydra_nsr_cu.pre_start_checks.snapshot.HardwareSnapshot`.

        Returns:
            Pre-start check instances. Empty list (the default) means
            this activity has no preconditions and can always start.
        """
        return []

    # --- Logging / provenance hook ----------------------------------------
    #
    # Consumed by the SessionLog (see :mod:`hydra_nsr_cu.session_log`).
    # Called generically by the runner — like ``pre_start_checks``
    # above, this is a defaulted extension hook each concrete activity
    # overrides to surface its own parameters. The dict is captured
    # around the activity's run; its values come from constructor args,
    # so for activities built from a ``WorkflowSettingsSnapshot`` the
    # recorded parameters inherit the snapshot's "values as of Start,
    # not as of later" guarantee.

    def parameter_summary(self) -> Dict[str, Any]:
        """Return a JSON-serializable dict of the parameters this activity used.

        Values should be the raw underlying form: SI units for numerics
        (amperes, seconds, ...) and labels — not indices — for
        enum-style choices. The log delegate formats values for display;
        the persisted dict is the canonical fact.

        Identity fields (:attr:`activity_id`, :attr:`instance_id`) are
        NOT included — they live alongside the dict on the log row's
        factual fields. Duplicating them inside the parameter dict would
        mix identity with parameters.

        Defaults to an empty dict, which is the correct answer for an
        activity with no user-facing parameters. Subclasses with
        parameters override to surface them.
        """
        return {}