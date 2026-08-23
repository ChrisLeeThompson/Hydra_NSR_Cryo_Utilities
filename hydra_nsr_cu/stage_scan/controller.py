"""Stage / Scan page controller.

Manual-assist controller backing the Stage / Scan page. The page hosts
three direct user-gesture operations:

* **Stage rotation** (and its tilt-before / tilt-after variants).
  Multi-phase, threaded, claims the cross-page lock, and gated by a
  pre-start check pass (REFUSE / ASK_CONFIRM / PASS) before a worker
  is spawned — the same two-step Start pattern as the workflow runners.
* **Scan rotate SEM and FIB** to a canonical angle (0 or π rad) via
  :meth:`set_scan_rotation_to_0` / :meth:`set_scan_rotation_to_180`.
  Synchronous, no thread, no lock. The rotation worker's optional
  "scan rotate after" phase instead uses :meth:`_rotate_scan_by_180`,
  which adds π to each beam's current scan rotation (wrapped into
  [0, 2π)) to compensate for the 180° physical rotation.
* **Stage Z slider.** Streaming relative moves while the slider is
  held, driven by a long-lived worker on a 50 ms tick. Not gated by
  pre-start checks; a separate safety gate (:mod:`.z_safety_gate`)
  disables the slider while the stage is outside the safe range.

All SDK contact goes through the ops facades in
:mod:`hydra_nsr_cu.microscope`; the controller never reaches into the
underlying ``SdbMicroscopeClient``. Stage rotation is split into a pure
planner (:func:`_plan_rotation_phases`), a single-shot threaded worker
(:class:`_StageRotateWorker`), and this controller, which owns the
worker lifecycle and exposes ``isRotating`` plus the paired
``rotationStarted`` / ``rotationFinished`` signals to QML. There is no
state recording: operations are direct user gestures with no
side-effect bracketing.
"""
from __future__ import annotations

import logging
import math
import threading
from dataclasses import dataclass
from typing import Callable, FrozenSet, List, Optional, Type

from PySide6.QtCore import Property, QObject, QThread, Signal, Slot

from .. import defaults
from ..microscope import MicroscopeClientLike
from ..pre_start_checks import (
    PendingStart,
    PreStartCheck,
    gather_snapshot,
    run_pre_start_checks,
    to_dialog_items,
)
from ..pre_start_checks.checks import (
    StagePositionWithinSafeRangeCheck,
    describe_out_of_range,
    is_within_safe_range,
)
from ..settings.settings_controller import SettingsController
from .z_safety_gate import ZSafetyGate

logger = logging.getLogger(__name__)


# --- Angle helpers ------------------------------------------------------


def _wrap_scan_rotation_rad(angle_rad: float) -> float:
    """Wrap an angle into AutoScript's accepted scan-rotation domain [0, 2π).

    AutoScript's ``scanning.rotation`` rejects writes outside [0, 2π)
    with "specified value is out of range". Wrapping is lossless for a
    rotation (periodic in 2π), so canonicalizing here changes nothing
    physically.
    """
    wrapped = angle_rad % math.tau
    # Float edge: for a tiny negative input (e.g. -1e-20), Python's
    # modulo computes tau - 1e-20, which rounds to exactly math.tau —
    # outside [0, 2π). Guard it back to the canonical 0.0.
    if wrapped >= math.tau:
        wrapped = 0.0
    return wrapped


# --- Phase planning -----------------------------------------------------
#
# Stage rotation is decomposed into up to four phases, each of which
# maps to a single hardware operation. The planner is a pure function
# (no Qt, no microscope) so it can be exhaustively tested without
# threading or hardware mocks. The worker just iterates the planner's
# output and dispatches each phase to a small _execute_phase switch.

# Phase kinds. Module-level constants rather than an Enum keep the
# dataclass JSON-friendly if we ever want to log the plan as data.
PHASE_ZERO_TILT = "zero_tilt"
PHASE_ROTATE_180 = "rotate_180"
PHASE_TILT_AFTER = "tilt_after"
PHASE_SCAN_ROTATE_AFTER = "scan_rotate_after"

# Phases that emit a user-visible status message during normal
# (success-path) execution. PHASE_SCAN_ROTATE_AFTER is excluded
# because its underlying operation (two property writes) is
# near-instant; the outer "Stage rotation complete" covers it.
# Its friendly name is still produced by the controller's
# _format_phase_friendly for the failure path, so a phase that
# raises during scan-rotate still gets "Rotating scan failed"
# rather than a generic fallback.
_SUCCESS_VISIBLE_PHASES = frozenset({
    PHASE_ZERO_TILT, PHASE_ROTATE_180, PHASE_TILT_AFTER,
})


@dataclass(frozen=True)
class _RotatePhase:
    """A single phase of a stage rotation operation.

    ``kind`` is one of the ``PHASE_*`` constants above. ``tilt_rad``
    is used only by :data:`PHASE_TILT_AFTER` and is the absolute tilt
    value (in radians) to move to; for all other kinds it is ignored
    and left at its zero default.
    """
    kind: str
    tilt_rad: float = 0.0


def _plan_rotation_phases(
    *,
    zero_tilt_before: bool,
    scan_rotate_after: bool,
    tilt_after: bool,
    tilt_after_angle_deg: int,
) -> list[_RotatePhase]:
    """Build an ordered list of rotation phases from the user's settings.

    Pure function. The order matters:

    1. (optional) Zero the stage tilt — needed to clear specimen-stage
       interferences before rotating.
    2. (always) Rotate the stage by π radians around the rotation axis,
       compucentric.
    3. (optional) Tilt the stage to a user-chosen angle.
    4. (optional) Flip SEM and FIB scan rotation by π so post-rotation
       imaging matches pre-rotation visual orientation.

    The "rotate" phase is always present because it's the operation
    the user explicitly asked for; the surrounding phases are
    conveniences.
    """
    phases: list[_RotatePhase] = []
    if zero_tilt_before:
        phases.append(_RotatePhase(kind=PHASE_ZERO_TILT))
    phases.append(_RotatePhase(kind=PHASE_ROTATE_180))
    if tilt_after:
        phases.append(_RotatePhase(
            kind=PHASE_TILT_AFTER,
            tilt_rad=math.radians(tilt_after_angle_deg),
        ))
    if scan_rotate_after:
        phases.append(_RotatePhase(kind=PHASE_SCAN_ROTATE_AFTER))
    return phases


# --- Worker -------------------------------------------------------------


class _StageRotateWorker(QObject):
    """Worker that executes a rotation plan on a dedicated thread.

    Single-shot: lives on a :class:`QThread` for one rotation, then is
    destroyed via ``deleteLater`` once :attr:`finished` has been emitted.

    Stage moves are synchronous and can't be interrupted from another
    thread, so ``stop()`` takes effect at the next phase boundary, not
    mid-phase.

    ``phaseStarted(kind: str, tilt_rad: float)`` is emitted just before
    each phase begins (after the cancel check). The payload is
    structured rather than pre-formatted so user-facing strings stay in
    the controller; the queued connection delivers it on the GUI thread.

    ``finished(success: bool, reason: str)`` is ``(True, "")`` on
    completion, ``(False, "cancelled")`` on stop, or ``(False, "<phase>
    failed: <ex>")`` when a phase raises. The controller does not parse
    ``reason``; it exists for log/debug consumers.
    """

    phaseStarted = Signal(str, float)
    finished = Signal(bool, str)

    def __init__(
        self,
        microscope: MicroscopeClientLike,
        plan: list[_RotatePhase],
        rotate_scan_by_180: Callable[[], None],
        parent: Optional[QObject] = None,
    ) -> None:
        super().__init__(parent)
        self._microscope = microscope
        self._plan = plan
        # Callable, not the controller, so the worker doesn't reach
        # into a sibling object's private interface. The controller
        # owns the additive-delta logic; the worker just calls it.
        # See StageScanController._rotate_scan_by_180 for why this is
        # additive rather than absolute.
        self._rotate_scan_by_180 = rotate_scan_by_180
        self._stop_event = threading.Event()

    def stop(self) -> None:
        """Request that the rotation cancel at the next phase boundary."""
        self._stop_event.set()

    @Slot()
    def run(self) -> None:
        """Execute the rotation plan, phase by phase."""
        for phase in self._plan:
            if self._stop_event.is_set():
                logger.info(
                    "Rotation cancelled before phase %r", phase.kind,
                )
                self.finished.emit(False, "cancelled")
                return

            # Cross-thread queued signal: lands on the controller's
            # GUI-thread slot, where the kind/tilt_rad is formatted
            # into status-bar text. Emitted after the cancel check
            # so a cancelled-before-this-phase doesn't produce a
            # ghost "phase starting" message.
            self.phaseStarted.emit(phase.kind, phase.tilt_rad)

            logger.info("Rotation: starting phase %r", phase.kind)
            try:
                self._execute_phase(phase)
            except Exception as ex:
                logger.exception(
                    "Rotation: phase %r raised", phase.kind,
                )
                self.finished.emit(False, f"{phase.kind} failed: {ex}")
                return

        logger.info("Rotation: all phases complete")
        self.finished.emit(True, "")

    def _execute_phase(self, phase: _RotatePhase) -> None:
        """Dispatch a single phase to the appropriate microscope op."""
        stage = self._microscope.stage

        if phase.kind == PHASE_ZERO_TILT:
            stage.absolute_move(stage.make_position(t=0.0))
        elif phase.kind == PHASE_ROTATE_180:
            stage.relative_move(
                stage.make_position(r=math.pi),
                rotate_compucentric=True,
            )
        elif phase.kind == PHASE_TILT_AFTER:
            stage.absolute_move(stage.make_position(t=phase.tilt_rad))
        elif phase.kind == PHASE_SCAN_ROTATE_AFTER:
            # Reuses the controller's additive-delta helper. The
            # contract is "add π to both beams, wrapped into
            # [0, 2π)" — see StageScanController._rotate_scan_by_180.
            # Unconditional; any AutoScript exception fails the phase
            # normally.
            self._rotate_scan_by_180()
        else:
            raise ValueError(f"Unknown rotation phase kind: {phase.kind!r}")


# --- Z slider state and worker ----------------------------------------


class _SliderState:
    """Lock-protected wrapper around the Z slider's current integer value.

    The slider's value is mutated from the GUI thread (via the
    ``set_z_slider`` Slot) and read from the worker thread (once per
    tick). The lock keeps the read/write atomic — important on 32-bit
    platforms where Python integer assignment isn't necessarily atomic
    at the byte level.

    The fail-locked flag deliberately lives on the worker rather than
    here: only the worker reads or writes it, so it doesn't need
    cross-thread synchronization.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._value: int = 0

    def set(self, value: int) -> None:
        with self._lock:
            self._value = int(value)

    def get(self) -> int:
        with self._lock:
            return self._value


class _StageZWorker(QObject):
    """Long-lived worker that drives the Stage Z slider's streaming moves.

    Constructed and started in :meth:`StageScanController.__init__`,
    runs for the controller's lifetime, torn down via
    :meth:`StageScanController.shutdown`. One worker serves every
    Z-slider gesture in the session.

    On each tick (:data:`defaults.STAGE_Z_TICK_INTERVAL_S`) it reads the
    slider value from :class:`_SliderState` and, when non-zero, issues a
    relative ``z`` move of ``slider_value × STAGE_Z_STEP_SIZE_M`` with
    ``link_z_y=True`` (xT's "Link Z to Y" behavior). Idle ticks are
    used for the safe-range position poll.

    Any exception from ``relative_move`` fail-locks the worker: one
    :attr:`errorOccurred` emit, then no further moves until the slider
    returns to 0 (the user releases it). A persistent fault is therefore
    not retried while the slider is still held.
    """

    # One-shot per failure. The receiver should be a queued slot on
    # the GUI thread that emits ``statusUpdated`` on the controller.
    errorOccurred = Signal(str)

    # Stage X/Y (metres) read on the safe-range poll cadence. Queued to
    # the controller's ``_on_stage_position_read`` on the GUI thread.
    positionRead = Signal(float, float)

    def __init__(
        self,
        microscope: MicroscopeClientLike,
        slider_state: _SliderState,
        parent: Optional[QObject] = None,
    ) -> None:
        super().__init__(parent)
        self._microscope = microscope
        self._slider_state = slider_state
        self._stop_event = threading.Event()
        # Worker-thread-only state; not lock-protected.
        self._fail_locked: bool = False

        # Safe-range poll state. ``_poll_active`` / ``_poll_now`` are
        # thread-safe Events set from the GUI thread (via
        # ``set_poll_active``); ``_ticks_since_poll`` is worker-thread-only.
        self._poll_active = threading.Event()
        self._poll_now = threading.Event()
        self._ticks_since_poll: int = 0
        self._poll_every_ticks: int = max(
            1,
            round(
                defaults.STAGE_SAFE_RANGE_POLL_INTERVAL_S
                / defaults.STAGE_Z_TICK_INTERVAL_S
            ),
        )

    def stop(self) -> None:
        """Request the worker exit at its next tick boundary.

        Safe to call from any thread. Returns immediately; the worker
        may take up to one tick interval (plus the duration of an
        in-flight ``relative_move``) to actually exit.
        """
        self._stop_event.set()

    def set_poll_active(self, active: bool) -> None:
        """Enable or disable the safe-range position poll.

        Called from the GUI thread (``StageScanController`` on page
        activate/deactivate). Enabling also requests an immediate poll,
        so the gate reflects the current position within one tick of the
        page becoming active rather than after a full poll interval.
        Safe to call from any thread — it only sets/clears Events.
        """
        if active:
            self._poll_now.set()
            self._poll_active.set()
        else:
            self._poll_active.clear()

    def _maybe_poll_safe_range(self) -> None:
        """Emit stage X/Y for the safety gate, on the poll cadence.

        Called on idle ticks (slider at 0). A no-op unless polling is
        active. Emits immediately when an immediate poll was requested
        (page just activated), otherwise every ``_poll_every_ticks`` idle
        ticks. A read failure is logged and skipped — it does not
        fail-lock the Z mover, which only guards ``relative_move``.
        """
        if not self._poll_active.is_set():
            self._ticks_since_poll = 0
            return
        if self._poll_now.is_set():
            self._poll_now.clear()
        else:
            self._ticks_since_poll += 1
            if self._ticks_since_poll < self._poll_every_ticks:
                return
        self._ticks_since_poll = 0
        try:
            pos = self._microscope.stage.current_position
            self.positionRead.emit(float(pos.x), float(pos.y))
        except Exception:
            logger.exception("Stage Z: safe-range position poll failed")

    @Slot()
    def run(self) -> None:
        """Tick loop. Runs on the worker's thread until :meth:`stop` is called."""
        logger.info("Stage Z worker: starting loop")
        while not self._stop_event.is_set():
            # Interruptible sleep — returns True immediately if stop
            # was signaled, False after timeout. Either way, take one
            # iteration to handle the wake-up cleanly.
            if self._stop_event.wait(defaults.STAGE_Z_TICK_INTERVAL_S):
                break

            slider_value = self._slider_state.get()

            # Fail-lock recovery: the user has to release the slider
            # before we attempt any more moves. Once they do, the
            # next tick clears the lock silently — the slider's
            # responsiveness in xT serves as the "ready again"
            # feedback.
            if self._fail_locked:
                if slider_value == 0:
                    logger.info(
                        "Stage Z worker: slider returned to 0, "
                        "clearing fail-locked state",
                    )
                    self._fail_locked = False
                continue

            # Idle ticks (slider at 0) are the common case — most of
            # the time nobody is dragging the slider. There's no Z move
            # to make, so the idle tick is used to poll the stage
            # position for the safety gate (slower cadence, and only
            # while the page is active).
            if slider_value == 0:
                self._maybe_poll_safe_range()
                continue

            dz = slider_value * defaults.STAGE_Z_STEP_SIZE_M
            try:
                stage = self._microscope.stage
                stage.relative_move(
                    stage.make_position(z=dz),
                    link_z_y=True,
                )
            except Exception as ex:
                logger.exception("Stage Z: relative_move failed")
                self._fail_locked = True
                self.errorOccurred.emit(str(ex))

        logger.info("Stage Z worker: loop exited")


class StageScanController(QObject):
    """Stage / Scan page controller.

    Constructed in :meth:`AppController._create_microscope_dependent_services`
    after the microscope client exists. Exposed to QML as
    ``appController.stageScan``.

    Public surface:

    * :meth:`set_scan_rotation_to_0`, :meth:`set_scan_rotation_to_180` —
      explicit setters for the page's two scan-rotate buttons.
    * :meth:`start_rotation`, :meth:`stop` plus the ``isRotating``
      Property and the ``rotationStarted`` / ``rotationFinished``
      signals — the threaded stage rotation operation.
    * :meth:`respondToConfirmation` plus the
      ``preStartCheckRefused`` / ``preStartCheckNeedsConfirmation``
      signals — the two-step Start for rotation. See
      :meth:`start_rotation`.
    * :meth:`set_z_slider` — Z slider streaming-move dispatch.
    * :meth:`shutdown` — orderly teardown of all threaded operations.
    """

    # StatusBar text — same shape as :class:`StagePositionsController`
    # and the workflow runners. main.qml routes this through the
    # multiplexed StatusBar message slot.
    statusUpdated = Signal(str)

    # Notify signal for the ``isRotating`` Property. Consumers binding
    # to the Property pick up state changes through this.
    isRotatingChanged = Signal()

    # Notify signal for the ``zSafetyState`` Property (Z-slider safety
    # gate). Emitted only on an actual state transition — see
    # ``_mutate_gate_and_notify``.
    zSafetyStateChanged = Signal()

    # Notify signal for the ``zSafetyMessage`` Property — the distance-
    # aware out-of-range text shown in the lock overlay. Emitted only when
    # the message text actually changes (see ``_set_z_safety_message``).
    zSafetyMessageChanged = Signal()

    # Edge signals, paired with ``isRotating``:
    # ``rotationStarted`` fires when a rotation begins; the controller
    # has already set ``_is_rotating = True`` before emitting.
    # ``rotationFinished(success, reason)`` fires when a rotation ends;
    # the controller has already set ``_is_rotating = False`` before
    # emitting (so consumers see consistent post-state).
    #
    # The ``rotationStarted`` / ``rotationFinished`` pair is what
    # AppController wires to the cross-page running-id mechanism;
    # ``isRotating`` is what QML page bindings use for busy state.
    rotationStarted = Signal()
    rotationFinished = Signal(bool, str)

    # --- Pre-start check signals (consumed by main.qml's two dialogs) -----
    #
    # Same shape as the corresponding signals on
    # :class:`WorkflowRunner`. QML's main.qml hosts the two
    # ConfirmDialog instances and connects to whichever controller is
    # active. The payload in both cases is a list of
    # ``{"title", "message"}`` dicts, typically produced by
    # :func:`pre_start_checks.to_dialog_items`, assigned to
    # ``ConfirmDialog.checkItems`` by the QML handlers.

    # Hard-refuse dialog (dismiss only). Emitted alongside a
    # ``statusUpdated("Pre-start check failed")`` breadcrumb.
    preStartCheckRefused = Signal(list)

    # Soft-warning dialog (OK / Cancel). The user's response arrives
    # via :meth:`respondToConfirmation`; the controller holds the
    # rotation pending until then.
    preStartCheckNeedsConfirmation = Signal(list)

    def __init__(
        self,
        microscope: MicroscopeClientLike,
        settings: SettingsController,
        parent: Optional[QObject] = None,
    ) -> None:
        super().__init__(parent)
        self._microscope = microscope
        # Settings reference is needed by ``start_rotation`` to read
        # the four user-facing checkboxes/angle at the moment of the
        # click. Reading at click time (rather than caching at __init__)
        # means the user's most recent settings choices always apply.
        self._settings = settings

        # Rotation-state fields. None / False until a rotation starts;
        # set during ``_spawn_rotation_worker`` and cleared in
        # ``_on_rotate_finished`` / ``_on_rotate_thread_finished``.
        self._is_rotating: bool = False
        self._rotate_thread: Optional[QThread] = None
        self._rotate_worker: Optional[_StageRotateWorker] = None

        # Remembered friendly text of the last phase the worker
        # announced via phaseStarted. Set in
        # _on_rotation_phase_started, consumed by the failure branch
        # of _on_rotate_finished so the failure text shares
        # vocabulary with what the user just saw ("Tilting stage to
        # 17° failed" instead of "Stage rotation failed:
        # tilt_after failed: <ex>"). Cleared on every rotation end.
        self._current_phase_friendly: Optional[str] = None

        # --- Two-step Start state ---
        #
        # Populated by :meth:`start_rotation` when the pre-start
        # check pass returns ASK_CONFIRM; cleared on either branch of
        # :meth:`respondToConfirmation`. ``None`` at all other times.
        # Tracked separately from ``_is_rotating`` because a
        # pending-confirmation state is "neither idle nor rotating"
        # — the controller has committed to the check pass but not
        # yet to spawning a worker.
        self._pending_start: Optional[PendingStart] = None

        # Confirmed check types carried into the running rotation.
        # Populated on the accept path of :meth:`respondToConfirmation`
        # (or reset to empty for the PASS path of
        # :meth:`start_rotation`). The rotation has no mid-run
        # re-check today, so this field is populated correctly but
        # not consulted; kept for cross-surface parity with
        # :attr:`WorkflowRunner._confirmed_check_types`.
        self._confirmed_check_types: FrozenSet[Type[PreStartCheck]] = frozenset()

        # --- Z slider safety gate ---
        # Pure state machine (fail-closed: UNKNOWN until the first
        # position reading, then SAFE / BLOCKED / UNLOCKED). UNKNOWN keeps
        # the slider disabled without showing the lock overlay, so the
        # overlay doesn't flash before the first poll lands. Driven by
        # ``_on_stage_position_read`` (fed by the Z worker's idle-tick
        # poll) and by ``unlockZSlider``; QML binds the lock
        # overlay to ``zSafetyState``.
        self._z_safety_gate = ZSafetyGate()

        # Distance-aware out-of-range text for the lock overlay, kept in
        # sync with the gate by ``_on_stage_position_read``. Empty until
        # the first poll; QML shows a generic fallback in that window. Uses
        # the same ``describe_out_of_range`` wording as the rotate / Move
        # To confirm dialog so the two safety surfaces read identically.
        self._z_safety_message = ""

        # --- Z slider worker setup ---
        # The Z worker is long-lived: started here, runs for the
        # controller's lifetime, torn down in ``shutdown``. Idle ticks
        # (slider at 0 — the common case) are cheap, so eagerly
        # starting the thread costs nothing measurable.
        self._z_slider_state = _SliderState()
        self._z_thread = QThread()
        self._z_worker = _StageZWorker(
            microscope=self._microscope,
            slider_state=self._z_slider_state,
        )
        self._z_worker.moveToThread(self._z_thread)
        # ``run`` starts ticking when the thread starts; the worker
        # exits ``run`` only when ``shutdown`` sets its stop event.
        self._z_thread.started.connect(self._z_worker.run)
        # Cross-thread signal: the worker emits errorOccurred from its
        # own thread; the connection is queued because the controller
        # lives on the GUI thread. The slot then re-emits as
        # ``statusUpdated`` text.
        self._z_worker.errorOccurred.connect(self._on_z_error)
        # Queued (worker thread -> GUI thread): each safe-range poll
        # feeds the gate via ``_on_stage_position_read``.
        self._z_worker.positionRead.connect(self._on_stage_position_read)
        self._z_thread.start()

    # --- QML-visible properties -------------------------------------------

    @Property(bool, notify=isRotatingChanged)
    def isRotating(self) -> bool:
        """True while a stage rotation is in progress.

        Set to True before :attr:`rotationStarted` fires, and back to
        False before :attr:`rotationFinished` fires. QML page
        bindings use this for busy state (e.g., disabling the Rotate
        button while the worker runs).
        """
        return self._is_rotating

    @Property(str, notify=zSafetyStateChanged)
    def zSafetyState(self) -> str:
        """Z-slider safety gate state, as a string for QML binding.

        One of ``"SAFE"``, ``"BLOCKED"``, ``"UNLOCKED"``, or
        ``"UNKNOWN"`` (the names of :class:`ZSafetyState`). The Stage /
        Scan page binds the slider's ``enabled`` and the lock overlay's
        ``visible`` to this: the overlay shows only in ``"BLOCKED"``, and
        the slider is live only in ``"SAFE"`` / ``"UNLOCKED"``.
        ``"UNKNOWN"`` (no reading yet, before the first poll lands) keeps
        the slider fail-closed without showing the lock overlay -- so the
        overlay doesn't flash on first page activation. Exposed as a
        string rather than an int enum so the binding stays readable and
        independent of how the controller is registered with QML.
        """
        return self._z_safety_gate.state.name

    @Property(str, notify=zSafetyMessageChanged)
    def zSafetyMessage(self) -> str:
        """Distance-aware out-of-range text for the lock overlay.

        The same wording the rotate / Move To confirm dialog shows for the
        radial check (built by
        :func:`pre_start_checks.checks.describe_out_of_range`), updated
        live from the Z worker's safe-range poll. Empty until the first
        poll arrives (the page shows a generic fallback for that brief
        window). The overlay binds its message Label to this so the slider
        gate and the dialog name the out-of-range reason identically.
        """
        return self._z_safety_message

    # --- Scan rotate (explicit setters) -----------------------------------

    @Slot()
    def set_scan_rotation_to_0(self) -> None:
        """Set both SEM and FIB scan rotation to 0 radians.

        Unconditional — works regardless of starting state. Provides a
        recovery path when the chained "scan rotate after rotation"
        phase has produced a non-canonical read-back value (e.g. 2π,
        or 30° + 180° = 210°), or when the user has otherwise driven
        the beams out of a canonical pair.

        Synchronous. No thread, no lock — the operation is two
        property writes (microseconds), done before the next QML
        frame paints.
        """
        self._set_scan_rotation_absolute(0.0)

    @Slot()
    def set_scan_rotation_to_180(self) -> None:
        """Set both SEM and FIB scan rotation to π radians (180°).

        Unconditional — works regardless of starting state. Same role
        as :meth:`set_scan_rotation_to_0` but for the other canonical
        angle. See that method's docstring for the broader discussion.

        Synchronous, no thread, no lock.
        """
        self._set_scan_rotation_absolute(math.pi)

    def _set_scan_rotation_absolute(self, target_rad: float) -> None:
        """Write ``target_rad`` to both SEM and FIB scan rotation.

        Single-source-of-truth helper for both explicit setter Slots.
        Emits a ``statusUpdated`` message on completion so the user
        sees confirmation in the StatusBar.
        """
        logger.info(
            "Scan rotate: writing %.4f rad to both SEM and FIB", target_rad,
        )
        self._microscope.electron_beam.scan_rotation_rad = target_rad
        self._microscope.ion_beam.scan_rotation_rad = target_rad
        self.statusUpdated.emit(
            f"Scan rotated SEM and FIB to {math.degrees(target_rad):.0f}°"
        )

    def _rotate_scan_by_180(self) -> None:
        """Add π radians to both SEM and FIB scan rotation, wrapped to [0, 2π).

        Additive-delta helper used by the rotation worker's optional
        "scan rotate after rotation" chained phase. The contract is
        "compensate for the 180° physical stage rotation that just
        happened" — the imaging frame inverts on a 180° stage
        rotation, and adding 180° to scan rotation flips it back.

        The sum is wrapped into [0, 2π) via
        :func:`_wrap_scan_rotation_rad` before writing: AutoScript
        rejects values outside that range, and the common π starting
        point (after a prior flip or the "Scan Rotate to 180" button)
        would otherwise produce a raw target of 2π and fail the phase
        with "specified value is out of range". Wrapping is lossless
        for a rotation: π + π → 0, 30° + 180° → 210°.
        """
        sem_current = self._microscope.electron_beam.scan_rotation_rad
        fib_current = self._microscope.ion_beam.scan_rotation_rad
        sem_target = _wrap_scan_rotation_rad(sem_current + math.pi)
        fib_target = _wrap_scan_rotation_rad(fib_current + math.pi)
        logger.info(
            "Scan rotate +180°: SEM %.4f → %.4f rad; FIB %.4f → %.4f rad",
            sem_current, sem_target, fib_current, fib_target,
        )
        self._microscope.electron_beam.scan_rotation_rad = sem_target
        self._microscope.ion_beam.scan_rotation_rad = fib_target
        # No status emit here. PHASE_SCAN_ROTATE_AFTER is excluded
        # from _SUCCESS_VISIBLE_PHASES because this operation is
        # near-instant (two property writes); the outer "Stage
        # rotation complete" already covers the whole rotation. The
        # phase still has a friendly name in _format_phase_friendly,
        # so a failure here surfaces as "Rotating scan failed"
        # rather than a generic fallback.

    # --- Stage Z slider ---------------------------------------------------

    @Slot(int)
    def set_z_slider(self, value: int) -> None:
        """Update the Z slider's commanded value.

        Called from QML on every slider value change (typically many
        times per second while the user is dragging). The Slot itself
        only updates the lock-protected slider state; the worker
        thread polls the state on its tick interval and dispatches
        the actual ``relative_move`` call.

        ``value`` is an integer in ``AppConfig.qml``'s slider range
        (currently -25..25). The physical Z motion per tick is
        ``value × STAGE_Z_STEP_SIZE_M`` (currently 2.5 µm/tick). The
        slider snaps to 0 on release in QML, which is what eventually
        clears any fail-locked state — see :class:`_StageZWorker`.

        Not gated by pre-start checks — streaming relative-move
        semantics don't map cleanly to a gate-and-resume flow.
        """
        self._z_slider_state.set(value)

    @Slot()
    def unlockZSlider(self) -> None:
        """Acknowledge an out-of-range stage position to enable the slider.

        Called from the lock overlay's Unlock button. A no-op unless the
        gate is ``"BLOCKED"`` — acknowledging is meaningless when the
        stage is in range (see :meth:`ZSafetyGate.acknowledge`). The
        acknowledgement clears automatically when the stage returns to
        the safe range (RESET_ON_PASS), so a later excursion prompts
        again.
        """
        self._mutate_gate_and_notify(self._z_safety_gate.acknowledge)

    @Slot(bool)
    def setStagePageActive(self, active: bool) -> None:
        """Start/stop the safe-range poll based on the page's active state.

        The Stage / Scan page calls this with ``visible && enabled`` — so
        the position poll runs only while the page is on screen and not
        disabled by a running workflow. Enabling requests an immediate
        poll, so the gate reflects the real stage position within one
        worker tick (~``STAGE_Z_TICK_INTERVAL_S``) of activation rather
        than after a full poll interval. Until that first reading lands
        the gate reports ``UNKNOWN`` (slider disabled, no lock overlay) --
        the poll is serviced on the worker thread, not synchronously here.
        """
        self._z_worker.set_poll_active(active)

    # --- Stage rotation ---------------------------------------------------

    @Slot()
    def start_rotation(self) -> None:
        """Begin a stage rotation using the user's saved settings.

        Reads ``zeroTiltBeforeRotation``, ``scanRotateAfterRotation``,
        ``tiltAfterRotation``, and ``tiltAfterRotationAngleDeg`` from
        :class:`SettingsController` at the moment of the click.

        Runs the rotation pre-start check pass before spawning the
        worker:

        * REFUSE → emit :attr:`preStartCheckRefused`,
          ``statusUpdated("Pre-start check failed")``, return.
        * ASK_CONFIRM → emit :attr:`preStartCheckNeedsConfirmation`,
          stash a :class:`PendingStart`, return; QML calls back via
          :meth:`respondToConfirmation`.
        * PASS → :meth:`_spawn_rotation_worker` immediately.

        No-op (with a warning log) if a rotation is already in flight
        or a previous start is still pending confirmation.
        """
        # Guard against re-entry. Three flags need to be clear before
        # we can run the check pass:
        #   - _is_rotating is the logical "rotation in progress" flag,
        #     cleared in _on_rotate_finished before rotationFinished
        #     emits.
        #   - _rotate_thread is the thread-lifecycle flag, cleared
        #     later in _on_rotate_thread_finished after the thread's
        #     event loop has fully exited.
        # Between those two events there's a small window where
        # _is_rotating is False but _rotate_thread is still alive;
        # starting a new rotation in that window would overwrite the
        # thread reference and Qt would abort with "QThread: Destroyed
        # while thread is still running". The double check is
        # defensive against that race.
        #   - _pending_start is the two-step Start flag, populated
        #     when the check pass returns ASK_CONFIRM and cleared on
        #     either branch of respondToConfirmation. While set, the
        #     user has a confirmation dialog open; don't pile on.
        if self._is_rotating or self._rotate_thread is not None:
            logger.warning(
                "start_rotation called while already rotating; ignoring"
            )
            return

        if self._pending_start is not None:
            logger.warning(
                "start_rotation called while a previous start is "
                "pending confirmation; ignoring"
            )
            return

        # --- Pre-start check pass ---
        checks = self._rotation_pre_start_checks()
        try:
            snapshot = gather_snapshot(self._microscope)
            summary = run_pre_start_checks(checks, snapshot)
        except Exception:
            # gather_snapshot reads live hardware; a transient glitch must
            # not propagate out of this @Slot and leave the Rotate button
            # enabled with only a console traceback. Refuse with a
            # user-facing breadcrumb instead.
            logger.exception(
                "StageScan: pre-start hardware read failed; refusing rotation"
            )
            self.statusUpdated.emit("Pre-start check failed (hardware read)")
            return

        if summary.refused:
            items = to_dialog_items(summary.refused)
            self.preStartCheckRefused.emit(items)
            self.statusUpdated.emit("Pre-start check failed")
            logger.info(
                "StageScan: refusing rotation "
                "(%d pre-start check%s failed)",
                len(summary.refused),
                "" if len(summary.refused) == 1 else "s",
            )
            return

        if summary.needs_confirmation:
            items = to_dialog_items(summary.needs_confirmation)
            self.preStartCheckNeedsConfirmation.emit(items)
            self._pending_start = PendingStart(
                confirmed_check_types=summary.needs_confirmation_types,
            )
            logger.info(
                "StageScan: awaiting user confirmation "
                "(%d pre-start check%s ask for confirmation)",
                len(summary.needs_confirmation),
                "" if len(summary.needs_confirmation) == 1 else "s",
            )
            return

        # All clear — spawn directly. No types were confirmed because
        # none needed confirmation.
        self._confirmed_check_types = frozenset()
        self._spawn_rotation_worker()

    @Slot(bool)
    def respondToConfirmation(self, accepted: bool) -> None:
        """Resume or abort a rotation paused pending confirmation.

        Called by QML when the user clicks OK (``accepted=True``) or
        Cancel (``accepted=False``) on the confirm dialog driven by
        :attr:`preStartCheckNeedsConfirmation`.

        Accept path: records the confirmed check types in
        :attr:`_confirmed_check_types` (parity with the workflow
        runners; the rotation has no mid-run re-check) and calls
        :meth:`_spawn_rotation_worker`.

        Reject path: emits ``"Stage rotation cancelled"`` — the same
        text :meth:`_on_rotate_finished` uses for a mid-rotation Stop,
        since either way the rotation didn't run to completion — and
        clears the pending state.

        Defensive no-op if no start is pending (e.g. a stale dialog
        dismiss arriving after a separate state change).
        """
        pending = self._pending_start
        self._pending_start = None
        if pending is None:
            logger.warning(
                "respondToConfirmation called with no pending "
                "start; ignoring",
            )
            return

        if not accepted:
            logger.info(
                "StageScan: pre-start confirmation cancelled by user"
            )
            self.statusUpdated.emit("Stage rotation cancelled")
            return

        self._confirmed_check_types = pending.confirmed_check_types
        logger.info(
            "StageScan: pre-start confirmation accepted; "
            "spawning rotation worker "
            "(%d check type(s) confirmed)",
            len(self._confirmed_check_types),
        )
        self._spawn_rotation_worker()

    def _spawn_rotation_worker(self) -> None:
        """Construct and start the rotation thread and worker.

        Called from :meth:`start_rotation` on the PASS path and from
        :meth:`respondToConfirmation` on the accept path. Reads the
        rotation settings, builds the plan, sets up the worker /
        thread / signal wiring, flips :attr:`_is_rotating` to True,
        emits :attr:`rotationStarted`, and starts the thread.

        Pre-condition: ``self._is_rotating is False``,
        ``self._rotate_thread is None``, and
        ``self._pending_start is None``.
        """
        plan = _plan_rotation_phases(
            zero_tilt_before=self._settings.zeroTiltBeforeRotation,
            scan_rotate_after=self._settings.scanRotateAfterRotation,
            tilt_after=self._settings.tiltAfterRotation,
            tilt_after_angle_deg=self._settings.tiltAfterRotationAngleDeg,
        )
        logger.info(
            "Stage rotation: starting plan with %d phase(s): %s",
            len(plan), [p.kind for p in plan],
        )

        # --- Threaded worker setup ---
        # Mirrors the AppController._ConnectWorker pattern: worker
        # lives on its own QThread, is destroyed via deleteLater after
        # finished, and the GUI-thread receivers handle the lock and
        # status text.
        thread = QThread()
        worker = _StageRotateWorker(
            microscope=self._microscope,
            plan=plan,
            rotate_scan_by_180=self._rotate_scan_by_180,
        )
        worker.moveToThread(thread)

        thread.started.connect(worker.run)

        worker.finished.connect(self._on_rotate_finished)
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        # Cross-thread queued connection — the worker thread emits;
        # the slot runs on the GUI thread to format the status text.
        # Auto connection type picks queued because the worker is
        # already on its own thread by the time we connect here.
        worker.phaseStarted.connect(self._on_rotation_phase_started)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(self._on_rotate_thread_finished)

        self._rotate_thread = thread
        self._rotate_worker = worker

        # State flips and start signal fire before the thread runs
        # so consumers see a consistent "rotation has begun" state by
        # the time any worker code executes.
        #
        # No initial "Rotating stage..." statusUpdated emit here:
        # the worker emits phaseStarted for each phase (including
        # PHASE_ROTATE_180) as it begins, and that path is the
        # single source of truth for per-phase status text. The
        # cross-thread queued signal latency is single-digit ms,
        # well below human flicker perception.
        self._set_is_rotating(True)
        self.rotationStarted.emit()

        thread.start()

    def _rotation_pre_start_checks(self) -> List[PreStartCheck]:
        """Checks gathered for a rotation gesture.

        Today: a single :class:`StagePositionWithinSafeRangeCheck`.
        Future rotation-specific checks would be added here. Method
        form rather than a module-level constant mirrors the
        ``@classmethod pre_start_checks()`` pattern on activity
        classes — keeps the dispatch site uniform across the
        codebase, and leaves room for a future per-instance check
        without changing the call shape.
        """
        return [StagePositionWithinSafeRangeCheck()]

    @Slot()
    def stop(self) -> None:
        """Request that an in-progress rotation cancel at the next phase.

        No UI button currently calls this; the Slot exists so the
        public surface is stable and a future Stop button (or a
        programmatic test) can use it without controller changes.

        Stage moves are uninterruptible mid-phase, so this returns
        immediately but the rotation continues until the current
        phase completes; the next-phase guard then sees the stop
        event and emits ``rotationFinished(False, "cancelled")``.
        """
        if self._rotate_worker is not None:
            logger.info("stop(): requesting rotation cancel")
            self._rotate_worker.stop()

    @Slot()
    def shutdown(self) -> None:
        """Stop any in-flight rotation and the Z worker, waiting briefly.

        Called by :meth:`AppController.shutdown` before the microscope
        client disconnects. The 1-second wait per worker matches the
        connect-thread shutdown pattern; if a stage move is mid-flight
        on real hardware (where moves can take ~10s), the wait will
        time out and the process exit will terminate the thread.
        That's acceptable — the user is quitting the app, not asking
        for a graceful rollback.

        Order: rotation first (it might be mid-phase on a slow move),
        Z worker second (its 50 ms tick almost always finishes well
        within the timeout).
        """
        if self._rotate_thread is not None and self._rotate_thread.isRunning():
            logger.info("Shutdown: stopping rotation worker")
            if self._rotate_worker is not None:
                self._rotate_worker.stop()
            # quit() directly (mirroring the Z-worker path below) rather
            # than relying on the queued worker.finished -> thread.quit
            # connection: the GUI thread is blocked in wait() here and
            # can't deliver that queued slot, so without a direct quit()
            # the event loop never returns and wait() always times out
            # (a spurious "did not finish" warning + a deferred
            # deleteLater). quit() only takes effect once run() has
            # returned, so it never severs an in-flight phase.
            self._rotate_thread.quit()
            if not self._rotate_thread.wait(1000):
                logger.warning(
                    "Rotate thread did not finish in time; "
                    "process exit will terminate it"
                )

        if self._z_thread.isRunning():
            logger.info("Shutdown: stopping Z worker")
            self._z_worker.stop()
            # The worker checks stop_event between ticks; quit() makes
            # the thread's event loop return once the worker's run()
            # method exits.
            self._z_thread.quit()
            if not self._z_thread.wait(1000):
                logger.warning(
                    "Z thread did not finish in time; "
                    "process exit will terminate it"
                )

    # --- Internal helpers and Slot handlers --------------------------------

    def _set_is_rotating(self, value: bool) -> None:
        """Update the backing field and fire the notify signal on change."""
        if self._is_rotating != value:
            self._is_rotating = value
            self.isRotatingChanged.emit()

    def _format_phase_friendly(self, kind: str, tilt_rad: float) -> str:
        """Bare user-facing name for a rotation phase.

        Returns text *without* a trailing ellipsis or " failed"
        suffix, so the caller can compose either:

        * ``f"{friendly}..."`` for the success-path running state
          (e.g. ``"Tilting stage to 17°..."``).
        * ``f"{friendly} failed"`` for the failure-path terminal
          state (e.g. ``"Tilting stage to 17° failed"``).

        The PHASE_TILT_AFTER branch rounds the radians-to-degrees
        conversion to defend against any float edge cases at the
        .5 boundary — for the integer-degree settings the planner
        builds from, the round-trip is exact, so the round() is
        belt-and-braces.

        The defensive ``"Stage rotation"`` fallback handles any
        future phase kind added without a matching branch.
        """
        if kind == PHASE_ZERO_TILT:
            return "Tilting stage to 0°"
        if kind == PHASE_ROTATE_180:
            return "Rotating stage"
        if kind == PHASE_TILT_AFTER:
            return f"Tilting stage to {round(math.degrees(tilt_rad))}°"
        if kind == PHASE_SCAN_ROTATE_AFTER:
            # Used only on the failure path — see _SUCCESS_VISIBLE_PHASES.
            return "Rotating scan"
        return "Stage rotation"

    @Slot(str, float)
    def _on_rotation_phase_started(self, kind: str, tilt_rad: float) -> None:
        """GUI-thread receiver for the worker's ``phaseStarted`` signal.

        Always stores the friendly phase text in
        :attr:`_current_phase_friendly` — the failure branch of
        :meth:`_on_rotate_finished` needs it whether or not the
        phase emits during the success path.

        Emits ``statusUpdated`` (with a trailing ellipsis to signal
        an in-flight indeterminate operation) only for phases listed
        in :data:`_SUCCESS_VISIBLE_PHASES`. PHASE_SCAN_ROTATE_AFTER
        is silent during success per its near-instant nature.
        """
        friendly = self._format_phase_friendly(kind, tilt_rad)
        self._current_phase_friendly = friendly
        if kind in _SUCCESS_VISIBLE_PHASES:
            self.statusUpdated.emit(f"{friendly}...")

    @Slot(bool, str)
    def _on_rotate_finished(self, success: bool, reason: str) -> None:
        """GUI-thread receiver for the worker's ``finished`` signal.

        Order matters here: ``_set_is_rotating(False)``
        runs *before* ``rotationFinished.emit(...)``, so any consumer
        that reads ``isRotating`` from a ``rotationFinished`` slot
        sees the post-rotation state, not a stale True.

        Failure-message convention: the worker's ``reason`` payload
        encodes the phase kind and exception text for log/debug
        consumers, but the user-facing text uses
        :attr:`_current_phase_friendly` (set by
        :meth:`_on_rotation_phase_started`) so the failure message
        shares vocabulary with the running-state message the user
        just saw. Exception detail lives in the worker's
        ``logger.exception`` line — short status bar, console log
        for the why.
        """
        self._set_is_rotating(False)

        if success:
            self.statusUpdated.emit("Stage rotation complete")
        elif reason == "cancelled":
            self.statusUpdated.emit("Stage rotation cancelled")
        else:
            phase_text = self._current_phase_friendly or "Stage rotation"
            self.statusUpdated.emit(f"{phase_text} failed")

        self._current_phase_friendly = None
        self.rotationFinished.emit(success, reason)

    @Slot()
    def _on_rotate_thread_finished(self) -> None:
        """Null Python references once the thread has actually exited.

        Mirrors :meth:`AppController._on_connect_thread_finished` — we
        wait for the thread to be fully done before dropping our
        references, to avoid a dangling worker that's still running
        when its owning thread reference goes out of scope.
        """
        self._rotate_thread = None
        self._rotate_worker = None

    @Slot(str)
    def _on_z_error(self, message: str) -> None:
        """GUI-thread receiver for the Z worker's ``errorOccurred`` signal.

        Re-emits as a short ``statusUpdated`` text — the user sees a
        "Stage Z error (see console log)" message in the StatusBar.
        Full exception detail is in the worker's ``logger.exception``
        line at the point of failure; the worker has also already set
        its own ``_fail_locked`` flag by the time this slot runs, so
        recovery happens silently when the user releases the slider.

        Short status bar, console log for the why — same convention
        as :meth:`_on_rotate_finished`'s failure branch.
        """
        logger.warning("Stage Z worker reported error: %s", message)
        self.statusUpdated.emit("Stage Z error (see console log)")

    @Slot(float, float)
    def _on_stage_position_read(
        self, stage_x_m: float, stage_y_m: float
    ) -> None:
        """GUI-thread receiver for the Z worker's safe-range poll.

        The worker reads ``stage.current_position`` on its idle ticks and
        emits the X / Y here via a queued connection. This feeds the
        radial check into the gate and emits ``zSafetyStateChanged`` only
        if the gate's state actually changed.

        The pass/fail definition is the shared
        :func:`pre_start_checks.checks.is_within_safe_range`, so the
        slider gate and the rotate / Move To pre-start gate agree on what
        "within safe range" means.
        """
        in_range = is_within_safe_range(stage_x_m, stage_y_m)
        self._mutate_gate_and_notify(
            lambda: self._z_safety_gate.observe_safe_range(in_range)
        )
        # Keep the overlay's distance text current. Only meaningful when
        # the stage is actually out of range (the overlay is shown by QML
        # only while the gate is BLOCKED); when in range, clear it so
        # zSafetyMessage never holds a stale "out of range" string.
        # Reuses the in_range result already computed for the gate, and
        # _set_z_safety_message emits only on an actual text change so
        # safe-range polls don't churn the binding.
        self._set_z_safety_message(
            "" if in_range else describe_out_of_range(stage_x_m, stage_y_m)
        )

    def _set_z_safety_message(self, message: str) -> None:
        """Update ``zSafetyMessage``, emitting only on a real change.

        Mirrors the notify discipline of :meth:`_mutate_gate_and_notify`:
        the safe-range poll re-runs at a fixed cadence, but the bound
        Label should re-evaluate only when the text actually differs.
        """
        if message != self._z_safety_message:
            self._z_safety_message = message
            self.zSafetyMessageChanged.emit()

    def _mutate_gate_and_notify(self, mutate: Callable[[], None]) -> None:
        """Apply a gate mutation and emit ``zSafetyStateChanged`` on change.

        Captures the state, runs ``mutate`` (an ``observe_safe_range`` or
        ``acknowledge`` call), and emits only if the resulting state
        differs — so QML bindings re-evaluate on real transitions, not on
        every poll tick that re-confirms the same state.
        """
        previous = self._z_safety_gate.state
        mutate()
        if self._z_safety_gate.state is not previous:
            self.zSafetyStateChanged.emit()