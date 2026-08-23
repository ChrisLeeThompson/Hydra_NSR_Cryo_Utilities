"""JSON-serializable entries for the session log file.

Three entry kinds, one line of JSONL each:

* :class:`SessionBeginEntry` — written at workflow start. Carries the
  session identity, workflow type, start timestamp, and the editable
  ``notes`` field. Notes live here (not on the end entry) so that
  *interrupted* sessions — which by definition never get an end
  entry written — can still be annotated.

* :class:`SessionEndEntry` — written at workflow finish. Carries the
  outcome (``all_complete``) and total wall-clock duration. Absent
  for an interrupted session; reconciliation handles that case.

* :class:`ActivityLogEntry` — written once per activity finish.
  Carries the result, duration, last status message, and the
  parameter dict from
  :meth:`hydra_nsr_cu.activities.base.ActivityService.parameter_summary`.
  Its ``notes`` field is written empty and not yet surfaced in the
  UI; it reserves room for per-activity annotation without a
  file-format migration.

The discriminator field is ``kind``; :func:`entry_from_dict`
dispatches on it. *Entry* (an activity that has run) is deliberately
distinct from the cryo package's *Record* (an activity to run).

Every entry carries a ``"v"`` schema-version field. The loader warns
on an unfamiliar version but still attempts to parse; a missing
mandatory field makes ``from_dict`` raise and the caller drops that
entry. Unknown extra fields survive loading but are not preserved
through a rewrite (``to_dict`` emits only known fields).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, Union

logger = logging.getLogger(__name__)


# --- Discriminator constants ------------------------------------------------

KIND_SESSION_BEGIN = "session_begin"
KIND_SESSION_END = "session_end"
KIND_ACTIVITY = "activity"

CURRENT_SCHEMA_VERSION = 1


# --- Entry dataclasses ------------------------------------------------------


@dataclass
class SessionBeginEntry:
    """The opening entry of a session in the log.

    Plain (non-frozen) dataclass: the ``notes`` field is editable
    after the fact, mutated via the in-memory model and persisted
    by atomic full-file rewrite. The other fields are write-once
    by convention — enforced by the SessionLog controller layer,
    not by the dataclass itself.
    """

    session_id: str
    workflow_id: str
    started_at: str
    notes: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "v": CURRENT_SCHEMA_VERSION,
            "kind": KIND_SESSION_BEGIN,
            "session_id": self.session_id,
            "workflow_id": self.workflow_id,
            "started_at": self.started_at,
            "notes": self.notes,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "SessionBeginEntry":
        """Build from a parsed dict; raises ValueError on missing mandatories.

        ``session_id`` and ``started_at`` are mandatory — without them
        the entry can't be tied back to its session or rendered in
        the UI. Missing optional fields fall back to sensible defaults
        in the same defensive style as
        :meth:`hydra_nsr_cu.cryo.activity_records.SputterCoatRecord.from_dict`.
        """
        session_id = d.get("session_id")
        if not session_id:
            raise ValueError(
                "SessionBeginEntry: missing required field 'session_id'"
            )
        started_at = d.get("started_at")
        if not started_at:
            raise ValueError(
                "SessionBeginEntry: missing required field 'started_at'"
            )
        return cls(
            session_id=str(session_id),
            workflow_id=str(d.get("workflow_id", "")),
            started_at=str(started_at),
            notes=str(d.get("notes", "")),
        )


@dataclass
class SessionEndEntry:
    """The closing entry of a session in the log.

    Write-once by convention. ``all_complete`` is ``True`` only when
    every activity in the session finished cleanly (per the worker's
    ``finished`` signal semantics); ``False`` for stop / exception
    paths.
    """

    session_id: str
    ended_at: str
    all_complete: bool
    total_duration_s: float

    def to_dict(self) -> Dict[str, Any]:
        return {
            "v": CURRENT_SCHEMA_VERSION,
            "kind": KIND_SESSION_END,
            "session_id": self.session_id,
            "ended_at": self.ended_at,
            "all_complete": self.all_complete,
            "total_duration_s": self.total_duration_s,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "SessionEndEntry":
        session_id = d.get("session_id")
        if not session_id:
            raise ValueError(
                "SessionEndEntry: missing required field 'session_id'"
            )
        # A blank/missing ended_at is rejected (like session_id) rather
        # than coerced to "". The reconciler and is_interrupted treat
        # ``ended_at is not None`` as "session completed", so a blank
        # value would badge a corrupt entry as completed-with-no-timestamp
        # and break the "end fields are None exactly when interrupted"
        # invariant. Raising here makes the tolerant loader drop the entry,
        # leaving the session correctly interrupted.
        ended_at = d.get("ended_at")
        if not ended_at:
            raise ValueError(
                "SessionEndEntry: missing required field 'ended_at'"
            )
        return cls(
            session_id=str(session_id),
            ended_at=str(ended_at),
            all_complete=bool(d.get("all_complete", False)),
            total_duration_s=float(d.get("total_duration_s", 0.0)),
        )


@dataclass
class ActivityLogEntry:
    """One activity outcome recorded in the log.

    The ``params`` dict is the verbatim
    :meth:`ActivityService.parameter_summary` output, which is
    JSON-serializable by contract. Activities are free to grow their
    own param shapes over time; the log persists whatever they emit
    at the time of the run.

    The ``notes`` field is a reserved per-activity annotation slot:
    written empty by the system, not currently surfaced in the UI,
    and preserved by ``to_dict`` so round-tripping never drops it.
    """

    session_id: str
    finished_at: str
    activity_id: str
    instance_id: str
    result: str
    duration_s: float
    last_status: str
    params: Dict[str, Any] = field(default_factory=dict)
    notes: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "v": CURRENT_SCHEMA_VERSION,
            "kind": KIND_ACTIVITY,
            "session_id": self.session_id,
            "finished_at": self.finished_at,
            "activity_id": self.activity_id,
            "instance_id": self.instance_id,
            "result": self.result,
            "duration_s": self.duration_s,
            "last_status": self.last_status,
            "params": self.params,
            "notes": self.notes,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "ActivityLogEntry":
        session_id = d.get("session_id")
        if not session_id:
            raise ValueError(
                "ActivityLogEntry: missing required field 'session_id'"
            )
        # ``params`` may be absent or non-dict in malformed data; coerce
        # to an empty dict rather than raising — the entry is still
        # useful even if its params got corrupted (the activity is
        # still shown as having run).
        raw_params = d.get("params")
        params = raw_params if isinstance(raw_params, dict) else {}
        return cls(
            session_id=str(session_id),
            finished_at=str(d.get("finished_at", "")),
            activity_id=str(d.get("activity_id", "")),
            instance_id=str(d.get("instance_id", "")),
            result=str(d.get("result", "")),
            duration_s=float(d.get("duration_s", 0.0)),
            last_status=str(d.get("last_status", "")),
            params=params,
            notes=str(d.get("notes", "")),
        )


# Type alias used by the reconciliation layer.
LogEntry = Union[SessionBeginEntry, SessionEndEntry, ActivityLogEntry]


# --- Dispatcher -------------------------------------------------------------


def entry_from_dict(d: Dict[str, Any]) -> LogEntry:
    """Construct the appropriate entry subtype from a parsed-JSON dict.

    Dispatches on the ``"kind"`` discriminator. Logs a warning on an
    unfamiliar schema version (``"v"`` ≠ :data:`CURRENT_SCHEMA_VERSION`)
    and proceeds — the per-class ``from_dict`` will tolerate missing
    optional fields and raise ``ValueError`` only when a mandatory
    field is absent.

    Raises :class:`ValueError` for unknown ``kind`` or missing
    mandatories. Callers (typically the reconciliation layer) catch
    and drop offending entries with their own warning.
    """
    v = d.get("v", CURRENT_SCHEMA_VERSION)
    if v != CURRENT_SCHEMA_VERSION:
        logger.warning(
            "Session log: entry has schema version v=%r (expected v=%d); "
            "attempting to parse with current semantics",
            v, CURRENT_SCHEMA_VERSION,
        )

    kind = d.get("kind")
    if kind == KIND_SESSION_BEGIN:
        return SessionBeginEntry.from_dict(d)
    if kind == KIND_SESSION_END:
        return SessionEndEntry.from_dict(d)
    if kind == KIND_ACTIVITY:
        return ActivityLogEntry.from_dict(d)
    raise ValueError(
        f"Session log: unknown entry kind {kind!r}; cannot dispatch"
    )