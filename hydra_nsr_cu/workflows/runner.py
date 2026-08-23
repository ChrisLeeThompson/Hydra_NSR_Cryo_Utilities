"""Workflow runner base class and worker.

The runner exposes a uniform interface to QML (``start()``, ``stop()``,
``isRunning``, status signals) regardless of which workflow it's running.
Page-specific subclasses add their own parameters and property surface;
the threading machinery lives entirely here.

Subclasses implement two hooks, both called on the GUI thread:

* :meth:`WorkflowRunner._validate_pre_start` — runs inside :meth:`start`
  before any worker is spawned and returns a
  :class:`PreStartValidationResult` (``"ok"``, ``"refused"``, or
  ``"needs_confirmation"``). On ``"needs_confirmation"`` the runner
  holds a :class:`PendingStart` until QML answers via
  :meth:`respondToConfirmation`; ``"ok"`` and an accepted confirmation
  both go through :meth:`_on_commit_to_run` and then spawn the worker.
* :meth:`WorkflowRunner._next_pending_activity` — called once per loop
  iteration (via ``BlockingQueuedConnection`` from the worker). Returns
  the next :class:`ActivityService`, ``None`` to end the run as
  complete, or :meth:`_abort_fetch` to end it as not-complete.

Activities are fetched one at a time rather than snapshotted at start,
so additions and switch toggles made mid-run take effect at the next
fetch. An executed activity is identified by the key
``f"{activity_id}/{instance_id}"`` (``"{activity_id}/"`` for
single-instance activities).
"""
from __future__ import annotations

import logging
import threading
import time
from abc import abstractmethod
from dataclasses import dataclass
from typing import (
    Any, Callable, Dict, FrozenSet, List, Literal, Optional, Set, Tuple, Type, Union,
)

from PySide6.QtCore import (
    Property,
    Qt,
    QObject,
    QThread,
    Signal,
    Slot,
)

from ..activities.base import (
    ActivityResult,
    ActivityService,
    ProgressCallback,
    StatusCallback,
)
from ..microscope import MicroscopeClientLike
from ..status_text import SEE_LOG, exception_detail, shorten
from ..pre_start_checks import (
    PendingStart,
    PreStartCheck,
    PreStartCheckResult,
    gather_snapshot,
    run_pre_start_checks,
    to_dialog_items,
)


logger = logging.getLogger(__name__)


# Generous bounded timeout (ms) for joining the workflow worker thread
# during application shutdown — see :meth:`WorkflowRunner.wait_for_stop`.
# Larger than the connect thread's 1 s wait because, once stop is
# requested, the worker only exits after any in-flight uninterruptible
# AutoScript op returns (home, stage move, GIS, or the tail of a sputter
# run). Bounded so a long/stuck op can't hang application quit forever;
# on timeout we log and proceed with teardown.
_SHUTDOWN_JOIN_TIMEOUT_MS = 5000

# Second, short join granted by :meth:`WorkflowRunner.wait_for_stop`
# AFTER the main join budget expires and the abandon event has been
# set. The after_run settle polls re-check abandon at ~1 s granularity
# (sliced sleeps — see pfib_conditions._settle_poll), so a stuck
# cleanup normally exits well within this grace window.
_ABANDON_GRACE_JOIN_MS = 2000


def build_abandon_predicate(
    stop_event: Optional[threading.Event],
    abandon_event: Optional[threading.Event],
) -> Callable[[], bool]:
    """Build the ``should_abandon`` predicate for after_run cleanup work.

    The raw stop_event can't mean "abandon" — on the stop exit path
    it is already set, and cleanup (e.g. the CP workflow's PFIB
    restore) deliberately still runs. Its state is latched at build
    time (cleanup entry) instead:

    * completed/exception path (stop not yet set): a FIRST Stop press
      during cleanup abandons it — nothing else is running, that's
      clearly the user's intent.
    * stop path (stop already set): cleanup runs; a SECOND Stop press
      sets the runner's abandon event (see :meth:`WorkflowRunner.stop`)
      and abandons it.

    Both events are aliased here rather than read through the runner:
    the runner nulls the attributes on the GUI thread after the worker
    finishes, and the predicate is called from the worker thread.
    """
    stop_already_set = stop_event.is_set() if stop_event is not None else False

    def should_abandon() -> bool:
        if abandon_event is not None and abandon_event.is_set():
            return True
        return (
            stop_event is not None
            and stop_event.is_set()
            and not stop_already_set
        )

    return should_abandon


def _format_duration(seconds: float) -> str:
    """Render an elapsed-time duration for the status bar.

    Used by :meth:`WorkflowRunner._on_finished` to compose the
    "Workflow complete — <duration>" message at the end of a
    successful run.

    Format rules:

    * Seconds are rounded to the nearest whole integer.
    * Sub-minute durations omit the minutes component
      (``"23 s"``, not ``"0 min 23 s"``).
    * Minute-and-above durations use the ``"M min S s"`` form.
    * No hours tier — a 95-minute workflow renders as ``"95 min 5 s"``.

    Rounding is applied before choosing the tier, so ``59.6`` renders
    as ``"1 min 0 s"``.

    Examples:
        >>> _format_duration(0.0)
        '0 s'
        >>> _format_duration(59.6)
        '1 min 0 s'
        >>> _format_duration(323.6)
        '5 min 24 s'
        >>> _format_duration(5400.0)
        '90 min 0 s'
    """
    total_seconds = round(seconds)
    if total_seconds < 60:
        return f"{total_seconds} s"
    minutes, secs = divmod(total_seconds, 60)
    return f"{minutes} min {secs} s"


def _safe_parameter_summary(activity: ActivityService) -> Dict[str, Any]:
    """Call :meth:`ActivityService.parameter_summary` defensively.

    Returns the dict on success, or ``{}`` if the call raises. The
    session log is observational and never load-bearing — a buggy
    ``parameter_summary`` override must not be able to derail a
    workflow. Failures are logged with traceback for forensics; the
    workflow proceeds with an empty params dict for that activity's
    log entry.

    Lives at module scope (alongside :func:`_format_duration`) rather
    than as a method on :class:`_WorkflowWorker` so it can be unit
    tested without spinning up a worker or a thread.
    """
    try:
        return activity.parameter_summary()
    except Exception:
        logger.exception(
            "Workflow: activity.parameter_summary() raised for %r/%r; "
            "logging with empty params",
            activity.activity_id, activity.instance_id,
        )
        return {}


class _FetchAborted:
    """Sentinel type for a fetch-hook abort — see :data:`_FETCH_ABORTED`."""

    __slots__ = ()


# Returned through the fetch bridge when the subclass hook ABORTS the
# run — mid-run validation failure, pre-start-check refusal, or an
# unexpected exception inside the hook — as opposed to ``None``, which
# means "nothing left to run". The worker maps it to
# ``all_complete = False`` so the success-only cleanup (PFIB / stage
# restores) and the "Workflow complete" final status don't fire on an
# aborted run. Subclasses don't touch the sentinel directly — they
# return :meth:`WorkflowRunner._abort_fetch` from their fetch hook.
_FETCH_ABORTED = _FetchAborted()

# What a fetch can produce: the next activity to run, the abort
# sentinel, or None when no activities remain.
FetchOutcome = Union[ActivityService, _FetchAborted, None]


# --- Pre-start validation result types --------------------------------------


@dataclass(frozen=True)
class PreStartValidationResult:
    """Tri-state outcome from a subclass's :meth:`_validate_pre_start`.

    The base class also accepts a plain ``bool`` (``True`` → ``"ok"``,
    ``False`` → ``"refused"``); only the ``"needs_confirmation"``
    outcome requires the dataclass form.

    Attributes:
        outcome: One of ``"ok"``, ``"refused"``, ``"needs_confirmation"``.
        pending_confirmed_check_types: When outcome is
            ``"needs_confirmation"``, the set of check types the user
            is being asked to confirm. Carried forward into
            :attr:`WorkflowRunner._confirmed_check_types` on the
            accept path, so subsequent mid-run re-checks of the same
            types proceed silently rather than reprompting. Empty
            for the other outcomes.
    """
    outcome: Literal["ok", "refused", "needs_confirmation"]
    pending_confirmed_check_types: FrozenSet[Type[PreStartCheck]] = frozenset()


class _WorkflowWorker(QObject):
    """Runs activities sequentially on a worker thread.

    Single-use — each :meth:`WorkflowRunner.start` constructs a fresh
    worker. Holds a :class:`threading.Event` for stop signaling and a
    callable that, when invoked, returns the next activity to run.

    Iterative fetching
    ------------------
    Rather than holding a snapshotted list of activities, the worker
    asks the runner for the next pending activity at each iteration.
    The runner forwards to the subclass's
    :meth:`WorkflowRunner._next_pending_activity`, which runs on the
    GUI thread (the call is dispatched via
    ``Qt.BlockingQueuedConnection`` so the worker blocks until the
    GUI thread services it). This lets the user mutate the activity
    list mid-run — additions are picked up on the next iteration,
    toggles-off make activities invisible to the next fetch.

    Signals fire as state changes; the runner re-emits relevant ones
    to QML. Cross-thread emission is automatically queued by Qt — the
    runner's slots run on the GUI thread.
    """

    # Emitted at the start of each activity. Two args:
    #   activity_id  — type discriminator (e.g. "sputter_coat")
    #   instance_id  — per-instance id for activities that can have
    #                  multiple instances; "" for single-instance
    #                  activities (RT Prep style).
    activityStarted = Signal(str, str)

    # Emitted at the end of each activity. Seven args:
    #   activity_id  — same as above.
    #   instance_id  — same as above.
    #   result       — ActivityResult value as string ("complete",
    #                  "stop", "exception").
    #   duration_s   — wall-clock seconds the activity ran for, measured
    #                  with ``time.monotonic()`` immediately around the
    #                  ``activity.run(...)`` call. Reported on every
    #                  outcome (complete / stop / exception).
    #                  ``time.monotonic()`` is used (not ``time.time()``)
    #                  so a clock change during a run doesn't perturb
    #                  the measurement.
    #   last_status  — the most recent short status the activity passed
    #                  to ``on_status`` before returning or raising
    #                  (on the uncaught-raise path, a "<phase> — failed
    #                  (see console log)" line built by the worker).
    #                  Empty string if the activity never emitted a
    #                  status. Shown on the exception status icon's
    #                  tooltip. The full traceback goes to the log only.
    #   detail       — optional longer summary for the tooltip's second
    #                  line (an exception's "<Type>: <message>"); never
    #                  shown in the status bar. Empty when none.
    #   params       — dict from ``ActivityService.parameter_summary()``,
    #                  captured once before ``activity.run()`` (the
    #                  activity is stateless beyond its constructor, so
    #                  capture-before equals capture-after and yields a
    #                  populated value even when ``run()`` raises).
    #                  Forwarded to :attr:`WorkflowRunner.activityRecorded`
    #                  for SessionLog consumption. ``{}`` if
    #                  ``parameter_summary()`` itself raised — see
    #                  :func:`_safe_parameter_summary`. Declared as
    #                  ``object`` in the Signal signature because Qt's
    #                  meta type system doesn't directly support dict
    #                  shapes.
    activityFinished = Signal(str, str, str, float, str, str, object)

    # Forwarded to the StatusBar progress bar: (current_seconds, total_seconds).
    progressUpdated = Signal(int, int)

    # Forwarded to the StatusBar text: short human-readable status.
    statusUpdated = Signal(str)

    # Emitted once the entire workflow is done. Three args:
    #   all_complete       — True if every activity completed normally;
    #                        False if any was stopped, raised, returned
    #                        an exception result, or the run was
    #                        aborted by the fetch hook. (A None fetch
    #                        means "nothing left to run" and leaves the
    #                        run complete — aborts travel exclusively
    #                        via the _FETCH_ABORTED sentinel.)
    #   total_duration_s   — wall-clock seconds for the entire
    #                        ``run()`` call, including the optional
    #                        ``before_run`` / ``after_run`` hooks and
    #                        the small fetch round-trips between
    #                        activities. This is the user-perceived
    #                        "Start clicked → workflow done" time, so
    #                        it differs slightly from the sum of
    #                        per-activity durations (and is always
    #                        ≥ that sum). Reported on every exit path
    #                        for log forensics; the runner only
    #                        surfaces it in the status bar on the
    #                        success path.
    #   ended_by_exception — True iff the loop ended because an
    #                        activity failed (caught EXCEPTION result
    #                        or uncaught raise). The runner needs
    #                        this to pick the final status-bar branch;
    #                        it cannot be derived from the stop event,
    #                        because a Stop press during the after_run
    #                        cleanup (the abandon gesture) sets the
    #                        stop event on a run that actually ended
    #                        by exception.
    finished = Signal(bool, float, bool)

    def __init__(
        self,
        fetch_next: Callable[[Set[str]], FetchOutcome],
        stop_event: threading.Event,
        before_run: Optional[Callable[[StatusCallback], None]] = None,
        after_run: Optional[Callable[[StatusCallback, bool], None]] = None,
    ) -> None:
        super().__init__()
        # Fetch callback: given the set of executed activity keys,
        # returns the next pending activity, None when the loop
        # should end normally, or the abort sentinel when the run
        # must end as not-complete. The callable handles the
        # GUI-thread dispatch itself (see WorkflowRunner.start for
        # how it's set up).
        self._fetch_next = fetch_next
        self._stop_event = stop_event
        # Optional pre/post hooks that run on the worker thread, before
        # any activity runs and after every activity has completed (or
        # the workflow has stopped/raised). Used by CPWorkflow to wrap
        # the activity sequence in a PFIB conditions capture/restore,
        # and by both workflows for the stage-position restore.
        #
        # ``after_run`` receives a ``completed`` bool — True iff the
        # activity loop ran to natural completion (no stop, no
        # exception). Hooks that perform exit-path-dependent work
        # (e.g. restoring the stage only on success) branch on it.
        self._before_run = before_run
        self._after_run = after_run

    @Slot()
    def run(self) -> None:
        """Execute activities until none are pending. Runs on the worker thread.

        The optional ``_before_run`` and ``_after_run`` hooks bracket
        the activity loop. ``_after_run`` runs on every exit path
        (success, stop, exception during loop) so consumers can rely
        on it for cleanup (e.g. PFIB conditions restoration), and it
        receives an ``all_complete`` bool so exit-path-dependent work
        (e.g. the stage-position restore, success-only) can branch on
        the outcome.

        Timing
        ------
        Wall-clock start is captured here (before ``_before_run``) so
        the total duration reported on :attr:`finished` covers the
        full Start-to-finish span the user perceives, including
        before/after hooks and inter-activity fetch round-trips. Each
        activity's own duration is timed independently with a
        ``t0_activity`` captured immediately before ``activity.run``,
        so signal-emission latency from ``activityStarted`` doesn't
        leak into the per-activity number. ``time.monotonic()`` is
        used throughout — never ``time.time()`` — so the measurement
        survives an NTP sync or manual clock change mid-run.
        """
        t0_total = time.monotonic()

        # Pre-run hook — runs once before any activity. An exception
        # here is logged but does not abort the workflow; the after_run
        # hook still fires so any partial state is cleaned up.
        if self._before_run is not None:
            try:
                self._before_run(self.statusUpdated.emit)
            except Exception:
                logger.exception(
                    "Workflow: before_run hook raised; continuing"
                )

        all_complete = True
        ended_by_exception = False

        # Keys of activities that have been executed (or skipped due
        # to validation failure) in this run. Passed to the subclass
        # on each fetch so it can avoid re-issuing already-handled
        # activities. Format: "{activity_id}/{instance_id}".
        executed_keys: Set[str] = set()

        try:
            while True:
                if self._stop_event.is_set():
                    logger.info(
                        "Workflow: stop requested before next fetch"
                    )
                    all_complete = False
                    break

                # Fetch the next pending activity from the GUI thread.
                # Returns None when there's nothing left to run, or
                # the abort sentinel when a subclass aborts (e.g.
                # mid-run validation failure on a layered-deposition
                # workflow, or a pre-start-check refusal).
                activity = self._fetch_next(executed_keys)
                if activity is None:
                    break
                if isinstance(activity, _FetchAborted):
                    # Aborted runs are not complete: the success-only
                    # cleanup (PFIB / stage restores) must not fire,
                    # and the final status must not read "Workflow
                    # complete". The abort site already emitted its
                    # own reason and recorded it for _on_finished.
                    logger.info("Workflow: run aborted by fetch hook")
                    all_complete = False
                    break

                activity_id = activity.activity_id
                instance_id = activity.instance_id
                key = f"{activity_id}/{instance_id}"

                # Re-check stop after the fetch round-trip; the user
                # may have clicked Stop while the GUI thread was
                # building the activity.
                if self._stop_event.is_set():
                    logger.info(
                        "Workflow: stop requested after fetch; "
                        "skipping %r", activity_id,
                    )
                    all_complete = False
                    break

                self.activityStarted.emit(activity_id, instance_id)
                logger.info(
                    "Workflow: starting activity %r (instance=%r)",
                    activity_id, instance_id,
                )

                # Capture the activity's most recent user-facing status
                # text by wrapping the on_status callback. The wrapped
                # callback still forwards to ``self.statusUpdated.emit``
                # (so the StatusBar gets every update in real time);
                # we additionally retain the latest text in
                # ``last_status`` so the runner can stash it for the
                # post-mortem tooltip on the activity's status icon.
                # Scoped to this single activity — reset on each
                # iteration — so an exception in activity N doesn't
                # surface a stale message from activity N-1.
                #
                # ``last_detail`` is the optional longer summary (an
                # exception's "<Type>: <message>") that goes to the
                # tooltip only, never to the status bar.
                #
                # A list-of-one (rather than ``nonlocal``) keeps the
                # closure simple and explicit about the mutation.
                last_status: List[str] = [""]
                last_detail: List[str] = [""]

                def _on_status(text: str, detail: str = "") -> None:
                    text = shorten(text)
                    last_status[0] = text
                    last_detail[0] = detail
                    self.statusUpdated.emit(text)

                # Capture the activity's parameter summary once for both
                # emit paths (completion and exception). ``parameter_summary``
                # reads constructor state and per ``ActivityService``'s
                # "single-use, stateless beyond constructor" contract,
                # ``run()`` doesn't mutate it — so capturing before is
                # equivalent to capturing after, and means we still have
                # something to log even if ``run()`` raises before it
                # would otherwise be called. Defensive against
                # ``parameter_summary`` itself raising; see the helper.
                params = _safe_parameter_summary(activity)

                # Time the activity tightly. ``t0_activity`` is captured
                # *after* ``activityStarted.emit`` so the queued cross-
                # thread signal latency doesn't get charged against the
                # activity's wall-clock duration. The total-duration
                # counter (``t0_total``) absorbs that latency naturally
                # at the outer scope.
                t0_activity = time.monotonic()
                try:
                    result = activity.run(
                        self._stop_event,
                        self.progressUpdated.emit,
                        _on_status,
                    )
                except Exception as exc:
                    activity_duration = time.monotonic() - t0_activity
                    # Uncaught-raise fallback (activities that catch
                    # their own errors go through
                    # ``report_activity_exception`` instead). The bar
                    # gets a short line built from the last reported
                    # phase; the exception summary goes to the tooltip
                    # via ``last_detail``; the traceback goes to the
                    # log below.
                    if last_status[0]:
                        last_status[0] = shorten(
                            f"{last_status[0].rstrip('.')} — failed {SEE_LOG}"
                        )
                    else:
                        last_status[0] = f"Activity failed {SEE_LOG}"
                    last_detail[0] = exception_detail(exc)
                    logger.exception(
                        "Workflow: activity %r raised after %.2fs; "
                        "stopping workflow",
                        activity_id, activity_duration,
                    )
                    self.activityFinished.emit(
                        activity_id, instance_id,
                        ActivityResult.EXCEPTION.value,
                        activity_duration,
                        last_status[0],
                        last_detail[0],
                        params,
                    )
                    executed_keys.add(key)
                    all_complete = False
                    ended_by_exception = True
                    break

                activity_duration = time.monotonic() - t0_activity
                self.activityFinished.emit(
                    activity_id, instance_id, result.value,
                    activity_duration,
                    last_status[0],
                    last_detail[0],
                    params,
                )
                logger.info(
                    "Workflow: activity %r (instance=%r) finished "
                    "with %s in %.2fs",
                    activity_id, instance_id, result.value,
                    activity_duration,
                )
                executed_keys.add(key)

                # A stop or exception result from one activity ends the
                # workflow. Activities catch their own hardware errors
                # and *return* EXCEPTION (see ActivityResult), so this
                # branch mirrors the uncaught-raise handler above —
                # without it a failed activity would let the remaining
                # activities run and the run would report itself
                # complete (and the success-only restores would fire
                # after a failure).
                if result == ActivityResult.EXCEPTION:
                    all_complete = False
                    ended_by_exception = True
                    break
                if result == ActivityResult.STOP:
                    all_complete = False
                    break
        finally:
            # Post-run hook — runs on every exit path. An exception
            # here is logged but doesn't change the workflow result.
            # ``all_complete`` tells the hook which exit path this is
            # (True only on natural completion) so it can gate
            # exit-path-dependent cleanup such as the stage restore.
            if self._after_run is not None:
                try:
                    self._after_run(self.statusUpdated.emit, all_complete)
                except Exception:
                    logger.exception(
                        "Workflow: after_run hook raised; ignoring"
                    )

        # Total wall-clock for the whole run, including before/after
        # hooks. Computed *after* the finally so the after_run hook is
        # included in what the user sees as workflow duration.
        total_duration = time.monotonic() - t0_total
        self.finished.emit(all_complete, total_duration, ended_by_exception)


class _FetchHelper(QObject):
    """GUI-thread bridge for the worker's iterative activity fetch.

    The worker calls :meth:`fetch` from its own thread; that emits
    :attr:`fetchRequested`, which is connected to :meth:`_on_fetch`
    via ``Qt.BlockingQueuedConnection``. Qt's event loop services the
    call on the GUI thread, the runner's
    :meth:`WorkflowRunner._next_pending_activity` runs there, and
    the result is stashed in :attr:`_result` before the signal call
    returns. The worker thread reads :attr:`_result` after its
    blocking emit returns.

    This pattern avoids ``QMetaObject.invokeMethod(...,
    Q_RETURN_ARG(...))``, which in PySide6 is unreliable for Python
    return types that aren't registered with Qt's meta-object system
    (``ActivityService``, ``set``). The signal/slot path is fully
    Pythonic and only relies on ``BlockingQueuedConnection``'s
    documented synchronous behavior.

    Single-use per fetch call: callers don't reuse the helper across
    threads other than the one originally registered. Owned by the
    runner (parented for memory management) and never deleted before
    the runner itself.
    """

    # Internal signal: emitted by the worker, received on the GUI
    # thread. The connection is BlockingQueuedConnection so emit()
    # blocks until the slot returns. The argument is a stand-in —
    # the actual arguments are stored on self before emit() and
    # the result is read from self after emit().
    fetchRequested = Signal()

    def __init__(self, runner: "WorkflowRunner") -> None:
        # Parent to the runner so we share its thread affinity (the
        # GUI thread) and its lifetime.
        super().__init__(runner)
        self._runner = runner
        # Per-call I/O slots. Set by fetch(); read by _on_fetch and
        # then by fetch() again. No re-entrancy: each call to fetch()
        # returns before another can begin (guaranteed by
        # BlockingQueuedConnection's synchronous semantics).
        self._executed_keys: Set[str] = set()
        self._result: FetchOutcome = None
        self.fetchRequested.connect(
            self._on_fetch, Qt.ConnectionType.BlockingQueuedConnection,
        )

    def fetch(
        self, executed_keys: Set[str],
    ) -> FetchOutcome:
        """Worker-thread entry point. Returns when the GUI thread answers."""
        self._executed_keys = executed_keys
        self._result = None
        # Blocks until _on_fetch completes on the GUI thread.
        self.fetchRequested.emit()
        return self._result

    @Slot()
    def _on_fetch(self) -> None:
        """GUI-thread side of the fetch. Runs the subclass hook."""
        try:
            self._result = self._runner._next_pending_activity(
                self._executed_keys
            )
        except Exception:
            # If the subclass raises, we surface the abort sentinel to
            # the worker (which ends the loop with all_complete=False)
            # and log here. Subclass exceptions inside
            # _next_pending_activity are unexpected — they suggest a
            # programming error rather than user data invalidity
            # (which aborts cleanly via _abort_fetch after emitting a
            # validationFailed signal). Route through _abort_fetch
            # like every other abort site so the run's final status
            # names the abort instead of falling through to the
            # misleading "Workflow stopped" (we run on the GUI
            # thread, so recording the reason is safe here too).
            logger.exception(
                "Workflow: _next_pending_activity raised; ending workflow"
            )
            self._result = self._runner._abort_fetch(
                "Workflow aborted: internal error"
            )


class WorkflowRunner(QObject):
    """Base class for workflow runners exposed to QML.

    Subclasses add their own parameter properties and override
    :meth:`_next_pending_activity` to construct their specific
    activities. Threading and signal plumbing live entirely in this
    base class, along with the two-step Start state machine and
    the pre-start check signals consumed by QML dialogs in
    ``main.qml``.
    """

    # Lifecycle notify signal — covers both "started" and "finished".
    isRunningChanged = Signal()

    # Per-activity status. Three args:
    #   activity_id  — type discriminator. RT-style pages route on this.
    #   instance_id  — per-instance id. Cryo-style pages route on this.
    #                  Empty string for single-instance activities.
    #   status       — one of "running", "complete", "stop",
    #                  "exception". Matches ActivityContainer.activityState
    #                  values.
    activityStatusChanged = Signal(str, str, str)

    # Per-activity record event — consumed by SessionLog. Fires on
    # every outcome path (complete / stop / exception) from
    # :meth:`_on_activity_finished`, after the runner's internal
    # duration / last-status bookkeeping has run — so the invokable
    # accessors (:meth:`activityDuration`, :meth:`lastStatusMessage`)
    # are already populated by the time a consumer of this signal
    # queries them.
    #
    # Distinct from :attr:`activityStatusChanged` above: that one
    # drives the UI (narrow, three args, per-row status flip); this
    # one drives the log (wide, full per-activity record). Same
    # event, two consumer audiences. Fires *after*
    # ``activityStatusChanged`` so any pre-existing UI consumer wins
    # the microsecond race and a log handler's view of the world
    # matches what the user just saw.
    #
    # Args:
    #   activity_id  — type discriminator
    #   instance_id  — per-instance id ("" for RT-style)
    #   result       — "complete" | "stop" | "exception"
    #   duration_s   — wall-clock seconds the activity ran for
    #   last_status  — last user-facing status text the activity emitted
    #   params       — dict from ActivityService.parameter_summary(),
    #                  captured before activity.run() so it's
    #                  populated even on the exception path
    activityRecorded = Signal(str, str, str, float, str, object)

    # StatusBar plumbing — passed straight through from the worker.
    progressUpdated = Signal(int, int)
    statusUpdated = Signal(str)

    # Workflow lifecycle bookends — useful for AppController to track
    # which workflows are running across the whole app.
    workflowStarted = Signal()
    workflowFinished = Signal(bool)

    # --- Pre-start check signals (consumed by main.qml's two dialogs) -----
    #
    # Emitted by the subclass's :meth:`_validate_pre_start` (or its
    # mid-run analogue inside :meth:`_next_pending_activity`) when a
    # pre-start check produces a non-PASS outcome. The runner does
    # not emit these itself — they're a contract between the subclass
    # and QML, with the runner providing the signal definitions so
    # all workflow runners share the same QML wiring.
    #
    # Payload in both cases is a list of ``{"title", "message"}``
    # dicts, typically produced by
    # :func:`pre_start_checks.to_dialog_items` from the
    # orchestrator's summary. QML assigns the list to
    # ``ConfirmDialog.checkItems``, which renders one
    # icon-plus-description row per entry — presentation is
    # entirely QML's concern.

    # Hard-refuse dialog (dismiss only). Emitted alongside a
    # ``statusUpdated("Pre-start check failed")`` breadcrumb.
    preStartCheckRefused = Signal(list)

    # Soft-warning dialog (OK / Cancel). The user's response arrives
    # via :meth:`respondToConfirmation`; the runner holds the start
    # state until then.
    preStartCheckNeedsConfirmation = Signal(list)

    def __init__(self, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._is_running: bool = False

        # Threading state — recreated for each run, cleaned up on
        # _on_thread_finished. See app_controller.py for the same
        # pattern with detailed comments on why we don't null these
        # in _on_finished (the deleteLater chain runs after).
        self._thread: Optional[QThread] = None
        self._worker: Optional[_WorkflowWorker] = None
        self._stop_event: Optional[threading.Event] = None

        # Escalation of the Stop button: set when the user presses
        # Stop while a stop is already requested (or presses Stop
        # after the activity loop has ended and only the after_run
        # cleanup is still running). Subclass after_run hooks poll it
        # (via their own latched predicate — see
        # CPWorkflow._after_run) to abandon a slow best-effort
        # restore. Recreated per run alongside _stop_event.
        self._abandon_event: Optional[threading.Event] = None

        # --- Post-run reporting state written by after_run hooks ------
        #
        # Written on the WORKER thread by the subclass's _after_run
        # hook; read on the GUI thread by _on_finished. The write
        # happens-before the worker's queued `finished` emit (which is
        # what triggers _on_finished), so the read is ordered — same
        # pattern as CPWorkflow._after_run's existing attribute
        # cleanup.
        #
        # _after_run_warning: short user-facing warning folded into
        # the final status-bar text ("" = none). Needed because
        # _on_finished composes the final bar text after _after_run
        # has returned — a plain on_status() emit from the hook would
        # be clobbered by "Workflow complete — ...".
        self._after_run_warning: str = ""
        # _after_run_record: optional synthetic session-log record
        # emitted through activityRecorded in _on_finished, while the
        # session is still open (workflowFinished — which closes it —
        # is emitted after). Keys: activity_id, result, duration_s,
        # last_status, params.
        self._after_run_record: Optional[Dict[str, Any]] = None
        # Last exception-path activity status, stashed by
        # _on_activity_finished. Lets _on_finished re-emit the failure
        # text on the exception path even when after_run breadcrumbs
        # ("Restoring PFIB...") have overwritten the status bar since.
        self._last_exception_status: str = ""
        # Mid-run abort reason, recorded by _abort_fetch (or
        # _mid_run_pre_start_check_passes) on the GUI thread during a
        # fetch round-trip. Read by _on_finished — also on the GUI
        # thread, strictly after the abort — so the final status bar
        # text preserves WHY the run ended instead of the misleading
        # "Workflow stopped" Empty when the run wasn't aborted.
        self._abort_status: str = ""

        # GUI-thread bridge for the worker's fetch loop. Long-lived
        # — one helper per runner, reused across runs. See
        # :class:`_FetchHelper` for the rationale.
        self._fetch_helper = _FetchHelper(self)

        # Per-run timing state. Populated as the worker reports
        # durations via the (private) extended ``activityFinished`` /
        # ``finished`` signals; reset to empty / 0.0 at the start of
        # every run (see :meth:`_spawn_worker`). The duration dict is
        # read by QML pages via :meth:`activityDuration` to render the
        # "Total duration: ..." tooltip on a completed activity's
        # status icon. ``_total_duration_s`` is consumed by
        # :meth:`_on_finished` to compose the success-path status bar
        # message.
        #
        # Key shape is ``(activity_id, instance_id)`` — a tuple rather
        # than the ``f"{a}/{b}"`` string used by the worker's
        # ``executed_keys`` set. The set has one job (membership test)
        # and never inspects components; the durations dict will be
        # looked up by the QML-routed pair, so keeping the two ids
        # separate avoids a formatting step at every read.
        self._durations_by_key: Dict[Tuple[str, str], float] = {}
        self._total_duration_s: float = 0.0

        # Parallel store of the most recent ``on_status`` text each
        # activity emitted before finishing, plus the exception summary
        # on a second line when there is one. Used by QML pages (via
        # :meth:`lastStatusMessage`) to compose the exception-path
        # tooltip on the activity's status icon. On the COMPLETE and
        # STOP paths the message is recorded too but pages don't
        # surface it (a completed activity's tooltip shows its
        # duration; a stopped activity has no icon and no tooltip).
        self._status_messages_by_key: Dict[Tuple[str, str], str] = {}

        # --- Two-step Start state ---
        #
        # Populated by :meth:`start` when :meth:`_validate_pre_start`
        # returns ``"needs_confirmation"``; cleared on either branch
        # of :meth:`respondToConfirmation`. ``None`` at all other
        # times. Tracked separately from ``_is_running`` because a
        # pending-confirmation state is "neither idle nor running" —
        # the workflow has committed to the validation pass but not
        # yet to the run.
        self._pending_start: Optional[PendingStart] = None

        # Confirmed check types carried into the running workflow.
        # Populated on the accept path of :meth:`respondToConfirmation`
        # (or reset to empty for the OK path of :meth:`start`).
        # :meth:`_mid_run_pre_start_check_passes` consults this set to
        # avoid re-refusing ASK_CONFIRM checks the user already
        # confirmed at workflow start.
        self._confirmed_check_types: FrozenSet[Type[PreStartCheck]] = frozenset()

    # --- Subclass hooks ----------------------------------------------------

    def _validate_pre_start(self) -> Union[PreStartValidationResult, bool]:
        """Hook invoked on the GUI thread inside :meth:`start`.

        Returns a :class:`PreStartValidationResult`:

        * ``"ok"`` — proceed with the run.
        * ``"refused"`` — refuse the start. The subclass has already
          emitted its own diagnostics (a :attr:`statusUpdated`
          breadcrumb and, where applicable, :attr:`preStartCheckRefused`
          or a per-row ``validationFailed``).
        * ``"needs_confirmation"`` — the subclass has emitted
          :attr:`preStartCheckNeedsConfirmation`; the runner waits for
          :meth:`respondToConfirmation`. ``pending_confirmed_check_types``
          is carried into :attr:`_confirmed_check_types` on accept.

        A plain ``bool`` is also accepted (``True`` → ``"ok"``,
        ``False`` → ``"refused"``) but cannot express
        ``"needs_confirmation"``. Default returns ``"ok"``.
        """
        return PreStartValidationResult(outcome="ok")

    def _on_commit_to_run(self) -> None:
        """Hook called on the GUI thread immediately before spawning the worker.

        Subclasses override to capture state that should reflect the
        moment the run is committed — e.g. a
        :class:`WorkflowSettingsSnapshot` or a PFIB recorder. Fires on
        the ``"ok"`` path of :meth:`start` and the accept path of
        :meth:`respondToConfirmation`; never on a refused or cancelled
        start.

        Default does nothing. An exception here is caught in
        :meth:`_spawn_worker` and refuses the start cleanly (unlike
        :meth:`_before_run`, which is best-effort on the worker thread).
        """
        return None

    def _before_run(self, on_status: StatusCallback) -> None:
        """Hook invoked on the worker thread before any activity runs.

        Default implementation does nothing. Subclasses override to
        capture state, set up shared resources, etc. Errors are
        logged but don't abort the workflow.
        """
        return None

    def _after_run(self, on_status: StatusCallback, completed: bool) -> None:
        """Hook invoked on the worker thread after the activity loop exits.

        Runs on every exit path — success, stop, exception. Subclasses
        override to restore captured state, clean up shared resources,
        etc. Errors are logged but don't change the workflow result.

        ``completed`` is True iff the activity loop ran to natural
        completion (no stop, no exception, no STOP activity result).
        Subclasses use it to gate exit-path-dependent work: e.g. the
        stage-position restore fires only when ``completed`` is True,
        since commanding further stage motion after an abnormal exit
        is unsafe. Cleanup that must happen regardless (dropping
        recorder / snapshot references) ignores it.
        """
        return None

    # --- QML-visible properties -------------------------------------------

    @Property(bool, notify=isRunningChanged)
    def isRunning(self) -> bool:
        return self._is_running

    # --- Subclass hook -----------------------------------------------------

    @abstractmethod
    def _next_pending_activity(
        self, executed_keys: Set[str],
    ) -> FetchOutcome:
        """Return the next activity to run, or ``None`` if no more are pending.

        Called on the GUI thread (via ``Qt.BlockingQueuedConnection``
        from the worker) once per loop iteration. Subclasses walk their
        own source-of-truth and return the first enabled activity whose
        key (``f"{activity_id}/{instance_id}"``) is not in
        ``executed_keys``.

        Subclasses validate the activities they return. For an enabled
        but invalid activity, emit a validation signal and return
        :meth:`_abort_fetch`, which ends the run as not-complete (the
        success-only cleanup is skipped and the recorded abort reason
        becomes the final status). Returning ``None`` ends the run as
        complete. To skip an activity and continue, add its key to
        ``executed_keys`` and fall through to the next candidate within
        the same call.
        """
        raise NotImplementedError

    def _abort_fetch(self, status: str = "") -> _FetchAborted:
        """Abort the run from inside :meth:`_next_pending_activity`.

        Records ``status`` as the run's abort reason and emits it on
        the status bar immediately; :meth:`_on_finished` re-emits it
        as the final text (in place of "Workflow stopped") so it
        survives any later breadcrumbs. Pass an empty ``status`` to
        keep a reason recorded earlier in the same fetch call (e.g.
        by :meth:`_mid_run_pre_start_check_passes`).

        GUI-thread only — call it exclusively from the fetch hook,
        which runs there; ``_abort_status`` is then read by
        ``_on_finished`` on the same thread, after the worker's
        queued ``finished`` emit.

        Returns:
            The sentinel the fetch hook must return to the worker.
        """
        if status:
            self._abort_status = status
            self.statusUpdated.emit(status)
        return _FETCH_ABORTED

    def _mid_run_pre_start_check_passes(
        self,
        activity_class: Type[ActivityService],
        microscope: MicroscopeClientLike,
    ) -> bool:
        """Run pre-start checks at mid-run fetch time.

        Called by subclasses from :meth:`_next_pending_activity` just
        before returning a candidate activity. Mid-run cannot pause for
        a confirmation dialog (the worker is blocked on the fetch, so
        waiting for input on the GUI thread would deadlock), so
        ``ASK_CONFIRM`` outcomes proceed only if their check type is in
        :attr:`_confirmed_check_types`; otherwise they are treated as
        ``REFUSE``.

        On abort, emits :attr:`preStartCheckRefused` and a
        ``"Pre-start check failed"`` breadcrumb, and records that text
        as the run's abort reason. The caller then returns
        ``self._abort_fetch()`` with no message so the recorded reason
        stands.

        Args:
            activity_class: The class whose
                :meth:`ActivityService.pre_start_checks` to run.
            microscope: Used to gather a fresh hardware snapshot.

        Returns:
            ``True`` if the activity can proceed; ``False`` if the
            workflow should abort.
        """
        checks = activity_class.pre_start_checks()
        if not checks:
            return True

        try:
            snapshot = gather_snapshot(microscope)
            summary = run_pre_start_checks(checks, snapshot)
        except Exception:
            # gather_snapshot reads live hardware; a transient glitch
            # mid-run must not escape the fetch hook as an anonymous
            # crash (the _FetchHelper backstop would label it an
            # internal error). Abort with the actual reason instead —
            # mirrors the guard on the start-time pass in the
            # subclasses' _validate_pre_start.
            logger.exception(
                "%s: mid-run pre-start hardware read failed; "
                "aborting workflow", type(self).__name__,
            )
            self._abort_status = "Workflow aborted: hardware read failed"
            self.statusUpdated.emit(self._abort_status)
            return False

        # Build the abort list: all REFUSE results, plus
        # ASK_CONFIRM results whose check type is not in the
        # pre-confirmed set. Results whose check_type IS in the
        # set proceed silently (the user already approved them at
        # workflow start).
        refusals: List[PreStartCheckResult] = list(summary.refused)
        for result in summary.needs_confirmation:
            if result.check_type not in self._confirmed_check_types:
                refusals.append(result)

        if refusals:
            items = to_dialog_items(refusals)
            self.preStartCheckRefused.emit(items)
            # Recorded (not just emitted) so the reason survives as
            # the final status text; the caller's _abort_fetch() is
            # called with no message so this one stands.
            self._abort_status = "Pre-start check failed"
            self.statusUpdated.emit("Pre-start check failed")
            logger.info(
                "%s: mid-run pre-start check failed for %s "
                "(%d violation%s)",
                type(self).__name__, activity_class.__name__,
                len(refusals), "" if len(refusals) == 1 else "s",
            )
            return False

        return True

    # --- Lifecycle ---------------------------------------------------------

    @Slot()
    def start(self) -> None:
        """Begin executing the workflow.

        No-op if a workflow is already running on this runner or if
        a previous :meth:`start` is still awaiting the user's
        confirmation. Returns immediately — the actual work happens
        on the worker thread (or is deferred until
        :meth:`respondToConfirmation` is called).

        Dispatches on :meth:`_validate_pre_start`'s outcome: ``"ok"``
        commits and spawns immediately; ``"refused"`` logs and returns;
        ``"needs_confirmation"`` stashes a :class:`PendingStart` and
        waits for QML to call :meth:`respondToConfirmation`. A ``bool``
        return is normalized to ``"ok"`` / ``"refused"``.
        """
        if self._is_running:
            logger.warning(
                "%s.start() called while already running; ignoring",
                type(self).__name__,
            )
            return

        if self._pending_start is not None:
            # A previous start is still waiting for the user's
            # response to a confirmation dialog. Don't pile on; the
            # dialog should be foregrounded by QML anyway.
            logger.warning(
                "%s.start() called while a previous start is "
                "pending confirmation; ignoring",
                type(self).__name__,
            )
            return

        raw_result = self._validate_pre_start()

        # Bool-compat shim: map a plain ``bool`` return onto the
        # tri-state result.
        if isinstance(raw_result, bool):
            result = PreStartValidationResult(
                outcome="ok" if raw_result else "refused",
            )
        else:
            result = raw_result

        if result.outcome == "refused":
            logger.info(
                "%s.start(): pre-start validation refused; ignoring",
                type(self).__name__,
            )
            return

        if result.outcome == "needs_confirmation":
            self._pending_start = PendingStart(
                confirmed_check_types=result.pending_confirmed_check_types,
            )
            logger.info(
                "%s.start(): awaiting user confirmation "
                "(%d check type(s) pending)",
                type(self).__name__,
                len(result.pending_confirmed_check_types),
            )
            return

        # outcome == "ok" — proceed straight to spawn. No types were
        # confirmed because none needed confirmation.
        self._confirmed_check_types = frozenset()
        self._spawn_worker()

    @Slot(bool)
    def respondToConfirmation(self, accepted: bool) -> None:
        """Resume or abort a start that was paused pending confirmation.

        Called by QML when the user clicks OK (``accepted=True``) or
        Cancel (``accepted=False``) on the confirm dialog driven by
        :attr:`preStartCheckNeedsConfirmation`.

        Accept path: records the confirmed check types in
        :attr:`_confirmed_check_types` (so mid-run re-checks of the
        same types proceed), then spawns the worker via
        :meth:`_spawn_worker`.

        Reject path: emits a ``"Start cancelled"`` breadcrumb and
        clears the pending state. :attr:`_confirmed_check_types` is
        left as-is; the next :meth:`start` resets it.

        No-op if no start is pending (e.g. a duplicate or stale dialog
        dismiss).
        """
        pending = self._pending_start
        self._pending_start = None
        if pending is None:
            logger.warning(
                "%s.respondToConfirmation called with no pending "
                "start; ignoring", type(self).__name__,
            )
            return

        if not accepted:
            logger.info(
                "%s: pre-start confirmation cancelled by user",
                type(self).__name__,
            )
            self.statusUpdated.emit("Start cancelled")
            return

        self._confirmed_check_types = pending.confirmed_check_types
        logger.info(
            "%s: pre-start confirmation accepted; spawning worker "
            "(%d check type(s) confirmed)",
            type(self).__name__,
            len(self._confirmed_check_types),
        )
        self._spawn_worker()

    def _spawn_worker(self) -> None:
        """Set up the thread + worker and start the run.

        Called from :meth:`start` on the OK path and from
        :meth:`respondToConfirmation` on the accept path. Fires
        :meth:`_on_commit_to_run` first so subclasses can capture
        their start-time state; if the hook raises, the run is
        refused cleanly without setting up any thread state.

        Pre-condition: ``self._is_running is False`` and
        ``self._pending_start is None``.
        """
        # Subclass commit hook fires before any thread state is set
        # up — if it raises, we haven't committed any workflow state
        # and the runner stays in a clean idle state.
        try:
            self._on_commit_to_run()
        except Exception:
            logger.exception(
                "%s._on_commit_to_run raised; refusing to start",
                type(self).__name__,
            )
            self.statusUpdated.emit("Cannot start: setup failed")
            return

        # Fresh stop and abandon events for this run. Workers do not
        # share state across runs.
        self._stop_event = threading.Event()
        self._abandon_event = threading.Event()

        # Reset per-run timing and status state. We reset *at start*
        # rather than *at end* so the invariant is "contents reflect
        # the current run, including the most recently completed run"
        # — between runs the values from the last run remain readable
        # by the tooltip wiring, which surfaces a completed activity's
        # duration after the workflow ends.
        self._durations_by_key = {}
        self._total_duration_s = 0.0
        self._status_messages_by_key = {}
        self._after_run_warning = ""
        self._after_run_record = None
        self._last_exception_status = ""
        self._abort_status = ""

        thread = QThread()
        worker = _WorkflowWorker(
            fetch_next=self._fetch_helper.fetch,
            stop_event=self._stop_event,
            before_run=self._before_run,
            after_run=self._after_run,
        )
        worker.moveToThread(thread)

        # Worker entry point — fires when the thread's event loop starts.
        thread.started.connect(worker.run)

        # Forward worker signals to our QML-visible signals. Cross-thread
        # emissions are queued automatically; these slots run on the GUI
        # thread.
        worker.activityStarted.connect(self._on_activity_started)
        worker.activityFinished.connect(self._on_activity_finished)
        worker.progressUpdated.connect(self.progressUpdated)
        worker.statusUpdated.connect(self.statusUpdated)
        worker.finished.connect(self._on_finished)

        # Teardown chain — same shape as AppController's connect thread.
        # See app_controller.py for the rationale on the ordering.
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(self._on_thread_finished)

        self._thread = thread
        self._worker = worker

        self._is_running = True
        self.isRunningChanged.emit()
        self.workflowStarted.emit()

        logger.info("%s: starting workflow", type(self).__name__)
        thread.start()

    @Slot()
    def stop(self) -> None:
        """Request the workflow stop at the next safe interruption point.

        No-op if no workflow is running. The actual stop happens
        asynchronously — the worker may take up to ~1 second to notice
        (the granularity of activity stop checks) plus however long any
        currently-running uninterruptible operation takes (e.g. a sputter
        coat duration that has already started, a home stage call in
        progress).

        Pressing Stop again while a stop is already requested
        ESCALATES: it sets the abandon event, which after_run cleanup
        hooks (the PFIB conditions restore) poll to skip their
        remaining best-effort work. The escalation also covers the
        completed/exception exit paths, where the stop event is set
        for the first time while only the restore is still running —
        the subclass's latched abandon predicate treats that
        first-press as abandon (see CPWorkflow._after_run). An
        AutoScript call already in flight can't be interrupted either
        way; abandon takes effect when it returns.
        """
        if not self._is_running or self._stop_event is None:
            return
        if self._stop_event.is_set():
            # Second press — escalate to abandoning after_run cleanup.
            if (self._abandon_event is not None
                    and not self._abandon_event.is_set()):
                logger.info(
                    "%s: stop re-requested; abandoning post-run cleanup",
                    type(self).__name__,
                )
                self.statusUpdated.emit("Abandoning cleanup...")
                self._abandon_event.set()
            return
        logger.info("%s: stop requested", type(self).__name__)
        self.statusUpdated.emit("Stop requested...")
        self._stop_event.set()

    def wait_for_stop(self, timeout_ms: int = _SHUTDOWN_JOIN_TIMEOUT_MS) -> bool:
        """Block until the worker thread has fully exited; for shutdown.

        Called on the GUI thread during application teardown, after
        :meth:`stop` has requested cancellation, so the worker is
        joined before the microscope client is disconnected (the worker
        holds ops objects that reference the SDB client, and
        disconnecting mid-op risks an indeterminate hardware state).

        Returns ``True`` if the thread finished (or was never running),
        ``False`` on timeout. Safe to call when no workflow is running.

        Bounded edge case: if shutdown lands during the brief
        inter-activity fetch round-trip, the worker is parked on the
        blocked GUI thread (see :class:`_FetchHelper`) and the join
        runs to the full timeout before teardown proceeds. This is
        accepted rather than servicing events during teardown.
        """
        thread = self._thread
        if thread is None or not thread.isRunning():
            return True
        # Note: quit() is called directly rather than relying on the
        # queued ``worker.finished -> thread.quit`` connection — the GUI
        # thread is blocked in wait() and cannot deliver that slot. The
        # quit is queued behind run() and takes effect when it returns.
        thread.quit()
        if thread.wait(timeout_ms):
            return True
        # The join budget is spent — the worker is stuck in slow
        # after_run work (e.g. a settle poll waiting out a species
        # switch). NOW abandon it and grant one short grace join; the
        # poll re-checks abandon at ~1 s granularity, so this usually
        # succeeds. Deliberately not set before the first wait: a
        # pre-set abandon would forbid a restore that hadn't started
        # yet, and the normal quick restore (four writes) comfortably
        # fits the main join budget.
        if self._abandon_event is None:
            return False
        logger.warning(
            "%s: worker did not exit within %d ms; abandoning "
            "post-run cleanup and re-joining",
            type(self).__name__, timeout_ms,
        )
        self._abandon_event.set()
        thread.quit()
        return thread.wait(_ABANDON_GRACE_JOIN_MS)

    # --- QML-callable accessors (Q_INVOKABLE via @Slot result=) -----------
    #
    # Pages use these to compose status-icon tooltips inside their
    # ``onActivityStatusChanged`` handlers. We expose the data via
    # invokable methods rather than a parallel "duration reported"
    # signal because:
    #
    #   * The page is already subscribed to ``activityStatusChanged``
    #     and already routes by ``activity_id`` / ``instance_id``.
    #     Adding two query calls inside that handler is much smaller
    #     than introducing a second signal subscription.
    #
    #   * Tooltips are read-on-demand by the user (mouseover); the
    #     page only needs the data at the moment status flips, not
    #     continuously.
    #
    # If a future feature ever needs to react to durations
    # asynchronously (e.g. a live "elapsed: X" counter while an
    # activity runs), adding a signal then is purely additive.

    @Slot(str, str, result=float)
    def activityDuration(
        self, activity_id: str, instance_id: str,
    ) -> float:
        """Return the wall-clock seconds the named activity ran for.

        Looks up the value the worker recorded when the activity
        finished. Returns ``0.0`` if no run has produced a duration
        for this key — most commonly because the page is querying
        before any workflow has run, or because the activity was
        skipped (mid-run validation failure, switch toggled off
        before its turn).

        Parameters mirror the ``activityStatusChanged`` signal: the
        ``activity_id`` is the type discriminator (e.g.
        ``"gis_purge"``) and ``instance_id`` is empty for
        single-instance activities, populated for cryo per-row
        activities.
        """
        return self._durations_by_key.get((activity_id, instance_id), 0.0)

    @Slot(str, str, result=str)
    def lastStatusMessage(
        self, activity_id: str, instance_id: str,
    ) -> str:
        """Return the named activity's last status text for its tooltip.

        This is the last text the activity passed to ``on_status``;
        on the exception path a second line carries the
        ``"<ExceptionType>: <message>"`` summary. Returns an empty
        string if no run has produced a message for this key (same
        conditions as :meth:`activityDuration`).
        """
        return self._status_messages_by_key.get(
            (activity_id, instance_id), "",
        )

    @Slot(float, result=str)
    def formatDuration(self, seconds: float) -> str:
        """Render a duration as the human-readable string used in tooltips.

        Thin wrapper around :func:`_format_duration` so QML pages
        share the single source of truth for the format rules
        (``"M min S s"`` form, sub-minute durations omit the minutes
        component, no hours tier, round to nearest second). Pages
        consume the result like::

            "Total duration: " + appController.rtWorkflow.formatDuration(dur)

        Keeping the helper accessible from QML — rather than
        reimplementing the rules in JavaScript inside ``AppConfig`` or
        a util singleton — eliminates the drift risk between the
        Python and QML rule sets.
        """
        return _format_duration(seconds)

    @Slot(result=float)
    def totalDuration(self) -> float:
        """Return the most-recent run's total wall-clock seconds.

        Populated by :meth:`_on_finished` from the worker's
        ``finished(all_complete, total_duration_s)`` signal. ``0.0``
        before the first workflow run; otherwise the duration of the
        most recently completed (or stopped, or exception-raised) run.

        Exposed as a Slot rather than baked into the
        :attr:`workflowFinished` signal payload to match the existing
        :meth:`activityDuration` / :meth:`lastStatusMessage` accessor
        pattern — keep the signal narrow, query for the rest. Consumed
        by :class:`SessionLog`'s ``on_workflow_finished`` handler at
        the wiring layer (see ``AppController._wire_workflow``).
        """
        return self._total_duration_s

    # --- Worker signal handlers (run on GUI thread) -----------------------

    @Slot(str, str)
    def _on_activity_started(
        self, activity_id: str, instance_id: str,
    ) -> None:
        self.activityStatusChanged.emit(activity_id, instance_id, "running")

    @Slot(str, str, str, float, str, str, object)
    def _on_activity_finished(
        self,
        activity_id: str,
        instance_id: str,
        result_str: str,
        duration_s: float,
        last_status: str,
        detail: str,
        params: Dict[str, Any],
    ) -> None:
        # result_str is one of "complete", "stop", "exception". We pass
        # it straight through as the status — matches the
        # ActivityContainer's activityState property values.
        #
        # Durations and last-status messages are stashed for every
        # outcome (complete / stop / exception) — the measurements are
        # real regardless of how the activity exited, and pages decide
        # which are surfaced. Today the COMPLETE path renders duration
        # and the EXCEPTION path renders last_status; STOP renders
        # neither (no icon, no tooltip). The dicts are exposed to QML
        # via :meth:`activityDuration` and :meth:`lastStatusMessage`
        # — the page handler queries them inside its own
        # ``onActivityStatusChanged`` slot rather than us emitting a
        # second signal.
        #
        # ``params`` is intentionally NOT stashed in a parallel
        # ``_params_by_key`` dict: unlike duration / last_status
        # (queried by QML pages on tooltip mouseover), the session log
        # subscribes to :attr:`activityRecorded` and consumes the
        # dict off that signal directly. Adding an accessor we don't
        # need would be sugar without a consumer.
        key = (activity_id, instance_id)
        self._durations_by_key[key] = duration_s
        # Tooltip text: the short status, plus the exception summary
        # on a second line when there is one. The bar and the session
        # log only ever see ``last_status``.
        self._status_messages_by_key[key] = (
            f"{last_status}\n{detail}" if detail else last_status
        )
        if result_str == ActivityResult.EXCEPTION.value:
            # Stashed so _on_finished can re-emit the failure text as
            # the final status-bar message — after_run breadcrumbs
            # ("Restoring PFIB...") may overwrite the bar between the
            # activity failing and the workflow finishing.
            self._last_exception_status = last_status
        self.activityStatusChanged.emit(activity_id, instance_id, result_str)

        # Emit the full per-activity record for SessionLog (and any
        # future consumer). Fires *after* ``activityStatusChanged`` —
        # see the signal-declaration comment for the rationale.
        self.activityRecorded.emit(
            activity_id, instance_id, result_str,
            duration_s, last_status, params,
        )

    @Slot(bool, float, bool)
    def _on_finished(
        self,
        all_complete: bool,
        total_duration_s: float,
        ended_by_exception: bool,
    ) -> None:
        self._total_duration_s = total_duration_s

        # The worker reports WHY the loop ended (``ended_by_exception``)
        # rather than us re-deriving it from the stop event here: a
        # Stop press during the after_run cleanup — the documented
        # abandon gesture — sets the stop event on a run that actually
        # ended by exception, and must not reclassify it as a user
        # stop (which would discard ``_last_exception_status``).

        self._is_running = False
        self.isRunningChanged.emit()

        # IMPORTANT: any final ``statusUpdated`` emit must happen
        # *before* ``workflowFinished`` below. ``AppController``
        # listens on ``workflowFinished`` and synchronously clears
        # ``runningWorkflowId`` to ``""``. The StatusBar in
        # ``main.qml`` multiplexes its Connections target on
        # ``runningWorkflowId`` (``case "rt_prep": return
        # appController.rtWorkflow`` ...), so the moment that flips,
        # ``mainStatusBar._activeRunner`` re-evaluates to ``null`` and
        # our ``statusUpdated`` slot is no longer connected. Emitting
        # the final message first ensures it lands while the binding
        # is still pointing at us.
        # Synthetic session-log record from the after_run hook (e.g. a
        # failed PFIB restore). Emitted here — on the GUI thread, and
        # while the session is still open (workflowFinished below is
        # what closes it) — rather than from the worker, preserving
        # the worker↔runner signal discipline.
        rec = self._after_run_record
        if rec is not None:
            self.activityRecorded.emit(
                rec["activity_id"], "", rec["result"],
                rec["duration_s"], rec["last_status"], rec["params"],
            )

        # Warning from the after_run hook, folded into the final
        # status text so it isn't clobbered by the completion message.
        # The duration is dropped from the warning forms deliberately:
        # short bar, and the duration is still in the log / session
        # record.
        warning = self._after_run_warning
        duration = _format_duration(total_duration_s)
        if all_complete:
            if warning:
                final = f"Workflow complete — {warning}"
            else:
                final = f"Workflow complete — {duration}"
        elif not ended_by_exception:
            if self._abort_status:
                # Mid-run abort (validation failure / pre-start-check
                # refusal). Preserve the abort reason as the final
                # text — "Workflow stopped" would mislabel it as a
                # user action, and the transient reason emitted at
                # abort time may have been overwritten since.
                final = self._abort_status
                if warning:
                    final = f"{final} — {warning}"
            # User-requested stop (or a stop-result activity). Replace
            # the transient "Stop requested..." that ``stop()`` emitted
            # with a final state message — otherwise the StatusBar is
            # stuck showing the in-flight request indefinitely.
            elif warning:
                final = f"Workflow stopped — {warning}"
            else:
                final = f"Workflow stopped — {duration}"
        else:
            # Exception path. Re-emit the failed activity's message —
            # after_run breadcrumbs ("Restoring PFIB...") may have
            # overwritten it in the bar since. With a post-run warning
            # as well, a short generic head keeps the line readable;
            # the activity's own text is still in its tooltip.
            if warning:
                final = f"Workflow failed — {warning}"
            else:
                final = self._last_exception_status
        if final:
            self.statusUpdated.emit(shorten(final))
        # else: nothing to say beyond what's already in the bar.

        self.workflowFinished.emit(all_complete)

    @Slot()
    def _on_thread_finished(self) -> None:
        # Mirror AppController's _on_connect_thread_finished — null
        # references after the thread's event loop has unwound, so
        # subsequent state inspection can rely on "is not None" meaning
        # "alive."
        self._thread = None
        self._worker = None
        self._stop_event = None
        self._abandon_event = None