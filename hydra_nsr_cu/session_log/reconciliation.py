"""Reconstruct :class:`Session` view objects from loaded log entries.

The persistence layer yields a flat sequence of entries in file
order — three kinds (``session_begin``, ``session_end``, ``activity``)
tied together by ``session_id``. Reconciliation pairs them into
:class:`Session` view objects with their activity children attached
and detects *interrupted* sessions (a begin with no matching end).

Why a separate layer
--------------------
Persistence is pure file I/O over dicts; records are individual entry
dataclasses. Neither knows about the two-tier session-plus-children
view the UI actually renders. Reconciliation is the small pure
function that bridges them — testable in complete isolation, with no
Qt, no file system, no microscope.

Edge-case policy
----------------
The session log is observational and the file may be inconsistent
(crash-torn, hand-edited, a write the OS dropped). Reconciliation
never raises — it produces the cleanest view of the survivors and
logs each anomaly for forensics:

* **Orphan activity** (no matching ``session_begin``) → dropped with a
  warning. By construction the writer always emits the begin first;
  an orphan activity is a corruption artifact, and rendering it as a
  visible UI row would be more confusing than informative.

* **Orphan session_end** (no matching ``session_begin``) → dropped
  with a warning. Same rationale.

* **Duplicate session_begin** for the same ``session_id`` → first
  wins; subsequent are dropped with a warning. The first one fixes
  the session's identity (start time, workflow id); a duplicate is
  a corruption signal, not a reset.

* **Duplicate session_end** for the same ``session_id`` → first
  wins, same rationale.

Output ordering is file-order of the begin entries. Activity children
within a session also preserve file order — which is start order on
the writer side, since the writer appends sequentially as activities
finish.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .records import (
    ActivityLogEntry,
    LogEntry,
    SessionBeginEntry,
    SessionEndEntry,
)

logger = logging.getLogger(__name__)


@dataclass
class Session:
    """Reconstructed view of one logged session.

    Built by :func:`reconcile` from the entries on disk. Fields from
    the session_begin are always present; fields from the session_end
    are ``None`` if the session was interrupted (no end entry was ever
    written). The UI renders the ``None`` case as the "interrupted"
    state — a forensically interesting first-class signal, not an
    error.
    """

    session_id: str
    workflow_id: str
    started_at: str
    notes: str
    activities: List[ActivityLogEntry] = field(default_factory=list)
    # All three of these are ``None`` exactly when the session has no
    # ``session_end`` entry — i.e. it was interrupted (app crash,
    # power loss, hard kill). They co-vary; we don't treat them
    # independently.
    ended_at: Optional[str] = None
    all_complete: Optional[bool] = None
    total_duration_s: Optional[float] = None

    @property
    def is_interrupted(self) -> bool:
        """True when no ``session_end`` was found for this session."""
        return self.ended_at is None


def reconcile(entries: List[LogEntry]) -> List[Session]:
    """Group ``entries`` into :class:`Session` view objects.

    Two-phase algorithm:

    1. Walk entries; for each ``session_begin``, create a
       :class:`Session` (skipping duplicates). Record session order
       by file order of the begin entries.
    2. Walk entries again; attach each ``session_end`` and each
       activity to its session by ``session_id``. Orphans, duplicate
       ends, and the (never-emitted-by-the-writer) extra begins are
       handled per the module's edge-case policy.

    The two-phase split lets phase 2 attach a session_end that appears
    *before* its session_begin in file order — which shouldn't happen
    on a sane writer but could happen if the file was hand-edited or
    if a future feature ever reorders writes. The extra pass is cheap
    at this data volume.
    """
    # Track session order by file order of begin entries. The dict
    # provides O(1) lookup; the list preserves the order we want to
    # render.
    session_order: List[str] = []
    sessions_by_id: Dict[str, Session] = {}

    # Phase 1 — sessions from begin entries.
    for entry in entries:
        if isinstance(entry, SessionBeginEntry):
            if entry.session_id in sessions_by_id:
                logger.warning(
                    "Session log: duplicate session_begin for "
                    "session_id=%r; keeping first, dropping subsequent",
                    entry.session_id,
                )
                continue
            sessions_by_id[entry.session_id] = Session(
                session_id=entry.session_id,
                workflow_id=entry.workflow_id,
                started_at=entry.started_at,
                notes=entry.notes,
            )
            session_order.append(entry.session_id)

    # Phase 2 — attach ends and activities. Iterates the same input
    # list a second time; begin entries are matched against the
    # already-built sessions_by_id, so the SessionBeginEntry branch
    # here is intentionally absent (already handled in phase 1).
    for entry in entries:
        if isinstance(entry, SessionEndEntry):
            session = sessions_by_id.get(entry.session_id)
            if session is None:
                logger.warning(
                    "Session log: dropping session_end with no matching "
                    "session_begin (session_id=%r)",
                    entry.session_id,
                )
                continue
            if session.ended_at is not None:
                logger.warning(
                    "Session log: duplicate session_end for "
                    "session_id=%r; keeping first, dropping subsequent",
                    entry.session_id,
                )
                continue
            session.ended_at = entry.ended_at
            session.all_complete = entry.all_complete
            session.total_duration_s = entry.total_duration_s

        elif isinstance(entry, ActivityLogEntry):
            session = sessions_by_id.get(entry.session_id)
            if session is None:
                logger.warning(
                    "Session log: dropping orphan activity "
                    "(session_id=%r, activity_id=%r)",
                    entry.session_id, entry.activity_id,
                )
                continue
            session.activities.append(entry)

    return [sessions_by_id[sid] for sid in session_order]