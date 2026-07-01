"""Qt model for the Session Log page.

One row per session — a flat :class:`QAbstractListModel` where each
row is a session, and that session's activities are exposed as a
nested list via the ``activities`` role. The Session Log page
renders one delegate per session (a card), with a ``Repeater``
inside the delegate iterating the activities list to render each
activity inline.

This is a step-6 design refinement: the original model interleaved
session-header rows and activity rows in a flat list, with the
delegate switching on a ``kind`` discriminator. The flat shape
worked but had a UX gotcha — partial scrolling could decouple
activities from their parent session header. Nesting activities
inside the session row fixes that by making each card a single
visually-contained unit.

Row shape
---------
Each row is a plain dict with these keys (snake_case internally,
mapped to camelCase QML role names via :meth:`roleNames`):

* ``session_id`` — opaque short id
* ``workflow_id`` — ``"cryo_prep"``, ``"rt_prep"``, …
* ``started_at`` — ISO 8601 local timestamp
* ``notes`` — user-editable string (empty by default)
* ``ended_at`` — ISO 8601 or ``None`` for an interrupted session
* ``all_complete`` — ``True``/``False`` outcome, or ``None`` for
  an interrupted session (no end record on disk)
* ``total_duration_s`` — float, or ``None`` if not yet ended
* ``is_interrupted`` — ``True`` when no ``session_end`` entry was
  ever written (process crash / forced exit mid-workflow)
* ``is_running`` — ``True`` only for the session the controller's
  ``_current_session_id`` points at right now. Pure runtime state:
  never persisted, never produced by reconciliation. Computed by
  the controller (``SessionLog._row_for``) at row-build time and
  injected here via the required keyword on
  :meth:`session_to_row`. The QML badge checks this *before*
  ``is_interrupted`` — a live session also has ``ended_at is
  None``, so without this flag it would falsely badge as
  "Interrupted".
* ``activities`` — list of activity dicts (camelCase keys; QML's
  Repeater consumes these directly as ``modelData.<field>``)

Mutation API
------------
* :meth:`replace_all` — startup load and ``clearAll``; emits
  ``modelReset``.
* :meth:`append_row` — running-workflow path: a new session
  appears here when ``workflowStarted`` fires; emits
  ``rowsInserted`` for that index.
* :meth:`update_row_at` — three triggers:
  1. New activity recorded during a running workflow (the row's
     ``activities`` list grows).
  2. Workflow finished (the row's ``ended_at`` /
     ``all_complete`` / ``total_duration_s`` fields populate).
  3. User edits the notes (the row's ``notes`` field changes).
  In all three cases the entire row dict is replaced and a
  blanket ``dataChanged`` is emitted; QML re-evaluates bindings
  for that row's delegate.

Granular over-modelReset on the running-workflow path was a
ratified design point — ListView scroll position survives
``rowsInserted`` / ``dataChanged`` but not ``modelReset``, and
during a live workflow the user is watching the page.

Presentation
------------
Values are stored canonical (raw SI for numerics, ISO strings for
timestamps, dicts for params, snake_case ids for workflows /
activities). Formatting for display lives on
:class:`SessionLog` as ``@Slot``-decorated helpers; the delegate
is the only consumer of that presentation layer.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from PySide6.QtCore import (
    Property,
    QAbstractListModel,
    QByteArray,
    QModelIndex,
    Qt,
    Signal,
)

from .records import ActivityLogEntry
from .reconciliation import Session

logger = logging.getLogger(__name__)


class SessionLogModel(QAbstractListModel):
    """One-row-per-session list model."""

    # Role enum values. Starting at ``Qt.UserRole`` keeps us clear of
    # the standard Qt roles. Ten session-level fields + the nested
    # activities list — significantly fewer roles than the flat-
    # interleaved version that came before, because activity fields
    # now live inside the ``activities`` list dicts rather than as
    # top-level row fields.
    SessionIdRole = Qt.UserRole + 1
    WorkflowIdRole = Qt.UserRole + 2
    StartedAtRole = Qt.UserRole + 3
    NotesRole = Qt.UserRole + 4
    EndedAtRole = Qt.UserRole + 5
    AllCompleteRole = Qt.UserRole + 6
    TotalDurationRole = Qt.UserRole + 7
    IsInterruptedRole = Qt.UserRole + 8
    ActivitiesRole = Qt.UserRole + 9
    # Appended after ActivitiesRole to keep existing role numbering
    # stable — role ints are internal, but stable numbering keeps
    # diffs and any cached delegate state honest.
    IsRunningRole = Qt.UserRole + 10

    # Notify signal so QML ``count`` bindings refresh. The base class
    # already emits ``rowsInserted`` / ``rowsRemoved`` which drives
    # the ListView, but a separate ``countChanged`` is needed for the
    # ``@Property`` notification contract — same pattern as
    # :class:`StagePositionsModel`.
    countChanged = Signal()

    def __init__(self, parent: Optional[Any] = None) -> None:
        super().__init__(parent)
        self._rows: List[Dict[str, Any]] = []

    # --- QAbstractListModel interface --------------------------------------

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        if parent.isValid():
            return 0
        return len(self._rows)

    def data(self, index: QModelIndex, role: int = Qt.DisplayRole) -> Any:
        if not index.isValid():
            return None
        row_idx = index.row()
        if not (0 <= row_idx < len(self._rows)):
            return None
        row = self._rows[row_idx]
        if role == self.SessionIdRole:
            return row.get("session_id")
        if role == self.WorkflowIdRole:
            return row.get("workflow_id")
        if role == self.StartedAtRole:
            return row.get("started_at")
        if role == self.NotesRole:
            return row.get("notes")
        if role == self.EndedAtRole:
            return row.get("ended_at")
        if role == self.AllCompleteRole:
            return row.get("all_complete")
        if role == self.TotalDurationRole:
            return row.get("total_duration_s")
        if role == self.IsInterruptedRole:
            return row.get("is_interrupted")
        if role == self.IsRunningRole:
            return row.get("is_running")
        if role == self.ActivitiesRole:
            return row.get("activities")
        return None

    def roleNames(self) -> Dict[int, QByteArray]:
        # camelCase QML-facing names; matches the JS / QML
        # convention and is consistent with other models in this
        # codebase (StagePositionsModel etc.).
        return {
            self.SessionIdRole: QByteArray(b"sessionId"),
            self.WorkflowIdRole: QByteArray(b"workflowId"),
            self.StartedAtRole: QByteArray(b"startedAt"),
            self.NotesRole: QByteArray(b"notes"),
            self.EndedAtRole: QByteArray(b"endedAt"),
            self.AllCompleteRole: QByteArray(b"allComplete"),
            self.TotalDurationRole: QByteArray(b"totalDurationS"),
            self.IsInterruptedRole: QByteArray(b"isInterrupted"),
            self.IsRunningRole: QByteArray(b"isRunning"),
            self.ActivitiesRole: QByteArray(b"activities"),
        }

    # --- QML conveniences --------------------------------------------------

    @Property(int, notify=countChanged)
    def count(self) -> int:
        return len(self._rows)

    # --- Mutation (called by the controller, not by QML) ------------------

    def replace_all(self, rows: List[Dict[str, Any]]) -> None:
        """Reset the model to the given row list. Emits ``modelReset``.

        Used at startup-load and at ``clearAll``. The ``modelReset``
        jump-resets ListView scroll position, which is acceptable
        for these two cases — neither is the running-workflow path
        where scroll continuity matters.
        """
        self.beginResetModel()
        self._rows = list(rows)
        self.endResetModel()
        self.countChanged.emit()

    def append_row(self, row: Dict[str, Any]) -> None:
        """Append a row at the end. Emits ``rowsInserted``.

        Used on the running-workflow path when ``workflowStarted``
        fires: a fresh session row lands at the bottom of the model
        (chronological ordering — most-recent session is at the
        end). Activities recorded during the run update *this* row's
        ``activities`` list via :meth:`update_row_at`; they don't
        get their own rows.
        """
        new_index = len(self._rows)
        self.beginInsertRows(QModelIndex(), new_index, new_index)
        self._rows.append(row)
        self.endInsertRows()
        self.countChanged.emit()

    def update_row_at(
        self, row_idx: int, new_row: Dict[str, Any],
    ) -> bool:
        """Replace the row at ``row_idx`` with ``new_row``.

        Emits ``dataChanged`` across all roles for the affected row.
        Three running-workflow triggers feed through this:

        1. A new activity is recorded — the row's ``activities`` list
           grows; QML's ``Repeater`` inside the delegate re-evaluates
           and renders the new activity inline.
        2. Workflow finishes — the row's ``ended_at`` /
           ``all_complete`` / ``total_duration_s`` fields populate.
        3. User edits the notes — the row's ``notes`` field changes.

        Returns ``True`` on success, ``False`` if the index is out
        of range.
        """
        if not (0 <= row_idx < len(self._rows)):
            return False
        self._rows[row_idx] = new_row
        model_index = self.index(row_idx, 0)
        self.dataChanged.emit(model_index, model_index, [])
        return True

    def find_session_row(self, session_id: str) -> int:
        """Return the index of the row for ``session_id``, or ``-1``.

        Used by the controller to locate a session for in-place
        updates (activity append, session_end finalization, note
        edit). Linear scan; the typical session count is small.
        """
        for i, row in enumerate(self._rows):
            if row.get("session_id") == session_id:
                return i
        return -1

    # --- Row construction helpers (called by the controller) --------------

    @staticmethod
    def session_to_row(
        session: Session, *, is_running: bool,
    ) -> Dict[str, Any]:
        """Build a session row dict from a :class:`Session`.

        Activities are nested as a list of dicts under the
        ``activities`` key — each session row carries its own
        children directly. QML's ``Repeater`` binds to this list to
        render the activities inline within the session's card.

        ``is_running`` is a **required keyword** by design.
        Running-ness is controller state (``_current_session_id``),
        not disk state — :class:`Session` cannot know it, and a
        defaulted flag would let a call site silently render a live
        session as "Interrupted" (the exact bug this parameter
        fixes). All production calls route through
        ``SessionLog._row_for``, which computes the flag from the
        single source of truth; the required keyword turns any
        bypass into a loud ``TypeError`` instead of a quiet wrong
        badge. Mirrors the ``restore()`` required-flag pattern in
        the PFIB conditions recorder.

        Static because the mapping itself is stateless; the
        controller supplies the one piece of runtime state via the
        flag. Centralizing the field-mapping here keeps the role
        names, the row keys, and the activity dict shape aligned in
        one place — drift between them would surface as silent UI
        breakage.
        """
        return {
            "session_id": session.session_id,
            "workflow_id": session.workflow_id,
            "started_at": session.started_at,
            "notes": session.notes,
            "ended_at": session.ended_at,
            "all_complete": session.all_complete,
            "total_duration_s": session.total_duration_s,
            "is_interrupted": session.is_interrupted,
            "is_running": is_running,
            "activities": [
                SessionLogModel._activity_to_qml_dict(a)
                for a in session.activities
            ],
        }

    @staticmethod
    def _activity_to_qml_dict(
        activity: ActivityLogEntry,
    ) -> Dict[str, Any]:
        """Build the activity dict consumed by the QML ``Repeater``.

        Uses camelCase keys to match the role-name convention
        elsewhere in the model — QML accesses fields uniformly as
        ``modelData.activityId``, ``modelData.durationS`` etc.
        Without this normalization, the QML side would mix
        snake_case (activity dict keys) and camelCase (session
        role names) inside the same delegate, which is the kind
        of avoidable cognitive load a small dict transform
        eliminates.

        ``sessionId`` is not duplicated into the activity dict —
        the activity is nested inside its parent session's row, so
        the parent identity is structural and reachable via the
        delegate's ``sessionId`` property.
        """
        return {
            "finishedAt": activity.finished_at,
            "activityId": activity.activity_id,
            "instanceId": activity.instance_id,
            "result": activity.result,
            "durationS": activity.duration_s,
            "lastStatus": activity.last_status,
            "params": activity.params,
        }