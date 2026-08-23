"""Stage Positions controller — QML-facing surface for saved positions.

Owns the :class:`StagePositionsModel`, the JSON persistence path, and
the worker-thread machinery for the Move To operation. Exposes
high-level operations to QML (``add_from_current``,
``update_from_current``, etc.) that combine reading the live stage
position with model mutation. QML never reads the stage directly —
all coordinate-producing operations go through this controller.

``move_to(position_id)`` runs the blocking ``absolute_move()`` on a
short-lived :class:`QThread` worker; the GUI thread observes it through
``isMoving``, ``moveStarted``, ``moveFinished``, and ``statusUpdated``.
A failed move is logged with a full traceback and surfaced as
``moveFinished(success=False)``; the message itself is not exposed —
QML opens a generic "Stage Move Error" dialog pointing to the logs.

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
from ..pre_start_checks import (
    PreStartCheck,
    gather_snapshot,
    run_pre_start_checks,
    to_dialog_items,
)
from ..pre_start_checks.checks import StagePositionWithinSafeRangeCheck
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
        target: The resolved ``StagePosition`` (real or simulated) for
            the ``absolute_move`` call. Typed as ``object`` because the
            two facades return distinct concrete types.
        position_name: User-visible name, used in status messages.
        position_id: Opaque id of the saved row, used only in logs.
        confirmed_check_types: Check types the user is being asked to
            confirm; carried into
            :attr:`StagePositionsController._confirmed_check_types` on
            accept (populated for parity, not consulted mid-run).
    """
    target: object
    position_name: str
    position_id: str
    confirmed_check_types: FrozenSet[Type[PreStartCheck]] = frozenset()


class _StageMoveWorker(QObject):
    """Worker that performs a single stage absolute_move on a worker thread.

    Single-shot — destroyed via ``deleteLater`` once :attr:`finished`
    has been emitted. Mirrors the lifecycle of :class:`_ConnectWorker`
    in :mod:`app_controller`.

    The worker holds a reference to the live stage ops object and the
    target position; it does not need a reference back to the controller.
    Status text is emitted at the start of the move so the StatusBar can
    show "Moving stage to: <name>..."; the controller decides what (if
    anything) to emit on completion.
    """

    # finished(success: bool, position_name: str). The error path is
    # logged with full traceback inside run(); we don't propagate the
    # error message because QML's user-facing response is a generic
    # dialog. The position_name lets the controller emit a "Stage
    # moved to: X" status message on success.
    finished = Signal(bool, str)

    # statusUpdated(text). Emitted at the start of the move so the
    # StatusBar can show progress.
    statusUpdated = Signal(str)

    def __init__(self, stage_ops, position, position_name: str) -> None:
        super().__init__()
        self._stage = stage_ops
        self._position = position
        self._position_name = position_name

    @Slot()
    def run(self) -> None:
        """Perform the move; emit finished(success) on completion."""
        try:
            self.statusUpdated.emit(
                f"Moving stage to: {self._position_name}..."
            )
            logger.info(
                "Stage move worker: moving to %r", self._position
            )
            self._stage.absolute_move(self._position)
            logger.info("Stage move worker: move complete")
            self.finished.emit(True, self._position_name)
        except Exception:
            logger.exception(
                "Stage move worker: absolute_move raised"
            )
            self.finished.emit(False, self._position_name)


class StagePositionsController(QObject):
    """Saved stage positions, exposed to QML as ``appController.stagePositions``."""

    # Emitted as the move starts and finishes. AppController listens to
    # these to update its global runningWorkflowId.
    moveStarted = Signal()
    # moveFinished(success: bool, position_name: str). On failure, QML opens its error
    # dialog; on success, it just clears the activity state to idle.
    moveFinished = Signal(bool, str)

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
        parent: Optional[QObject] = None,
    ) -> None:
        super().__init__(parent)
        self._microscope = microscope

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

    # --- Add / update / rename / remove (synchronous) ----------------------

    @Slot(str, result=bool)
    def add_from_current(self, name: str) -> bool:
        """Read the current stage position and add it as a new saved row."""
        name = name.strip()
        if not name:
            logger.warning("Stage positions: refusing to add unnamed position")
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
        )
        self._model.add_record(record)
        self._save_to_settings()
        logger.info(
            "Stage positions: added %r (id=%s)", record.name, record.id
        )
        return True

    @Slot(int, result=bool)
    def update_from_current(self, index: int) -> bool:
        """Update the row at ``index`` with the current stage position."""
        try:
            pos = self._microscope.stage.current_position
        except Exception:
            logger.exception(
                "Stage positions: failed to read current position for update"
            )
            return False

        ok = self._model.update_coordinates(
            index,
            x=getattr(pos, "x", 0.0),
            y=getattr(pos, "y", 0.0),
            z=getattr(pos, "z", 0.0),
            r=getattr(pos, "r", 0.0),
            t=getattr(pos, "t", 0.0),
        )
        if ok:
            self._save_to_settings()
            rec = self._model.get(index)
            logger.info(
                "Stage positions: updated coordinates for %r (id=%s)",
                rec.get("name", "?"), rec.get("id", "?"),
            )
        return ok

    @Slot(int, str, result=bool)
    def rename(self, index: int, new_name: str) -> bool:
        """Rename the row at ``index``."""
        new_name = new_name.strip()
        if not new_name:
            logger.warning("Stage positions: refusing to rename to empty string")
            return False
        # Reject a collision with a DIFFERENT row (renaming a row to its
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

        # Construct the target position via the StageOps factory. Real
        # ops returns autoscript's StagePosition; simulated returns
        # SimulatedStagePosition. Either is accepted by the matching
        # absolute_move.
        try:
            target = self._microscope.stage.make_position(
                x=rec.x, y=rec.y, z=rec.z, r=rec.r, t=rec.t,
            )
        except Exception:
            logger.exception(
                "Stage move: could not construct target StagePosition"
            )
            return

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
        )

    def _spawn_move_worker(
        self,
        *,
        target: object,
        position_name: str,
        position_id: str,
    ) -> None:
        """Construct and start the move thread and worker.

        Called from :meth:`move_to` on the PASS path and from
        :meth:`respondToConfirmation` on the accept path. Sets up
        the worker / thread / signal wiring, flips
        :attr:`isMoving` to True, emits :attr:`moveStarted`, and
        starts the thread.

        Pre-condition: ``self._is_moving is False``,
        ``self._move_thread is None``, and ``self._pending_move
        is None``.
        """
        # Spin up the worker thread. Same pattern as the connect thread
        # in app_controller.py — see there for detailed comments on
        # the deleteLater chain and reference-management.
        thread = QThread()
        worker = _StageMoveWorker(
            stage_ops=self._microscope.stage,
            position=target,
            position_name=position_name,
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
            "Stage move: dispatching to %r (id=%s)",
            position_name, position_id,
        )
        thread.start()

    @Slot(bool, str)
    def _on_move_finished(self, success: bool, position_name: str) -> None:
        """Receive the worker's final result on the GUI thread."""
        self._set_is_moving(False)
        if success:
            self.statusUpdated.emit(f"Stage moved to: {position_name}")
            logger.info("Stage move: complete")
        else:
            self.statusUpdated.emit("Stage move failed (see console log)")
            logger.error("Stage move failed (see console log for details)")
        self.moveFinished.emit(success, position_name)

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

        * **Coordinates already present** (some saved position has
          identical coordinates, whatever its name) -> reused, nothing
          added. This is what makes re-loading the same template
          idempotent: an already-imported copy matches on coordinates,
          so it is reused rather than duplicated. It also avoids stacking
          redundant positions at one physical spot.
        * **New coordinates, unused name** -> added as-is (fresh id). No
          issue; appearing in the list is the expected outcome.
        * **New coordinates, name already taken** -> imported under a
          unique ``"<name> (imported)"`` name (``(imported 2)`` etc. if
          needed) so the user keeps both. An issue records the rename.

        Coordinate match takes precedence over the name-collision rename
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
            coord_match = next(
                (rec for rec in current if self._coords_equal(rec, p)),
                None,
            )
            if coord_match is not None:
                # This exact position already exists (any name) — reuse.
                name_map[p.name] = coord_match.id
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