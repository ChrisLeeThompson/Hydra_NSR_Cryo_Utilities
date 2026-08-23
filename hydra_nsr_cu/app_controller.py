"""Top-level application controller.

Owns the microscope client, the settings controller, and per-page
workflow runners. Exposes all of them to QML through a single
``appController`` context property.

Cross-page workflow tracking
----------------------------
Only one workflow runs at a time across the whole app — running RT Prep
prevents starting Cryo Prep, and vice versa. The controller tracks which
workflow (if any) is running via :attr:`runningWorkflowId`. Pages bind
their ``enabled`` state to a check against this property.

Threading note
--------------
The microscope connection attempt is synchronous and blocking inside
AutoScript's ``SdbMicroscopeClient.connect()`` — typically 1–3s on a
fast-fail (connection refused) and up to ~30s on a slow timeout
(connecting to an unreachable but routable address). Running it on the
GUI thread freezes the window. We therefore run the connect attempt on
a short-lived ``QThread`` worker and update the controller's state
asynchronously when the worker finishes.

The simulation path stays synchronous — there is no connection to make,
so no benefit to threading it.
"""
from __future__ import annotations

import logging
from typing import Dict, Optional, Union

from PySide6.QtCore import Property, QObject, QThread, Signal, Slot

from .microscope.client import MicroscopeClient
from .microscope.simulated_client import SimulatedMicroscopeClient
from .settings.settings_controller import SettingsController
from .workflows.cp_workflow import CPWorkflow
from .workflows.rt_workflow import RTWorkflow
from .workflows.runner import WorkflowRunner
from .stage_positions.controller import StagePositionsController
from .stage_scan.controller import StageScanController
from .cryo.controller import CryoActivitiesController
from .session_log.controller import SessionLog

logger = logging.getLogger(__name__)


# Stable IDs for "running operations" — workflows and other
# user-initiated operations that should hold the global cross-page
# disable lock. The "WORKFLOW_ID" prefix is retained for continuity
# with existing code; the actual concept is broader.
WORKFLOW_ID_RT_PREP = "rt_prep"
WORKFLOW_ID_STAGE_MOVE = "stage_move"
WORKFLOW_ID_CRYO_PREP = "cryo_prep"
WORKFLOW_ID_STAGE_ROTATION = "stage_rotation"


class _ConnectWorker(QObject):
    """Worker that performs a single microscope connection attempt.

    Lives on a dedicated :class:`QThread` for the duration of one connect
    attempt, then is destroyed via ``deleteLater`` once :attr:`finished`
    has been emitted. Single-shot — do not reuse.

    The worker emits the resulting :class:`MicroscopeClient` on success,
    or ``None`` on any failure (import error, connection refused,
    timeout, etc.). Exceptions are logged inside the worker; the caller
    decides fallback policy based on the result.
    """

    # Emits MicroscopeClient or None. Declared as ``object`` because
    # QtCore signal types don't accept Optional[T] directly.
    finished = Signal(object)

    def __init__(self, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        # Set to the connected client immediately before ``finished``
        # is emitted on success; stays None on every failure path. Lets
        # AppController.shutdown() retrieve and disconnect a client that
        # was produced by a connect which completed in the shutdown race
        # window, where the queued ``finished`` slot never ran on the
        # (blocked) GUI thread. Read as a plain Python attribute, so it
        # is safe to access even after the worker's C++ object has been
        # ``deleteLater``- d.
        self.client: Optional[MicroscopeClient] = None

    @Slot()
    def run(self) -> None:
        """Attempt to connect; emit the client (or None) on completion."""
        client: Optional[MicroscopeClient]
        try:
            client = MicroscopeClient()
        except Exception:
            logger.warning(
                "Could not import autoscript_sdb_microscope_client; "
                "AutoScript may not be installed",
                exc_info=True,
            )
            self.finished.emit(None)
            return

        try:
            client.connect()
        except Exception:
            logger.info(
                "Could not connect to microscope; "
                "will fall back to simulation",
                exc_info=True,
            )
            self.finished.emit(None)
            return

        self.client = client
        self.finished.emit(client)


class AppController(QObject):
    """Application-level coordinator exposed to QML as ``appController``."""

    # Notify signals — QML Property bindings refresh on these.
    connectionStatusChanged = Signal()
    isConnectedChanged = Signal()
    runningWorkflowIdChanged = Signal()

    def __init__(
        self,
        force_simulation: bool = False,
        parent: Optional[QObject] = None,
    ) -> None:
        super().__init__(parent)
        self._force_simulation: bool = force_simulation
        self._microscope: Optional[Union[MicroscopeClient, SimulatedMicroscopeClient]] = None
        self._connection_status: str = "Not connected"

        # Connection-thread state.
        self._connect_thread: Optional[QThread] = None
        self._connect_worker: Optional[_ConnectWorker] = None
        self._connect_in_progress: bool = False

        # Settings is created up front (before initialize()) so QML bindings
        # to appController.settings.* resolve immediately when main.qml loads.
        # Parented to self so Qt's ownership rules keep it alive for the
        # lifetime of the AppController.
        self._settings = SettingsController(parent=self)

        # Workflow runners. Created lazily after the microscope client
        # exists (in _on_connect_finished or in the simulation path of
        # initialize) — they need a microscope reference to construct
        # activities. None until then; QML bindings to .rtWorkflow /
        # .cpWorkflow must tolerate this. See
        # _create_microscope_dependent_services.
        self._rt_workflow: Optional[RTWorkflow] = None
        self._cp_workflow: Optional[CPWorkflow] = None

        # Stage positions controller. Like RTWorkflow, it needs the
        # microscope client to be present, so it's created in
        # _create_microscope_dependent_services after the connect resolves.
        self._stage_positions: Optional[StagePositionsController] = None

        # Stage / Scan page controller. Also microscope-dependent,
        # constructed alongside the stage_positions controller in
        # _create_microscope_dependent_services. Backs the manual-
        # assist Stage / Scan page (rotation, scan rotate 180°, Z slider).
        self._stage_scan: Optional[StageScanController] = None

        # Cryo activities controller. Unlike the workflow runners, it
        # does NOT need the microscope client to construct — its
        # responsibility is just managing the list of activity records
        # and persisting them. Workflow execution (which does need
        # the microscope) lives in CPWorkflow and reads this
        # controller's model when Start is clicked. Constructed
        # immediately so the QML Cryo page has its model from the
        # moment the engine loads.
        self._cryo_activities: CryoActivitiesController = (
            CryoActivitiesController(parent=self)
        )

        # Session log controller. Microscope-independent like
        # _cryo_activities — it owns the JSONL file at
        # <project_root>/session_logs/session_log.jsonl, the in-memory
        # list of past sessions, and the QAbstractListModel the
        # Session Log page binds to. Constructed immediately so the
        # page renders from disk even before the microscope connects.
        # Runner signals (workflowStarted / workflowFinished /
        # activityRecorded) are wired later in :meth:`_wire_workflow`,
        # once the workflow runners exist.
        self._session_log: SessionLog = SessionLog(parent=self)

        # Cross-workflow tracking. Empty string = nothing running.
        # Set by workflow signal handlers; read by QML page enable
        # bindings.
        self._running_workflow_id: str = ""

        # Keep a registry so we can iterate workflows for things like
        # "is anything running" without enumerating attributes.
        # Populated in _create_microscope_dependent_services.
        self._workflows: Dict[str, WorkflowRunner] = {}

    # --- QML-visible properties --------------------------------------------

    @Property(QObject, constant=True)
    def settings(self) -> SettingsController:
        """The user-preferences controller."""
        return self._settings

    @Property(QObject, notify=isConnectedChanged)
    def rtWorkflow(self) -> Optional[RTWorkflow]:
        """The RT Prep workflow runner.

        Returns ``None`` until the microscope client is constructed (real
        or simulated). After that, the same instance for the lifetime of
        the app. QML bindings should null-guard this:

            enabled: appController.rtWorkflow
                     ? appController.rtWorkflow.canStart
                     : false

        The notify is on ``isConnectedChanged`` because that's the
        transition that creates the runner; once created, the instance
        is constant.
        """
        return self._rt_workflow

    @Property(QObject, notify=isConnectedChanged)
    def cpWorkflow(self) -> Optional[CPWorkflow]:
        """The Cryo Prep workflow runner.

        Returns ``None`` until the microscope client is constructed
        (real or simulated). After that, the same instance for the
        lifetime of the app. QML bindings should null-guard, mirroring
        the ``rtWorkflow`` pattern:

            enabled: appController.cpWorkflow
                     ? appController.cpWorkflow.canStart
                     : false

        The notify is on ``isConnectedChanged`` because that's the
        transition that creates the runner; once created, the instance
        is constant.
        """
        return self._cp_workflow

    @Property(QObject, notify=isConnectedChanged)
    def stagePositions(self) -> Optional[StagePositionsController]:
        """The saved stage positions controller.

        Returns ``None`` until the microscope client is constructed
        (real or simulated). After that, the same instance for the
        lifetime of the app. QML bindings should null-guard, mirroring
        the ``rtWorkflow`` pattern.
        """
        return self._stage_positions

    @Property(QObject, notify=isConnectedChanged)
    def stageScan(self) -> Optional[StageScanController]:
        """The Stage / Scan page controller.

        Returns ``None`` until the microscope client is constructed
        (real or simulated). After that, the same instance for the
        lifetime of the app. QML bindings should null-guard, mirroring
        the ``rtWorkflow`` pattern.
        """
        return self._stage_scan

    @Property(QObject, constant=True)
    def cryoActivities(self) -> CryoActivitiesController:
        """The Cryo activities controller.

        Constant: the instance is created once in ``__init__`` and never
        replaced. Unlike ``rtWorkflow`` and ``stagePositions``, this
        controller doesn't need the microscope to function (it only
        manages the activity-list state), so it's available immediately
        and QML doesn't need to null-guard it.
        """
        return self._cryo_activities

    @Property(QObject, constant=True)
    def sessionLog(self) -> SessionLog:
        """The session log controller.

        Constant: created once in ``__init__`` and never replaced.
        Like :attr:`cryoActivities`, doesn't need the microscope to
        function — it owns the JSONL file, the in-memory list of
        past sessions, and the QAbstractListModel the Session Log
        page binds to. The page renders immediately on startup
        from whatever's on disk.

        Runner signals (workflowStarted / workflowFinished /
        activityRecorded) are wired in
        :meth:`_create_microscope_dependent_services` via
        :meth:`_wire_workflow`, after which new sessions and
        activities flow into the model as workflows run.
        """
        return self._session_log

    @Property(str, notify=connectionStatusChanged)
    def connectionStatus(self) -> str:
        """Short, human-readable connection status for the StatusBar."""
        return self._connection_status

    @Property(bool, notify=isConnectedChanged)
    def isConnected(self) -> bool:
        """True when a microscope client (real or simulated) is connected."""
        return self._microscope is not None and self._microscope.is_connected

    @Property(str, notify=runningWorkflowIdChanged)
    def runningWorkflowId(self) -> str:
        """ID of the currently-running workflow, or empty string if none.

        Used by QML pages for cross-page disable. A page enables itself
        when this is empty or matches its own page id. Page ids are
        kept aligned with the WORKFLOW_ID_* constants.
        """
        return self._running_workflow_id

    @Property(bool, notify=runningWorkflowIdChanged)
    def anyWorkflowRunning(self) -> bool:
        """True when any tracked workflow or operation is running.

        Derived from :attr:`runningWorkflowId`: empty string means
        nothing is running, anything else means something is. Used
        by the Settings page to disable workflow-affecting controls
        during a run (the snapshot in
        :class:`WorkflowSettingsSnapshot` is the load-bearing safety
        mechanism; this flag drives the UI lock as a clarity cue).

        Includes RT, CP, stage moves, and stage rotations — anything
        that gets registered with :meth:`_set_running_workflow_id`.
        """
        return bool(self._running_workflow_id)

    @Slot(str, result=bool)
    def is_page_blocked(self, page_id: str) -> bool:
        """True if a *different* page's workflow is running.

        Returns False when nothing is running, or when the running
        workflow is on the page asking. Pages call this to gate their
        ``enabled`` state.
        """
        if not self._running_workflow_id:
            return False
        return self._running_workflow_id != page_id

    # --- Lifecycle ---------------------------------------------------------

    @Slot()
    def initialize(self) -> None:
        """Set up the microscope client.

        Returns immediately when a real-connection attempt is dispatched —
        the actual connect runs on a worker thread and finalization happens
        in :meth:`_on_connect_finished`. The simulation path remains
        synchronous because there is no work to defer.

        Safe to call once. Repeat calls (or calls while a connect is in
        flight) log a warning and return without effect.
        """
        if self._microscope is not None or self._connect_in_progress:
            logger.warning("initialize() called twice; ignoring")
            return

        if self._force_simulation:
            logger.info("Starting in simulation mode (--simulation flag)")
            self._microscope = SimulatedMicroscopeClient()
            self._microscope.connect()
            self._set_status("Simulation mode")
            self._create_microscope_dependent_services()
            self.isConnectedChanged.emit()
            return

        # --- Threaded real-connection path ---
        self._set_status("Connecting to microscope...")
        self._connect_in_progress = True

        thread = QThread()
        worker = _ConnectWorker()
        worker.moveToThread(thread)

        thread.started.connect(worker.run)

        worker.finished.connect(self._on_connect_finished)
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(self._on_connect_thread_finished)

        self._connect_thread = thread
        self._connect_worker = worker
        thread.start()

    @Slot(object)
    def _on_connect_finished(self, client: Optional[MicroscopeClient]) -> None:
        """Receive the result of a threaded connect attempt.

        Runs on the GUI thread (signal is queued because the worker lives
        on the connect thread), so it's safe to mutate ``self._microscope``
        and emit notify signals here.
        """
        self._connect_in_progress = False
        # Don't null the thread/worker references here — see
        # _on_connect_thread_finished for why.

        if client is not None:
            self._microscope = client
            self._set_status(f"Connected")
        else:
            logger.info("Falling back to simulated microscope")
            self._microscope = SimulatedMicroscopeClient()
            self._microscope.connect()
            self._set_status("Microscope not available")

        self._create_microscope_dependent_services()

        self.isConnectedChanged.emit()

    @Slot()
    def _on_connect_thread_finished(self) -> None:
        """Null our Python references once the thread has actually exited."""
        self._connect_thread = None
        self._connect_worker = None

    @Slot()
    def shutdown(self) -> None:
        """Disconnect from the microscope. Called via app.aboutToQuit."""
        # Stop any running workflow before tearing down the microscope.
        # Workflows hold a reference to ops objects that hold a reference
        # to the SDB client; running them past disconnect would raise,
        # and disconnecting while a hardware op (sputter run, stage move,
        # GIS) is in flight risks an indeterminate state. So request stop
        # on each running workflow, then JOIN its worker thread before
        # disconnecting — mirroring the connect-thread / stage_scan join
        # pattern. (The cross-page lock means at most one runs, but we
        # iterate defensively.)
        running_workflows = [
            (wf_id, wf)
            for wf_id, wf in self._workflows.items()
            if wf.isRunning
        ]
        for wf_id, wf in running_workflows:
            logger.info("Shutdown: stopping workflow %r", wf_id)
            wf.stop()
        for wf_id, wf in running_workflows:
            if not wf.wait_for_stop():
                logger.warning(
                    "Shutdown: workflow %r worker did not exit in time; "
                    "proceeding with teardown (process exit will "
                    "terminate it)", wf_id,
                )

        # Stop any in-flight stage rotation. Same rationale: the
        # rotation worker holds ops references that would become
        # invalid post-disconnect.
        if self._stage_scan is not None:
            self._stage_scan.shutdown()

        if self._connect_thread is not None and self._connect_thread.isRunning():
            logger.info("Connect thread still running at shutdown; waiting briefly...")
            if not self._connect_thread.wait(1000):
                logger.warning(
                    "Connect thread did not finish in time; "
                    "process exit will terminate it"
                )

        # If a real connect completed during that wait, the queued
        # _on_connect_finished slot never ran on this (blocked) GUI
        # thread — QThread.wait() does not spin the event loop — so
        # self._microscope is still None even though the worker produced
        # a live client. Retrieve it directly from the worker and
        # disconnect it, rather than re-entering the event loop during
        # teardown.
        if self._microscope is None and self._connect_worker is not None:
            pending_client = getattr(self._connect_worker, "client", None)
            if pending_client is not None:
                logger.info(
                    "Shutdown: disconnecting microscope client produced "
                    "by an in-flight connect"
                )
                try:
                    pending_client.disconnect()
                except Exception:
                    logger.exception(
                        "Error disconnecting in-flight client during "
                        "shutdown (non-fatal)"
                    )

        if self._microscope is not None and self._microscope.is_connected:
            logger.info("Shutting down — disconnecting microscope")
            try:
                self._microscope.disconnect()
            except Exception:
                logger.exception("Error during shutdown (non-fatal)")

    # --- Workflow setup ----------------------------------------------------

    def _create_microscope_dependent_services(self) -> None:
        """Construct services that need the microscope client.

        Called once, on the GUI thread, after the microscope client has
        been created (real or simulated). Constructs:

        * :class:`StagePositionsController` — shared service used by
          the StagePositions UI component and by CPWorkflow's GIS
          Deposition activities to resolve position references.
        * :class:`RTWorkflow` — RT Prep page's workflow runner.
        * :class:`CPWorkflow` — Cryo Prep page's workflow runner.
          Depends on :attr:`_cryo_activities` (the activity list state),
          :attr:`_stage_positions` (for resolving GIS Deposition
          position references), and :attr:`_settings` (for PFIB
          restore, GIS port name, and the move-to-original toggle).
        * :class:`StageScanController` — Stage / Scan page controller.
          No dependencies on other services in this list; constructed
          last for ordering simplicity.

        Order matters here: stage positions must be constructed before
        any workflow runner that depends on it.
        """
        # Stage Positions — shared controller used by Cryo Prep activities
        # (GIS Deposition resolves position_id → coordinates here) and
        # the Stage Positions UI component on the Cryo page. Constructed
        # before any workflow runner that depends on it.
        self._stage_positions = StagePositionsController(
            microscope=self._microscope,
            parent=self,
        )
        # Stage Move uses the same running-id mechanism as workflow
        # runners — while a move is in flight, the global running id is
        # "stage_move" and every page's is_page_blocked() check returns
        # True (since their page ids don't match).
        self._stage_positions.moveStarted.connect(
            self._on_stage_move_started
        )
        self._stage_positions.moveFinished.connect(
            self._on_stage_move_finished
        )

        # Templates feature: wire the cryo activities controller's positions
        # lookup now that stage_positions exists. Templates are inert until
        # this is called.
        self._cryo_activities.set_positions_lookup(self._stage_positions)

        # RT Prep workflow runner.
        self._rt_workflow = RTWorkflow(
            microscope=self._microscope,
            settings=self._settings,
            parent=self,
        )
        self._wire_workflow(self._rt_workflow, WORKFLOW_ID_RT_PREP)

        # Cryo Prep workflow runner. Depends on stage positions
        # (constructed above) and on the cryo activities controller
        # (constructed eagerly in __init__ since it doesn't need the
        # microscope itself).
        self._cp_workflow = CPWorkflow(
            microscope=self._microscope,
            cryo_activities=self._cryo_activities,
            stage_positions=self._stage_positions,
            settings=self._settings,
            parent=self,
        )
        self._wire_workflow(self._cp_workflow, WORKFLOW_ID_CRYO_PREP)

        # Stage / Scan page controller. No dependencies on the workflows
        # or stage positions — purely a microscope-and-settings consumer.
        # Constructed last for ordering simplicity.
        #
        # The rotation operation claims the cross-page lock the same way
        # workflows do: rotationStarted → set running id, rotationFinished
        # → clear it. The scan-rotate-180 Slot is too fast to bother
        # locking, so it deliberately doesn't touch the lock.
        self._stage_scan = StageScanController(
            microscope=self._microscope,
            settings=self._settings,
            parent=self,
        )
        self._stage_scan.rotationStarted.connect(
            self._on_stage_rotation_started
        )
        self._stage_scan.rotationFinished.connect(
            self._on_stage_rotation_finished
        )

    def _wire_workflow(self, wf: WorkflowRunner, wf_id: str) -> None:
        """Connect a workflow's lifecycle signals.

        Two concerns share these closures:

        1. **Cross-page tracking**: set :attr:`runningWorkflowId` on
           start, clear it on finish. This is what gates page
           enable/disable.

        2. **Session logging**: forward the workflow lifecycle into
           :attr:`_session_log` so the running workflow appears in
           the Session Log page as it progresses and lands as a
           durable record in the JSONL file on disk.

        For (2), the workflow's :attr:`activityRecorded` signal goes
        straight to :meth:`SessionLog.on_activity_recorded`; the
        SessionLog routes each activity to its currently-open session.
        The total duration on finish comes from
        :meth:`WorkflowRunner.totalDuration`, kept off the
        :attr:`workflowFinished` payload so that signal stays narrow.

        Within each closure the cross-page lock is updated *first*,
        then the SessionLog call, so UI-lock observers see the state
        transition before any disk writing happens.
        """

        def _on_started() -> None:
            self._set_running_workflow_id(wf_id)
            self._session_log.on_workflow_started(wf_id)

        def _on_finished(all_complete: bool) -> None:
            # Only clear if we're the current owner. Defensive — in
            # principle two workflows shouldn't be running concurrently,
            # but if some future bug allowed it, we don't want one
            # workflow's finish to clear the running flag for another.
            if self._running_workflow_id == wf_id:
                self._set_running_workflow_id("")
            # Session log: read the total duration off the runner's
            # accessor and forward into on_workflow_finished. The
            # session log writes the SessionEndEntry, updates its
            # model row's outcome fields, and clears its
            # current-session pointer.
            self._session_log.on_workflow_finished(
                wf_id, all_complete, wf.totalDuration(),
            )

        wf.workflowStarted.connect(_on_started)
        wf.workflowFinished.connect(_on_finished)
        # Per-activity record event — feeds the SessionLog directly.
        # The runner's activityRecorded fires from
        # ``_on_activity_finished`` (after activityStatusChanged), so
        # SessionLog sees a consistent post-event state.
        wf.activityRecorded.connect(self._session_log.on_activity_recorded)
        self._workflows[wf_id] = wf

    def _set_running_workflow_id(self, wf_id: str) -> None:
        if self._running_workflow_id != wf_id:
            self._running_workflow_id = wf_id
            self.runningWorkflowIdChanged.emit()

    def _on_stage_move_started(self) -> None:
        self._set_running_workflow_id(WORKFLOW_ID_STAGE_MOVE)

    def _on_stage_move_finished(self, _success: bool) -> None:
        # Defensive — only clear if we're the current owner. Same
        # rationale as in _wire_workflow's _on_finished.
        if self._running_workflow_id == WORKFLOW_ID_STAGE_MOVE:
            self._set_running_workflow_id("")

    def _on_stage_rotation_started(self) -> None:
        self._set_running_workflow_id(WORKFLOW_ID_STAGE_ROTATION)

    def _on_stage_rotation_finished(self, _success: bool, _reason: str) -> None:
        # Defensive — only clear if we're the current owner. Same
        # rationale as in _wire_workflow's _on_finished. The reason
        # arg is logged inside the controller; we only need it
        # here to match the signal's signature.
        if self._running_workflow_id == WORKFLOW_ID_STAGE_ROTATION:
            self._set_running_workflow_id("")

    # --- Helpers -----------------------------------------------------------

    def _set_status(self, status: str) -> None:
        if self._connection_status != status:
            self._connection_status = status
            self.connectionStatusChanged.emit()