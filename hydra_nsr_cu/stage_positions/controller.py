"""Stage Positions controller — QML-facing surface for saved positions.

Owns the :class:`StagePositionsModel`, the JSON persistence path, and
the worker-thread machinery for the Move To operation. Exposes
high-level operations to QML (``add_from_current``,
``update_from_current``, etc.) that combine reading the live stage
position with model mutation. QML never reads the stage directly —
all coordinate-producing operations go through this controller.

Saving or updating a position also records the xT holder selected at
that moment. A holder outside ``defaults.SUPPORTED_HOLDERS`` refuses
the save (``holderNotSupported``); a failed holder read saves the
position without one.

``move_to(index)`` runs the move on a short-lived :class:`QThread`
worker; the GUI thread observes it through ``isMoving``,
``moveStarted``, ``moveFinished``, and ``statusUpdated``. The move is a
staged move with arrival checks (see ``microscope/stage_moves.py``),
staged or not per the Zero Tilt Before Stage Moves setting read live at
dispatch. When the position has a recorded holder the worker selects it
— while the stage is flat when the move is staged — and leaves it
selected afterwards. A failed move is logged with a full traceback and
surfaced as ``moveFinished(success=False, name, reason)``; the message
itself is not exposed — QML opens a generic "Stage Move Error" dialog,
or its shortfall variant when the stage stopped short, pointing to the
console log.

A Move To is gated by the pre-start check system (today a single
:class:`StagePositionWithinSafeRangeCheck`), mirroring stage rotation
in :class:`hydra_nsr_cu.stage_scan.controller.StageScanController`:
REFUSE emits :attr:`preStartCheckRefused`; ASK_CONFIRM emits
:attr:`preStartCheckNeedsConfirmation` and stashes a
:class:`_PendingMove` until :meth:`respondToConfirmation`; PASS spawns
the worker directly. The QML intent dialog ("Move the stage to X?")
stays in place, so on the override path the user sees the intent
confirm and then the safety acknowledgement — two dialogs guarding
different failure modes (accidental click vs. unusual current
position).
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Dict, FrozenSet, List, Optional, Tuple, Type

from PySide6.QtCore import (
    Property,
    QObject,
    QSettings,
    QThread,
    Signal,
    Slot,
)

from .. import defaults
from ..microscope import MicroscopeClientLike
from ..microscope.holder import (
    SHORTFALL_HOLDER_HINT,
    HolderSwitchError,
    is_supported_holder,
    select_holder,
)
from ..microscope.stage_moves import (
    StageMoveShortfall,
    StageTarget,
    staged_move,
    tilt_to_zero,
)
from ..pre_start_checks import (
    PreStartCheck,
    gather_snapshot,
    run_pre_start_checks,
    to_dialog_items,
)
from ..pre_start_checks.checks import StagePositionWithinSafeRangeCheck
from ..settings.settings_controller import SettingsController
from ..status_text import SEE_LOG, shorten
from .positions_model import (
    SavedStagePosition,
    StagePositionsModel,
    _new_id,
)

logger = logging.getLogger(__name__)


# QSettings key for the persisted positions list.
_K_POSITIONS_LIST = defaults.SCHEMA_PREFIX + "stagePositions/list"


@dataclass(frozen=True)
class _PendingMove:
    """Resume state for a Move To paused at the ASK_CONFIRM gate.

    Populated by :meth:`StagePositionsController.move_to` when the
    pre-start check pass produces ASK_CONFIRM; consumed (and cleared)
    on either branch of
    :meth:`StagePositionsController.respondToConfirmation`. Like
    :class:`hydra_nsr_cu.pre_start_checks.PendingStart` but also
    carries the already-resolved move target, so the accept path calls
    :meth:`_spawn_move_worker` without re-walking the positions model.
    Frozen so the resume path can't mutate the captured state.

    Attributes:
        target: The resolved :class:`StageTarget` for the staged move.
        position_name: User-visible name, used in status messages.
        position_id: Opaque id of the saved row, used only in logs.
        holder: Holder name to select before the move: the holder the
            position was saved with ("" = not recorded, leave the
            holder alone).
        confirmed_check_types: Check types the user is being asked to
            confirm; carried into
            :attr:`StagePositionsController._confirmed_check_types` on
            accept (populated for parity, not consulted mid-run).
    """
    target: StageTarget
    position_name: str
    position_id: str
    holder: str = ""
    confirmed_check_types: FrozenSet[Type[PreStartCheck]] = frozenset()


class _StageMoveWorker(QObject):
    """Worker that performs one Move To on a worker thread.

    Single-shot — destroyed via ``deleteLater`` once :attr:`finished`
    has been emitted. Mirrors the lifecycle of :class:`_ConnectWorker`
    in :mod:`app_controller`.

    The move is a staged move with arrival checks (see
    ``microscope/stage_moves.py``). When ``holder`` is set (the holder
    the position was saved with) the worker selects it before moving —
    after tilting the stage flat under the current holder when the move
    is staged, so the holder changes only while the stage is flat — and
    leaves it selected afterwards. Status text is emitted per stage;
    the controller decides what to emit on completion.
    """

    # finished(success, position_name, reason). ``reason`` is "" on
    # success, "shortfall" when an arrival check failed, "holder" when
    # the position's holder could not be selected, "error" otherwise;
    # the traceback is logged inside run() and the message is not
    # propagated because QML's response is a dialog pointing to the
    # console log.
    finished = Signal(bool, str, str)

    # statusUpdated(text). One line per stage of the move.
    statusUpdated = Signal(str)

    def __init__(
        self,
        stage_ops,
        target: StageTarget,
        position_name: str,
        *,
        staged: bool,
        holder: str,
    ) -> None:
        super().__init__()
        self._stage = stage_ops
        self._target = target
        self._position_name = position_name
        self._staged = bool(staged)
        self._holder = str(holder)

    def _say(self, text: str) -> None:
        self.statusUpdated.emit(shorten(text))

    @Slot()
    def run(self) -> None:
        """Tilt flat (staged), select the position's holder, then move."""
        success = False
        reason = "error"
        try:
            logger.info(
                "Stage move worker: moving to %r (staged=%s, holder=%r)",
                self._target, self._staged, self._holder,
            )
            if self._holder:
                # Leave under the current holder, arrive under the
                # position's: staged, the stage tilts flat first and
                # the staged move's own tilt stage is then skipped.
                if self._staged:
                    tilt_to_zero(self._stage, on_status=self._say)
                try:
                    select_holder(
                        self._stage, self._holder, on_status=self._say,
                    )
                except HolderSwitchError:
                    raise
                except Exception as exc:
                    # A refused name or a server error reads like a
                    # selection that did not take effect.
                    raise HolderSwitchError(
                        f"holder {self._holder!r} could not be selected"
                    ) from exc
            staged_move(
                self._stage, self._target, staged=self._staged,
                name=self._position_name, on_status=self._say,
            )
            logger.info("Stage move worker: move complete")
            success, reason = True, ""
        except StageMoveShortfall:
            logger.exception("Stage move worker: stage stopped short")
            logger.warning("Stage move worker: %s", SHORTFALL_HOLDER_HINT)
            reason = "shortfall"
        except HolderSwitchError:
            logger.exception(
                "Stage move worker: holder %r was not selected", self._holder,
            )
            reason = "holder"
        except Exception:
            logger.exception("Stage move worker: move raised")
        self.finished.emit(success, self._position_name, reason)


class StagePositionsController(QObject):
    """Saved stage positions, exposed to QML as ``appController.stagePositions``."""

    # Emitted as the move starts and finishes. AppController listens to
    # these to update its global runningWorkflowId.
    moveStarted = Signal()
    # moveFinished(success, position_name, reason). ``reason`` is "",
    # "shortfall", "holder" or "error". On failure QML opens its error
    # dialog (the shortfall and holder variants name the position or
    # holder); on success it just clears the activity state to idle.
    moveFinished = Signal(bool, str, str)
    # holderNotSupported(holder). Add / Update Position refused because
    # the selected holder is not in defaults.SUPPORTED_HOLDERS; QML
    # opens the "Holder Not Supported" dialog naming it.
    holderNotSupported = Signal(str)

    # Status text for the StatusBar — same shape as WorkflowRunner.
    statusUpdated = Signal(str)

    # Notify signal for the isMoving Property.
    isMovingChanged = Signal()

    # Pre-start check signals — mirror the
    # :class:`StageScanController` and :class:`WorkflowRunner`
    # surfaces. QML's pre-start dialog wiring in main.qml routes
    # both into the shared confirm/refuse dialogs and calls back
    # via :meth:`respondToConfirmation`. The single check gathered
    # for a Move To today is :class:`StagePositionWithinSafeRangeCheck`,
    # which is ASK_CONFIRM-only, so no REFUSE is reachable today;
    # the signal is wired in advance to make adding a REFUSE
    # check later a one-line change.
    preStartCheckRefused = Signal(list)
    preStartCheckNeedsConfirmation = Signal(list)

    def __init__(
        self,
        microscope: MicroscopeClientLike,
        settings: SettingsController,
        parent: Optional[QObject] = None,
    ) -> None:
        super().__init__(parent)
        self._microscope = microscope
        # Read live at dispatch time (like the Stage / Scan rotation
        # reads its settings): Zero Tilt Before Stage Moves decides
        # whether a Move To is staged.
        self._settings = settings

        self._model = StagePositionsModel(parent=self)

        self._qs = QSettings()

        # Move-thread state. Mirrors AppController._ConnectWorker — see
        # there for detailed comments on why we don't null these
        # references in the finished slot (deleteLater chain runs after,
        # and Python GC racing the C++ destruction is a real risk).
        self._move_thread: Optional[QThread] = None
        self._move_worker: Optional[_StageMoveWorker] = None
        self._is_moving: bool = False

        # --- Two-step Start state for the pre-start check protocol ---
        #
        # ``_pending_move`` is populated by :meth:`move_to` when the
        # pre-start check pass returns ASK_CONFIRM; cleared on
        # either branch of :meth:`respondToConfirmation`. ``None``
        # at all other times. Tracked separately from ``_is_moving``
        # because a pending-confirmation state is "neither idle nor
        # moving" — the controller has committed to the check pass
        # but not yet to spawning the worker.
        #
        # ``_confirmed_check_types`` is the post-accept carry of the
        # check types confirmed by the user. The move has no mid-run
        # re-check today, so this field is populated but not
        # consulted; kept for cross-surface parity with
        # :attr:`StageScanController._confirmed_check_types` and
        # :attr:`WorkflowRunner._confirmed_check_types`.
        self._pending_move: Optional[_PendingMove] = None
        self._confirmed_check_types: FrozenSet[Type[PreStartCheck]] = frozenset()

        self._load_from_settings()

    # --- QML-visible properties --------------------------------------------

    @Property(QObject, constant=True)
    def model(self) -> StagePositionsModel:
        """The list model. Bound to the QML ListView's ``model`` property."""
        return self._model

    @Property(bool, notify=isMovingChanged)
    def isMoving(self) -> bool:
        """True while a Move To is in flight."""
        return self._is_moving

    @Property(str, constant=True)
    def supportedHoldersText(self) -> str:
        """The supported holder names, comma-separated, for dialog text."""
        return ", ".join(defaults.SUPPORTED_HOLDERS)

    # --- Holder capture -----------------------------------------------------

    def _read_holder(self) -> Optional[str]:
        """The holder selected in xT, or None when it cannot be read."""
        try:
            return str(self._microscope.stage.holder)
        except Exception:
            logger.exception("Stage positions: failed to read the holder")
            return None

    def _refuse_unsupported(self, holder: Optional[str]) -> bool:
        """Emit :attr:`holderNotSupported` and return True for a bad holder.

        A failed read (``None``) is not a refusal: the position is then
        saved without a holder.
        """
        if holder is None or is_supported_holder(holder):
            return False
        logger.warning(
            "Stage positions: holder %r is not supported; position not saved",
            holder,
        )
        self.holderNotSupported.emit(holder)
        return True

    @Slot(result=str)
    def unsupported_holder(self) -> str:
        """The selected holder when a position cannot be saved with it.

        QML calls this before opening the Add Position name dialog or
        the Update Position confirmation, so the operator sees "Holder
        Not Supported" first. "" when the holder is supported or cannot
        be read (the save itself then records no holder).
        """
        holder = self._read_holder()
        if holder is None or is_supported_holder(holder):
            return ""
        return holder

    # --- Add / update / rename / remove (synchronous) ----------------------

    @Slot(str, result=bool)
    def add_from_current(self, name: str) -> bool:
        """Add the current stage position and holder as a new saved row."""
        name = name.strip()
        if not name:
            logger.warning("Stage positions: refusing to add unnamed position")
            return False
        if len(name) > defaults.STAGE_POSITION_NAME_MAX_CHARS:
            # The name dialog caps input at the same length; this is the
            # backend half of that guard (names reach status text).
            logger.warning(
                "Stage positions: refusing to add over-long name (%d chars)",
                len(name),
            )
            return False
        # Enforce name uniqueness in the backend, not just in the QML
        # dialog: get_record_by_name / find_index_by_name assume the first
        # match is the only match (the cryo templates feature resolves
        # cross-machine references by name). The QML dialog already blocks
        # this for user input; the backend check makes the invariant real.
        if self._model.find_index_by_name(name) >= 0:
            logger.warning(
                "Stage positions: refusing to add duplicate name %r", name
            )
            return False

        holder = self._read_holder()
        if self._refuse_unsupported(holder):
            return False

        try:
            pos = self._microscope.stage.current_position
        except Exception:
            logger.exception("Stage positions: failed to read current position")
            return False

        record = SavedStagePosition(
            id=_new_id(),
            name=name,
            x=getattr(pos, "x", 0.0),
            y=getattr(pos, "y", 0.0),
            z=getattr(pos, "z", 0.0),
            r=getattr(pos, "r", 0.0),
            t=getattr(pos, "t", 0.0),
            holder=holder or "",
        )
        self._model.add_record(record)
        self._save_to_settings()
        logger.info(
            "Stage positions: added %r (id=%s, holder=%r)",
            record.name, record.id, record.holder,
        )
        if holder is None:
            self.statusUpdated.emit(
                shorten(f"Position saved without holder {SEE_LOG}")
            )
        return True

    @Slot(int, result=bool)
    def update_from_current(self, index: int) -> bool:
        """Update the row at ``index`` with the current position and holder."""
        holder = self._read_holder()
        if self._refuse_unsupported(holder):
            return False

        try:
            pos = self._microscope.stage.current_position
        except Exception:
            logger.exception(
                "Stage positions: failed to read current position for update"
            )
            return False

        # The holder always follows the new coordinates: they were
        # captured under it. A failed read clears it rather than keep a
        # holder the coordinates may not have been captured under.
        ok = self._model.update_coordinates(
            index,
            x=getattr(pos, "x", 0.0),
            y=getattr(pos, "y", 0.0),
            z=getattr(pos, "z", 0.0),
            r=getattr(pos, "r", 0.0),
            t=getattr(pos, "t", 0.0),
            holder=holder or "",
        )
        if ok:
            self._save_to_settings()
            rec = self._model.get(index)
            logger.info(
                "Stage positions: updated %r (id=%s, holder=%r)",
                rec.get("name", "?"), rec.get("id", "?"), rec.get("holder", ""),
            )
            if holder is None:
                self.statusUpdated.emit(
                    shorten(f"Position updated without holder {SEE_LOG}")
                )
        return ok

    @Slot(int, str, result=bool)
    def rename(self, index: int, new_name: str) -> bool:
        """Rename the row at ``index``."""
        new_name = new_name.strip()
        if not new_name:
            logger.warning("Stage positions: refusing to rename to empty string")
            return False
        if len(new_name) > defaults.STAGE_POSITION_NAME_MAX_CHARS:
            logger.warning(
                "Stage positions: refusing to rename row %d to an over-long "
                "name (%d chars)", index, len(new_name),
            )
            return False
        # Reject a collision with a different row (renaming a row to its
        # own current name is a harmless no-op and stays allowed). Mirrors
        # the uniqueness guard in add_from_current — see its comment.
        existing = self._model.find_index_by_name(new_name)
        if existing >= 0 and existing != index:
            logger.warning(
                "Stage positions: refusing to rename row %d to %r "
                "(name already in use)", index, new_name
            )
            return False
        ok = self._model.rename(index, new_name)
        if ok:
            self._save_to_settings()
            logger.info(
                "Stage positions: renamed row %d to %r", index, new_name
            )
        return ok

    @Slot(int, result=bool)
    def remove(self, index: int) -> bool:
        """Remove the row at ``index``."""
        rec = self._model.get(index)
        ok = self._model.remove_at(index)
        if ok:
            self._save_to_settings()
            logger.info(
                "Stage positions: removed %r (id=%s)",
                rec.get("name", "?"), rec.get("id", "?"),
            )
        return ok

    # --- Move To (threaded) -----------------------------------------------

    @Slot(int)
    def move_to(self, index: int) -> None:
        """Move the stage to the position at ``index``.

        Returns immediately. The actual move runs on a worker thread.
        Observe via ``isMoving``, ``moveStarted``, ``moveFinished``,
        and ``statusUpdated``.

        The worker selects the holder the position was saved with and
        leaves it selected; a position with no recorded holder is
        reached under the holder already selected.

        Tri-state dispatch via the pre-start check system —
        mirrors :meth:`StageScanController.start_rotation`:

        * REFUSE → emit :attr:`preStartCheckRefused`,
          ``statusUpdated("Pre-start check failed")``, return.
        * ASK_CONFIRM → emit :attr:`preStartCheckNeedsConfirmation`,
          stash a :class:`_PendingMove`, return. QML opens the
          confirm dialog and calls back via
          :meth:`respondToConfirmation`.
        * PASS → :meth:`_spawn_move_worker` immediately.

        No-op (with a warning log) if a move is already in flight,
        if a previous start is still pending confirmation, or if
        the index/target resolution fails.
        """
        if self._is_moving:
            logger.warning("Stage move already in flight; ignoring move_to")
            return

        if self._pending_move is not None:
            # A previous Move To is still waiting for the user's
            # response to its confirmation dialog. The dialog is
            # modal so this guard is defensive against any future
            # path that opens move_to without going through QML.
            logger.warning(
                "Stage move: previous start is pending confirmation; "
                "ignoring move_to"
            )
            return

        rec_dict = self._model.get(index)
        if not rec_dict:
            logger.warning(
                "Stage move: no position at index %d; ignoring", index
            )
            return
        rec = SavedStagePosition.from_dict(rec_dict)

        # All five axes are required; a malformed row must not move
        # anything.
        try:
            target = StageTarget.from_position(rec)
        except ValueError:
            logger.exception(
                "Stage move: saved position %r is not a complete target",
                rec.name,
            )
            return

        holder = rec.holder

        # --- Pre-start check pass ---
        # Mirrors :meth:`StageScanController.start_rotation` (rule
        # of three: the inline pattern is duplicated rather than
        # extracted; revisit on a fourth consumer). Today the only
        # check gathered for a stage move is the radial-range
        # warning; adding more is a matter of extending
        # :meth:`_move_pre_start_checks`.
        checks = self._move_pre_start_checks()
        try:
            snapshot = gather_snapshot(self._microscope)
            summary = run_pre_start_checks(checks, snapshot)
        except Exception:
            # gather_snapshot reads live hardware; a transient glitch must
            # not propagate out of this @Slot and leave the Move To button
            # enabled with only a console traceback. Refuse with a
            # user-facing breadcrumb instead.
            logger.exception(
                "StagePositions: pre-start hardware read failed; "
                "refusing move"
            )
            self.statusUpdated.emit("Pre-start check failed (hardware read)")
            return

        if summary.refused:
            items = to_dialog_items(summary.refused)
            self.preStartCheckRefused.emit(items)
            self.statusUpdated.emit("Pre-start check failed")
            logger.info(
                "StagePositions: refusing move "
                "(%d pre-start check%s failed)",
                len(summary.refused),
                "" if len(summary.refused) == 1 else "s",
            )
            return

        if summary.needs_confirmation:
            items = to_dialog_items(summary.needs_confirmation)
            self.preStartCheckNeedsConfirmation.emit(items)
            self._pending_move = _PendingMove(
                target=target,
                position_name=rec.name,
                position_id=rec.id,
                holder=holder,
                confirmed_check_types=summary.needs_confirmation_types,
            )
            logger.info(
                "StagePositions: awaiting user confirmation "
                "(%d pre-start check%s ask for confirmation)",
                len(summary.needs_confirmation),
                "" if len(summary.needs_confirmation) == 1 else "s",
            )
            return

        # All clear — spawn directly. No types were confirmed because
        # none needed confirmation.
        self._confirmed_check_types = frozenset()
        self._spawn_move_worker(
            target=target,
            position_name=rec.name,
            position_id=rec.id,
            holder=holder,
        )

    def _move_pre_start_checks(self) -> List[PreStartCheck]:
        """Checks gathered for a Move To gesture.

        Today: a single :class:`StagePositionWithinSafeRangeCheck`.
        Future move-specific checks (e.g., target-position
        validation) would be added here. Method form rather than
        a module-level constant mirrors the same pattern in
        :meth:`StageScanController._rotation_pre_start_checks` —
        keeps the dispatch site uniform across the codebase.
        """
        return [StagePositionWithinSafeRangeCheck()]

    @Slot(bool)
    def respondToConfirmation(self, accepted: bool) -> None:
        """Resume or abort a Move To paused pending confirmation.

        Called by QML when the user clicks OK (``accepted=True``)
        or Cancel (``accepted=False``) on the confirm dialog
        driven by :attr:`preStartCheckNeedsConfirmation`.

        Accept path: records the confirmed check types in
        :attr:`_confirmed_check_types` and calls
        :meth:`_spawn_move_worker` with the stashed target.

        Reject path: emits ``"Stage move cancelled"`` and clears
        the pending state. The same status text is used to
        communicate user-cancelled moves regardless of which
        cancellation mechanism the user used.

        Defensive no-op if no start is pending (e.g. QML fires
        the signal twice through a race, or a stale dialog
        dismiss arrives after a separate state change).
        """
        pending = self._pending_move
        self._pending_move = None
        if pending is None:
            logger.warning(
                "respondToConfirmation called with no pending "
                "move; ignoring"
            )
            return

        if not accepted:
            logger.info(
                "StagePositions: pre-start confirmation cancelled by user"
            )
            self.statusUpdated.emit("Stage move cancelled")
            return

        self._confirmed_check_types = pending.confirmed_check_types
        logger.info(
            "StagePositions: pre-start confirmation accepted; "
            "spawning move worker "
            "(%d check type(s) confirmed)",
            len(self._confirmed_check_types),
        )
        self._spawn_move_worker(
            target=pending.target,
            position_name=pending.position_name,
            position_id=pending.position_id,
            holder=pending.holder,
        )

    def _spawn_move_worker(
        self,
        *,
        target: StageTarget,
        position_name: str,
        position_id: str,
        holder: str = "",
    ) -> None:
        """Construct and start the move thread and worker.

        Called from :meth:`move_to` on the PASS path and from
        :meth:`respondToConfirmation` on the accept path. Sets up
        the worker / thread / signal wiring, flips
        :attr:`isMoving` to True, emits :attr:`moveStarted`, and
        starts the thread. The Zero Tilt Before Stage Moves setting
        is read here, on the GUI thread, and handed to the worker.

        Pre-condition: ``self._is_moving is False``,
        ``self._move_thread is None``, and ``self._pending_move
        is None``.
        """
        staged = bool(self._settings.zeroTiltBeforeStageMoves)
        # Spin up the worker thread. Same pattern as the connect thread
        # in app_controller.py — see there for detailed comments on
        # the deleteLater chain and reference-management.
        thread = QThread()
        worker = _StageMoveWorker(
            stage_ops=self._microscope.stage,
            target=target,
            position_name=position_name,
            staged=staged,
            holder=holder,
        )
        worker.moveToThread(thread)

        thread.started.connect(worker.run)

        worker.statusUpdated.connect(self.statusUpdated)
        worker.finished.connect(self._on_move_finished)
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(self._on_move_thread_finished)

        self._move_thread = thread
        self._move_worker = worker

        self._set_is_moving(True)
        self.moveStarted.emit()

        logger.info(
            "Stage move: dispatching to %r (id=%s, staged=%s, holder=%r)",
            position_name, position_id, staged, holder,
        )
        thread.start()

    @Slot(bool, str, str)
    def _on_move_finished(
        self,
        success: bool,
        position_name: str,
        reason: str,
    ) -> None:
        """Receive the worker's final result on the GUI thread.

        Composes the final status line and emits :attr:`moveFinished`.
        """
        self._set_is_moving(False)
        if success:
            text = f"Stage moved to: {position_name}"
            logger.info("Stage move: complete")
        elif reason == "shortfall":
            text = f"Stage stopped short of {position_name}"
            logger.error("Stage move stopped short (see console log for details)")
        elif reason == "holder":
            text = "Holder selection failed"
            logger.error(
                "Stage move: holder not selected; move to the position not made"
            )
        else:
            text = "Stage move failed"
            logger.error("Stage move failed (see console log for details)")
        if not success:
            text += f" {SEE_LOG}"
        self.statusUpdated.emit(shorten(text))
        self.moveFinished.emit(success, position_name, reason)

    @Slot()
    def _on_move_thread_finished(self) -> None:
        """Null Python references after the thread's event loop exits.

        See :meth:`AppController._on_connect_thread_finished` for the
        rationale on why this is a separate slot rather than nulling
        in :meth:`_on_move_finished`.
        """
        self._move_thread = None
        self._move_worker = None

    # --- Lookup by id (for activities and templates) -----------------------

    def get_record_by_id(
        self, position_id: str
    ) -> Optional[SavedStagePosition]:
        """Return the record with this id, or None if not found."""
        index = self._model.find_index_by_id(position_id)
        if index < 0:
            return None
        d = self._model.get(index)
        if not d:
            return None
        return SavedStagePosition.from_dict(d)
    
    def get_record_by_name(
        self, name: str
    ) -> Optional[SavedStagePosition]:
        """Return the record with this name, or None if not found.

        Used by the Cryo templates feature to translate cross-machine
        position references from name back to the local id.
        """
        index = self._model.find_index_by_name(name)
        if index < 0:
            return None
        d = self._model.get(index)
        if not d:
            return None
        return SavedStagePosition.from_dict(d)

    # --- Bulk enumerate / import (for Cryo templates) ----------------------

    def all_saved_positions(self) -> List[SavedStagePosition]:
        """Snapshot of every saved position.

        Used by the Cryo templates feature for an "all positions" save.
        """
        return self._model.all_records()

    def import_positions(
        self, positions: List[SavedStagePosition],
    ) -> Tuple[Dict[str, str], List[str]]:
        """Non-destructively merge bundled template positions into the list.

        Returns ``(name_map, issues)`` where ``name_map`` maps each
        incoming position's (template) name to the local position id it
        resolved to — the caller uses it to rebind GIS activities to the
        right local position. ``issues`` are human-readable adjustment
        notes for the load summary.

        Policy (never destructive — an existing position is never
        overwritten or removed):

        * **Position already present** (some saved position has
          identical coordinates and the same holder, whatever its
          name) -> reused, nothing added. This is what makes re-loading
          the same template idempotent: an already-imported copy
          matches, so it is reused rather than duplicated. It also
          avoids stacking redundant positions at one physical spot. A
          template position with no recorded holder (older templates)
          matches on coordinates alone.
        * **New position, unused name** -> added as-is (fresh id). No
          issue; appearing in the list is the expected outcome.
        * **New position, name already taken** -> imported under a
          unique ``"<name> (imported)"`` name (``(imported 2)`` etc. if
          needed) so the user keeps both. An issue records the rename.
          Same coordinates under a different holder count as a new
          position: the holder decides what xT lets the stage reach.

        A position match takes precedence over the name-collision rename
        precisely so the second load of a renamed import is a no-op.

        Persists once at the end if anything was added.
        """
        name_map: Dict[str, str] = {}
        issues: List[str] = []
        if not positions:
            return name_map, issues

        # Grows as we add, so coordinate de-dup and name-uniqueness both
        # account for positions imported earlier in this same call.
        current = list(self._model.all_records())
        taken = {rec.name for rec in current}
        added = 0

        for p in positions:
            match = next(
                (rec for rec in current if self._same_position(rec, p)),
                None,
            )
            if match is not None:
                # This exact position already exists (any name) — reuse.
                name_map[p.name] = match.id
                continue
            if p.name in taken:
                # Same name, new coordinates — import under a fresh,
                # unique name so the user keeps both.
                final_name = self._unique_imported_name(p.name, taken)
                issues.append(
                    f"Stage position {p.name!r} already existed; imported "
                    f"the template's copy as {final_name!r}."
                )
            else:
                final_name = p.name
            new_rec = SavedStagePosition(
                id=_new_id(),
                name=final_name,
                x=p.x, y=p.y, z=p.z, r=p.r, t=p.t,
                holder=p.holder,
            )
            self._model.add_record(new_rec)
            current.append(new_rec)
            taken.add(final_name)
            name_map[p.name] = new_rec.id
            added += 1

        if added:
            self._save_to_settings()
        logger.info(
            "Stage positions: imported %d position%s from template "
            "(%d incoming)",
            added, "" if added == 1 else "s", len(positions),
        )
        return name_map, issues

    @staticmethod
    def _coords_equal(
        a: SavedStagePosition, b: SavedStagePosition,
    ) -> bool:
        """True when two positions share all five axis coordinates.

        Exact equality is correct here: both sides originate as Python
        floats and survive the JSON round-trip losslessly, so an
        unchanged position compares equal without needing a tolerance.
        """
        return (
            a.x == b.x and a.y == b.y and a.z == b.z
            and a.r == b.r and a.t == b.t
        )

    @classmethod
    def _same_position(
        cls, local: SavedStagePosition, incoming: SavedStagePosition,
    ) -> bool:
        """True when an incoming template position is already saved.

        Coordinates must match exactly (see :meth:`_coords_equal`) and
        so must the holder, unless the incoming position has none
        recorded (an older template), which matches on coordinates.
        """
        return cls._coords_equal(local, incoming) and (
            not incoming.holder or incoming.holder == local.holder
        )

    @staticmethod
    def _unique_imported_name(base: str, taken: set) -> str:
        """An unused ``"<base> (imported)"``-style name.

        ``taken`` is the set of names already in use (including ones
        added earlier in the same import), so successive collisions get
        ``(imported 2)``, ``(imported 3)``, and so on.
        """
        candidate = f"{base} (imported)"
        if candidate not in taken:
            return candidate
        n = 2
        while f"{base} (imported {n})" in taken:
            n += 1
        return f"{base} (imported {n})"

    # --- Internal state helpers --------------------------------------------

    def _set_is_moving(self, value: bool) -> None:
        if value != self._is_moving:
            self._is_moving = value
            self.isMovingChanged.emit()

    # --- Persistence -------------------------------------------------------

    def _load_from_settings(self) -> None:
        raw = self._qs.value(_K_POSITIONS_LIST)
        if raw is None:
            logger.info("Stage positions: no saved list; starting empty")
            return

        try:
            data = json.loads(str(raw))
        except (TypeError, ValueError):
            logger.warning(
                "Stage positions: could not parse saved list (got %r); "
                "starting empty",
                raw,
                exc_info=True,
            )
            return

        if not isinstance(data, list):
            logger.warning(
                "Stage positions: saved list is not an array (got %r); "
                "starting empty",
                type(data).__name__,
            )
            return

        records = []
        for item in data:
            if not isinstance(item, dict):
                logger.warning(
                    "Stage positions: skipping non-dict entry %r", item
                )
                continue
            try:
                records.append(SavedStagePosition.from_dict(item))
            except (TypeError, ValueError):
                logger.warning(
                    "Stage positions: could not parse entry %r; skipping",
                    item,
                    exc_info=True,
                )

        self._model.replace_all(records)
        logger.info(
            "Stage positions: loaded %d position%s from settings",
            len(records),
            "" if len(records) == 1 else "s",
        )

    def _save_to_settings(self) -> None:
        records = self._model.all_records()
        try:
            payload = json.dumps([r.to_dict() for r in records])
        except (TypeError, ValueError):
            logger.exception(
                "Stage positions: could not serialize list (this is a bug)"
            )
            return
        self._qs.setValue(_K_POSITIONS_LIST, payload)