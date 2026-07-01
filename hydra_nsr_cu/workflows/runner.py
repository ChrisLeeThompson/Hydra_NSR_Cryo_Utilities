"""Workflow runner base class and worker.

The runner exposes a uniform interface to QML (``start()``, ``stop()``,
``isRunning``, status signals) regardless of which workflow it's running.
Page-specific subclasses extend this with their own parameters and
property surface — they don't override the threading machinery.

Subclasses must implement two methods:

* :meth:`WorkflowRunner._validate_pre_start` — called on the GUI thread
  inside :meth:`start` before any worker is spawned. Returns a
  :class:`PreStartValidationResult` with one of three outcomes:

  - ``"ok"``: the start proceeds; :meth:`_on_commit_to_run` fires and
    the worker is spawned immediately.
  - ``"refused"``: the start is refused outright. Subclasses are
    expected to have already emitted user-facing diagnostics (a
    :attr:`statusUpdated` breadcrumb and, if applicable,
    :attr:`preStartCheckRefused` with a dialog message).
  - ``"needs_confirmation"``: the start is paused pending the
    confirm dialog. Subclasses are expected to have already emitted
    :attr:`preStartCheckNeedsConfirmation`. The runner waits for
    QML to call :meth:`respondToConfirmation` with the user's choice.

  For backwards compatibility, subclasses may still return ``bool``:
  ``True`` is treated as ``"ok"`` and ``False`` as ``"refused"``. The
  bool form has no way to express ``"needs_confirmation"`` — subclasses
  using the ASK_CONFIRM path MUST return :class:`PreStartValidationResult`.

* :meth:`WorkflowRunner._next_pending_activity` — called on the GUI
  thread (via ``BlockingQueuedConnection`` from the worker) once per
  iteration. Returns the next :class:`ActivityService` to run, or
  ``None`` to end the loop. Subclasses walk their own source-of-truth
  (a static list for RT-style workflows, the activity model for
  Cryo-style workflows) and skip any activity whose key is in the
  ``executed_keys`` argument.

Iteration model
---------------
Activities are fetched one at a time as the worker runs, rather than
snapshotted at start. This lets the user add activities to the queue
or toggle switches mid-run — anything still in the source-of-truth
when the worker asks for the next pending activity will run, as long
as it hasn't already been executed in this run.

The "key" used to identify an executed activity is
``f"{activity_id}/{instance_id}"``. For single-instance activities
(RT-style), ``instance_id`` is empty and the key collapses to
``"{activity_id}/"``, which is unique within a run.

If an activity fails validation when it's about to be fetched (e.g.
a GIS Deposition added mid-run that references a deleted position),
the subclass emits its own validation signal and returns ``None``
to end the workflow. The reasoning is workflow-specific: see
:class:`CPWorkflow`'s discussion of layered deposition. Subclasses
that don't have such a constraint can choose to skip-and-continue
instead.

Two-step Start
--------------
The pre-start check system can refuse the start outright (REFUSE
outcome) or ask the user to confirm (ASK_CONFIRM outcome). The
runner threads the ASK_CONFIRM path through a small state machine:

* :meth:`start` calls :meth:`_validate_pre_start`. If the result is
  ``"needs_confirmation"``, the runner stashes a :class:`PendingStart`
  and returns; QML is expected to open the confirm dialog (driven by
  the :attr:`preStartCheckNeedsConfirmation` signal the subclass
  emitted).
* QML calls :meth:`respondToConfirmation` with the user's accept/reject.
* Accept → :meth:`_on_commit_to_run` + :meth:`_spawn_worker`.
* Reject → "Start cancelled" status, pending state cleared.

The ``"ok"`` outcome bypasses the dialog and goes straight to
:meth:`_on_commit_to_run` + :meth:`_spawn_worker`.

The commit hook (:meth:`_on_commit_to_run`) is the natural place for
subclasses to capture start-time state (e.g. a
:class:`WorkflowSettingsSnapshot`, a PFIB recorder). It fires only on
paths committed to a run — never on a refused or cancelled start.

TODO: the QThread/worker/deleteLater teardown pattern used here also
appears in :mod:`app_controller` (for the connect thread) and
:mod:`stage_positions.controller` (for the stage move thread). If a
fourth case appears, or any of these three need to evolve, consider
extracting a small ``ThreadedTask`` helper that absorbs the lifecycle
plumbing. Holding off for now because the contexts differ enough that
a premature abstraction might lose more than it saves.
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


def _format_duration(seconds: float) -> str:
    """Render an elapsed-time duration for the status bar.

    Used by :meth:`WorkflowRunner._on_finished` to compose the
    "Workflow complete. Total duration: ..." message at the end of a
    successful run.

    Format rules:

    * Seconds are rounded to the nearest whole integer.
    * Sub-minute durations omit the minutes component
      (``"23 s"``, not ``"0 min 23 s"``) — a "0 min" prefix would be
      visual noise in the status bar's tight horizontal slot.
    * Minute-and-above durations use the ``"M min S s"`` form.
    * No hours tier — a 95-minute workflow renders as ``"95 min 5 s"``.
      Workflows over an hour are rare in practice; if that changes we
      can grow the formatter without affecting callers.

    The boundary case worth pinning down: rounding can push a value
    just under 60 seconds into the next tier
    (``59.6`` → ``round`` → ``60`` → ``"1 min 0 s"``). This is
    intentional — the rounded value is what the user thinks of as
    elapsed time, so the formatting tier should follow.

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


# --- Pre-start validation result types --------------------------------------


@dataclass(frozen=True)
class PreStartValidationResult:
    """Tri-state outcome from a subclass's :meth:`_validate_pre_start`.

    Replaces the legacy ``bool`` return type. The base class also
    accepts ``bool`` for backwards compatibility (``True`` → ``"ok"``,
    ``False`` → ``"refused"``), so existing subclasses that have not
    been ported yet continue to work; only the ``"needs_confirmation"``
    outcome requires the new dataclass form.

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

    # Emitted at the end of each activity. Six args:
    #   activity_id  — same as above.
    #   instance_id  — same as above.
    #   result       — ActivityResult value as string ("complete",
    #                  "stop", "exception").
    #   duration_s   — wall-clock seconds the activity ran for, measured
    #                  with ``time.monotonic()`` immediately around the
    #                  ``activity.run(...)`` call. Reported on every
    #                  outcome (complete / stop / exception) — the
    #                  measurement is real regardless of how the
    #                  activity exited, and consumers can decide how to
    #                  render partial durations. ``time.monotonic()`` is
    #                  used (not ``time.time()``) so an NTP sync or
    #                  manual clock change during a run doesn't perturb
    #                  the measurement.
    #   last_status  — the most recent string the activity passed to
    #                  its ``on_status`` callback before returning or
    #                  raising. Empty string if the activity never
    #                  emitted a status. Used by pages to render an
    #                  informative tooltip on the exception status icon
    #                  (e.g. "GIS purge: failed — port not found"
    #                  rather than a generic "Failed"). The full
    #                  traceback continues to land in the log via
    #                  ``logger.exception`` and is intentionally not
    #                  exposed to the UI — tooltips are too narrow for
    #                  multi-line content.
    #   params       — dict from ``ActivityService.parameter_summary()``,
    #                  captured once before ``activity.run()`` (the
    #                  activity is single-use and stateless beyond its
    #                  constructor, per its base-class contract, so
    #                  capture-before is equivalent to capture-after
    #                  while giving us a populated value even when
    #                  ``run()`` raises). Forwarded to the runner's
    #                  public :attr:`WorkflowRunner.activityRecorded`
    #                  signal for SessionLog consumption. ``{}`` on the
    #                  rare case ``parameter_summary()`` itself raised
    #                  — see :func:`_safe_parameter_summary`. Declared
    #                  as ``object`` in the Signal signature for the
    #                  same reason ``_ConnectWorker.finished`` uses
    #                  ``object`` for its Optional payload — Qt's meta
    #                  type system doesn't directly support dict
    #                  shapes, and the receiver knows what to expect.
    activityFinished = Signal(str, str, str, float, str, object)

    # Forwarded to the StatusBar progress bar: (current_seconds, total_seconds).
    progressUpdated = Signal(int, int)

    # Forwarded to the StatusBar text: short human-readable status.
    statusUpdated = Signal(str)

    # Emitted once the entire workflow is done. Two args:
    #   all_complete       — True if every activity completed normally;
    #                        False if any was stopped or raised, or if
    #                        the loop exited early via a None fetch.
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
    finished = Signal(bool, float)

    def __init__(
        self,
        fetch_next: Callable[[Set[str]], Optional[ActivityService]],
        stop_event: threading.Event,
        before_run: Optional[Callable[[StatusCallback], None]] = None,
        after_run: Optional[Callable[[StatusCallback, bool], None]] = None,
    ) -> None:
        super().__init__()
        # Fetch callback: given the set of executed activity keys,
        # returns the next pending activity or None when the loop
        # should end. The callable handles the GUI-thread dispatch
        # itself (see WorkflowRunner.start for how it's set up).
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
                # when a subclass aborts (e.g. mid-run validation
                # failure on a layered-deposition workflow).
                activity = self._fetch_next(executed_keys)
                if activity is None:
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
                # A list-of-one (rather than ``nonlocal``) keeps the
                # closure simple and explicit about the mutation.
                last_status: List[str] = [""]

                def _on_status(text: str) -> None:
                    last_status[0] = text
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
                    # Surface the exception's message in the tooltip so
                    # the user sees the actual cause and not just the
                    # phase label the activity was in when it failed.
                    # Per the AutoScript SDK, ``str(exc)`` is a
                    # human-readable description; the full traceback is
                    # in the log via ``logger.exception`` below. Two
                    # cases:
                    #   * No prior on_status (activity raised before
                    #     reporting any phase): synthesize
                    #     "<ExcType>: <message>" so the tooltip has
                    #     both a kind and a cause.
                    #   * Prior on_status present (activity reported a
                    #     phase, then raised uncaught): append the
                    #     exception message to that phase with an
                    #     em-dash separator. Activities that catch
                    #     their own exceptions handle this themselves
                    #     via ``report_activity_exception``; this
                    #     branch is the fallback for uncaught
                    #     propagation.
                    exc_msg = str(exc)
                    if last_status[0]:
                        last_status[0] = f"{last_status[0]} — {exc_msg}"
                    else:
                        last_status[0] = f"{type(exc).__name__}: {exc_msg}"
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
                        params,
                    )
                    executed_keys.add(key)
                    all_complete = False
                    break

                activity_duration = time.monotonic() - t0_activity
                self.activityFinished.emit(
                    activity_id, instance_id, result.value,
                    activity_duration,
                    last_status[0],
                    params,
                )
                logger.info(
                    "Workflow: activity %r (instance=%r) finished "
                    "with %s in %.2fs",
                    activity_id, instance_id, result.value,
                    activity_duration,
                )
                executed_keys.add(key)

                # A stop result from one activity propagates to the workflow.
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
        self.finished.emit(all_complete, total_duration)


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
        self._result: Optional[ActivityService] = None
        self.fetchRequested.connect(
            self._on_fetch, Qt.ConnectionType.BlockingQueuedConnection,
        )

    def fetch(
        self, executed_keys: Set[str],
    ) -> Optional[ActivityService]:
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
            # If the subclass raises, we surface None to the worker
            # (which will end the loop with all_complete=False) and
            # log here. Subclass exceptions inside _next_pending_activity
            # are unexpected — they suggest a programming error rather
            # than user data invalidity (which should return None
            # cleanly with a validationFailed signal).
            logger.exception(
                "Workflow: _next_pending_activity raised; ending workflow"
            )
            self._result = None


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
        # activity emitted before finishing. Used by QML pages (via
        # :meth:`lastStatusMessage`) to compose the exception-path
        # tooltip on the activity's status icon — typically something
        # like "GIS purge: failed — port not found", which is far more
        # useful than a generic "Failed" badge. On the COMPLETE and
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
        # Subclasses' mid-run pre-start check passes (step 8 of the
        # pre-start check rollout) consult this set to avoid
        # reprompting for ASK_CONFIRM checks the user already
        # confirmed at workflow start. Currently unused at this stage
        # of the rollout but populated correctly so subsequent steps
        # don't need to revisit the lifecycle.
        self._confirmed_check_types: FrozenSet[Type[PreStartCheck]] = frozenset()

    # --- Subclass hooks ----------------------------------------------------

    def _validate_pre_start(self) -> Union[PreStartValidationResult, bool]:
        """Hook invoked on the GUI thread inside :meth:`start`.

        Returns a :class:`PreStartValidationResult` carrying one of
        three outcomes:

        * ``"ok"`` — proceed with the run.
        * ``"refused"`` — refuse the start. The subclass is expected
          to have emitted its own diagnostics (a
          :attr:`statusUpdated` breadcrumb plus, where applicable,
          :attr:`preStartCheckRefused` with a dialog message,
          and any subclass-specific signals like
          ``validationFailed`` for per-row UI feedback).
        * ``"needs_confirmation"`` — pause the start pending user
          confirmation. The subclass is expected to have emitted
          :attr:`preStartCheckNeedsConfirmation` so QML can open the
          dialog. The runner stashes a :class:`PendingStart` and
          waits for :meth:`respondToConfirmation`. The
          ``pending_confirmed_check_types`` field on the result is
          carried into :attr:`_confirmed_check_types` on the accept
          path.

        For backwards compatibility, this hook may also return
        ``bool``: ``True`` → ``"ok"``, ``False`` → ``"refused"``. The
        bool form has no way to express ``"needs_confirmation"``, so
        subclasses using the ASK_CONFIRM path MUST return
        :class:`PreStartValidationResult`.

        Default returns ``PreStartValidationResult(outcome="ok")``.

        Note that this is the *all-or-nothing* validation pass at
        Start time: if any currently-enabled activity is invalid,
        the entire start is refused. Validation of activities added
        mid-run happens inside :meth:`_next_pending_activity` and is
        per-fetch.
        """
        return PreStartValidationResult(outcome="ok")

    def _on_commit_to_run(self) -> None:
        """Hook called immediately before spawning the worker.

        Subclasses override to capture any state that should reflect
        the moment the run is committed to actually starting — e.g.
        a :class:`WorkflowSettingsSnapshot`, a PFIB recorder. Runs
        on the GUI thread.

        Two paths fire this hook:

        * OK path of :meth:`start` — :meth:`_validate_pre_start`
          returned ``"ok"`` (or legacy ``True``).
        * Accept path of :meth:`respondToConfirmation` — the user
          confirmed an ASK_CONFIRM dialog.

        It does NOT fire on the refused or rejected paths, so the
        captured state is bound only to runs that actually start.

        Default does nothing. Exceptions are caught in
        :meth:`_spawn_worker` and cause the start to abort cleanly
        — capture failure before spawn is fatal to the run (unlike
        :meth:`_before_run`, which is best-effort on the worker
        thread).
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
    ) -> Optional[ActivityService]:
        """Return the next activity to run, or ``None`` if no more are pending.

        Called on the GUI thread (via ``Qt.BlockingQueuedConnection``
        from the worker thread) once per iteration of the workflow
        loop. Subclasses walk their own source-of-truth and return
        the first activity that is enabled and whose key is not in
        ``executed_keys``.

        The "key" format is ``f"{activity_id}/{instance_id}"``. For
        single-instance activities (``instance_id == ""``), the key
        collapses to ``"{activity_id}/"`` which is unique within a
        run.

        Subclasses are responsible for parameter validation of
        activities returned here. If an activity is enabled but
        invalid (e.g. a GIS Deposition with a stale position
        reference added mid-run), the subclass should emit its own
        validation signal and return ``None`` to abort the workflow.
        See :class:`CPWorkflow` for the rationale specific to
        layered deposition.

        Skipping an enabled activity
        ----------------------------
        Subclasses MAY mutate the passed-in ``executed_keys`` set to
        mark an activity as "handled but skipped" — useful when an
        activity can't be built due to a non-fatal configuration
        issue (e.g. an empty GIS port name in :class:`RTWorkflow`)
        and the workflow should continue to subsequent activities.
        After mutating the set, the subclass should fall through to
        check the next candidate activity within the same call,
        rather than returning ``None`` (which would end the loop).

        Returning ``None`` ends the workflow loop with
        ``all_complete=True`` only if no activities have been
        skipped or aborted; the worker tracks completion based on
        each activity's ``ActivityResult``, not on the absence of
        further pending work.
        """
        raise NotImplementedError
    
    def _mid_run_pre_start_check_passes(
        self,
        activity_class: Type[ActivityService],
        microscope: MicroscopeClientLike,
    ) -> bool:
        """Run pre-start checks at mid-run fetch time.

        Called by subclasses from :meth:`_next_pending_activity`
        just before returning a candidate activity. Returns ``True``
        if the activity can proceed; ``False`` if the workflow must
        abort (caller returns ``None`` from
        ``_next_pending_activity``, which ends the worker loop with
        ``all_complete=False``).

        Mid-run cannot pause for a confirmation dialog. The worker
        thread is blocked on the fetch via
        :class:`_FetchHelper`'s ``BlockingQueuedConnection``; opening
        a dialog and waiting for user input on the GUI thread would
        deadlock the fetch round-trip. Instead, ``ASK_CONFIRM``
        outcomes are honored only if the check type is already in
        :attr:`_confirmed_check_types` — meaning the user
        pre-confirmed it at workflow start. ``ASK_CONFIRM`` whose
        type is NOT pre-confirmed is treated as ``REFUSE`` (abort).

        On abort, emits :attr:`preStartCheckRefused` with the
        check-result items and a ``"Pre-start check failed"``
        status breadcrumb. The user sees the REFUSE dialog
        mid-workflow; the worker's ``finished`` slot leaves the
        breadcrumb text in place (exception-path semantics —
        neither ``all_complete`` nor ``was_stopped``).

        Args:
            activity_class: The class whose
                :meth:`ActivityService.pre_start_checks` to run.
                The classmethod is called fresh each time, so the
                current set of checks for that class is always used.
            microscope: Used to gather a fresh snapshot. Passed in
                rather than stored on the base class — the base
                doesn't need to know about microscope types, and
                each subclass holds its own ``self._microscope``.

        Returns:
            ``True`` if the activity can proceed; ``False`` if the
            workflow should abort.
        """
        checks = activity_class.pre_start_checks()
        if not checks:
            return True

        snapshot = gather_snapshot(microscope)
        summary = run_pre_start_checks(checks, snapshot)

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
        on the worker thread (or is deferred entirely until
        :meth:`respondToConfirmation` is called).

        Tri-state dispatch
        ------------------
        :meth:`_validate_pre_start` returns one of three outcomes:

        * ``"ok"`` — commit and spawn immediately
          (:meth:`_on_commit_to_run` then :meth:`_spawn_worker`).
        * ``"refused"`` — log and return; the subclass has already
          emitted user-facing diagnostics.
        * ``"needs_confirmation"`` — stash a :class:`PendingStart`
          and return. QML opens the confirm dialog (driven by the
          :attr:`preStartCheckNeedsConfirmation` signal the subclass
          emitted) and calls back via :meth:`respondToConfirmation`.

        Backwards compatibility: subclasses that still return ``bool``
        are normalized to ``"ok"`` / ``"refused"``. The ASK_CONFIRM
        path requires the dataclass return type.
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

        # Bool-compat shim: subclasses that haven't been ported to
        # the tri-state return type still return ``bool``. Map it.
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
        same types won't reprompt), fires :meth:`_on_commit_to_run`,
        and spawns the worker via :meth:`_spawn_worker`.

        Reject path: emits a ``"Start cancelled"`` status bar
        breadcrumb and clears the pending state. The captured
        :attr:`_confirmed_check_types` from the previous run is
        intentionally NOT cleared here — clearing happens at the
        next :meth:`start` call (the new run starts with whatever
        confirmation result it produces). A cancelled start is a
        no-op as far as the next run is concerned.

        Defensive no-op if no start is pending (e.g. QML fires the
        signal twice through a race, or a stale dialog dismiss
        arrives after a separate state change).
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

        # Fresh stop event for this run. Workers do not share state
        # across runs.
        self._stop_event = threading.Event()

        # Reset per-run timing and status state. We reset *at start*
        # rather than *at end* so the invariant is "contents reflect
        # the current run, including the most recently completed run"
        # — between runs the values from the last run remain readable
        # by the tooltip wiring, which surfaces a completed activity's
        # duration after the workflow ends.
        self._durations_by_key = {}
        self._total_duration_s = 0.0
        self._status_messages_by_key = {}

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
        """
        if not self._is_running or self._stop_event is None:
            return
        if self._stop_event.is_set():
            return  # Already requested
        logger.info("%s: stop requested", type(self).__name__)
        self.statusUpdated.emit("Stop requested...")
        self._stop_event.set()

    def wait_for_stop(self, timeout_ms: int = _SHUTDOWN_JOIN_TIMEOUT_MS) -> bool:
        """Block until the worker thread has fully exited; for shutdown.

        Intended to be called on the GUI thread during application
        teardown, AFTER :meth:`stop` has requested cancellation, so the
        caller can join the worker before the microscope client is
        disconnected. The worker holds ops objects that reference the
        SDB client, and disconnecting while a hardware op is in flight
        risks an indeterminate state — so we wait for the worker to
        leave :meth:`run` before letting teardown proceed.

        This calls ``quit()`` on the thread DIRECTLY rather than
        relying on the queued ``worker.finished -> thread.quit``
        connection wired in :meth:`start`. During shutdown the GUI
        thread is blocked here and cannot deliver that queued slot, so
        the thread's event loop would otherwise never exit and the wait
        would always time out. (The same mechanism is why the connect
        thread's plain ``wait`` can miss its queued result — see
        ``AppController.shutdown``.) ``quit()`` is queued behind the
        still-running ``run()`` and takes effect the moment ``run()``
        returns at its next safe stop check.

        Returns ``True`` if the thread finished (or was never running),
        ``False`` on timeout. Safe to call when no workflow is running.

        Known bounded edge case: the worker fetches each next activity via
        :class:`_FetchHelper`, which emits over a
        ``BlockingQueuedConnection`` and parks the worker until the GUI
        thread services :meth:`_FetchHelper._on_fetch`. If shutdown enters
        this method during that brief inter-activity fetch round-trip — and
        only then — the worker is parked waiting on the (now blocked) GUI
        thread while the GUI thread waits on the worker: a transient cyclic
        wait. ``quit()`` cannot break it (the worker is in ``run``, not its
        event loop), so ``wait`` runs to the full ``timeout_ms``, logs the
        "did not exit in time" warning, and teardown proceeds. This is
        bounded (the timeout), self-recovering, and has no correctness or
        data impact — only a slow quit in a narrow timing window (the
        worker spends almost all of its life inside ``activity.run``, where
        stop is noticed within ~1 s). The robust fix would be to service
        events during the join, but this app deliberately avoids
        event-loop re-entry during teardown (see ``AppController.shutdown``
        and the M5 in-flight-connect handling), so the bounded stall is
        accepted rather than risk teardown re-entrancy.
        """
        thread = self._thread
        if thread is None or not thread.isRunning():
            return True
        thread.quit()
        return thread.wait(timeout_ms)

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
        """Return the most recent status text the named activity emitted.

        Captured by the worker by wrapping the activity's
        ``on_status`` callback — the value is whatever the activity
        last passed to ``on_status`` before returning or raising. On
        the exception path, if the activity raised without ever
        emitting a status, the worker synthesizes a single-line
        ``"<ExceptionType>: <message>"`` summary instead so the
        tooltip is never empty for a failed activity.

        Returns an empty string if no run has produced a message for
        this key (same conditions as :meth:`activityDuration`).
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

    @Slot(str, str, str, float, str, object)
    def _on_activity_finished(
        self,
        activity_id: str,
        instance_id: str,
        result_str: str,
        duration_s: float,
        last_status: str,
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
        self._status_messages_by_key[key] = last_status
        self.activityStatusChanged.emit(activity_id, instance_id, result_str)

        # Emit the full per-activity record for SessionLog (and any
        # future consumer). Fires *after* ``activityStatusChanged`` —
        # see the signal-declaration comment for the rationale.
        self.activityRecorded.emit(
            activity_id, instance_id, result_str,
            duration_s, last_status, params,
        )

    @Slot(bool, float)
    def _on_finished(
        self, all_complete: bool, total_duration_s: float,
    ) -> None:
        self._total_duration_s = total_duration_s

        # Capture stop state before _is_running flips. The event
        # survives until _on_thread_finished nulls it (that runs after
        # _on_finished via the deleteLater chain), so this read is
        # safe. We use it below to distinguish "user clicked Stop"
        # from "activity raised an exception" — the two non-success
        # paths want different final-status behavior.
        was_stopped = (
            self._stop_event is not None and self._stop_event.is_set()
        )

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
        if all_complete:
            self.statusUpdated.emit(
                f"Workflow complete. Total duration: "
                f"{_format_duration(total_duration_s)}"
            )
        elif was_stopped:
            # User-requested stop. Replace the transient
            # "Stop requested..." that ``stop()`` emitted with a
            # final state message — otherwise the StatusBar is stuck
            # showing the in-flight request indefinitely.
            self.statusUpdated.emit(
                f"Workflow stopped. Total duration: "
                f"{_format_duration(total_duration_s)}"
            )
        # else: exception path. Leave the activity's last user-facing
        # status text in place — "Sputter coat: failed to set ion
        # species" is more informative than a generic failure line.

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