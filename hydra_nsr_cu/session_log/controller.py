"""SessionLog controller — the QML-facing top of the session_log subpackage.

Owns the model, the file path, the in-memory list of Sessions (the
UI's source of truth), and the handlers that turn runner signals
into log entries. Exposes QML-callable slots for note edits and the
Settings-page guarded clear.

Invariants
----------
* **The in-memory model is the source of truth.** Every event
  updates ``self._sessions`` and the :class:`SessionLogModel` rows
  immediately; the UI reads from there.
* **The file is a best-effort durable mirror.** Writes follow the
  in-memory update; failures log and emit :attr:`writeFailed` but
  never raise and never roll back in-memory state.
* **Self-heal on vanish.** Once ``_has_been_written`` is set, an
  append that finds the file missing triggers an
  :func:`atomic_rewrite` from the full in-memory model.
  ``clearAll`` resets the flag so a deliberate clear looks like a
  fresh install on the next write.
* **Running-ness is controller state, not disk state.** The
  ``is_running`` row flag is derived from ``_current_session_id``
  in :meth:`_row_for`, the single chokepoint for row construction.
  It is never persisted; a crash-orphaned session reloads as
  Interrupted, never Running.
* **Never load-bearing.** Nothing here can abort a workflow.

``SessionLog`` is constructed eagerly by ``AppController`` (no
microscope dependency) and populated from disk at construction;
runner signals are connected later by ``_wire_workflow`` once the
workflow runners exist.
"""
from __future__ import annotations

import logging
import os
import uuid
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from PySide6.QtCore import (
    Property,
    QObject,
    QUrl,
    Signal,
    Slot,
)
from PySide6.QtQml import QJSValue

from .model import SessionLogModel
from .persistence import append_record, atomic_rewrite, load_all
from .reconciliation import Session, reconcile
from .records import (
    ActivityLogEntry,
    LogEntry,
    SessionBeginEntry,
    SessionEndEntry,
    entry_from_dict,
)

logger = logging.getLogger(__name__)


# Default file name inside the session_logs/ directory. The directory
# leaves room for future log rotation; for now there's exactly one file.
_DEFAULT_LOG_FILENAME = "session_log.jsonl"


def _new_session_id() -> str:
    """Short, opaque, collision-resistant id for a session.

    Matches the format used by
    :func:`hydra_nsr_cu.stage_positions.positions_model._new_id` and
    :func:`hydra_nsr_cu.cryo.activity_records._new_instance_id` —
    8 hex chars from a UUID4. Long enough to be unambiguous in logs,
    short enough to be unobtrusive in serialized form.
    """
    return uuid.uuid4().hex[:8]


def _now_iso_local() -> str:
    """Local-time ISO 8601 timestamp, seconds precision.

    Local time matches the console log (``logging_setup.py`` uses
    Python's default ``%(asctime)s``), and the session log is read on
    the same machine it is written on. ``timespec="seconds"`` keeps
    the timestamps compact; sub-second precision isn't meaningful at
    this granularity.

    Format example: ``"2026-05-19T14:32:17"``.
    """
    return datetime.now().isoformat(timespec="seconds")


def _format_ion_current(amperes: Any) -> str:
    """Format an ion-beam current value in the most readable SI prefix.

    PFIB beam currents typically run in the picoampere to nanoampere
    range; raw amperes (3e-11 A) is unreadable. This formatter picks
    the SI prefix that gives a value between 0.1 and 1000, so the user
    sees ``"30 pA"`` rather than ``"3e-11 A"``.

    Defensive against the params dict ever carrying a non-numeric
    value here (corruption / hand-edit) — falls back to repr.
    """
    try:
        a = float(amperes)
    except (TypeError, ValueError):
        return str(amperes)
    abs_a = abs(a)
    if abs_a < 1e-9:
        return f"{a * 1e12:g} pA"
    if abs_a < 1e-6:
        return f"{a * 1e9:g} nA"
    if abs_a < 1e-3:
        return f"{a * 1e6:g} µA"
    return f"{a:g} A"


class SessionLog(QObject):
    """QML-facing session log controller.

    Constructed eagerly by :class:`AppController` (no microscope
    dependency). Exposed to QML as ``appController.sessionLog``.

    Runner signals are connected in
    ``AppController._wire_workflow`` after the workflow runners
    exist — see :meth:`on_workflow_started`,
    :meth:`on_workflow_finished`, :meth:`on_activity_recorded`.
    """

    # --- Signals ---------------------------------------------------------

    # Emitted after each write failure. Carries the human-readable
    # message that the page surfaces as a transient "Session log not
    # saved" hint. The workflow is unaffected; the in-memory model is
    # already updated by the time this fires.
    writeFailed = Signal(str)

    # File size notify — emitted after any write (success or
    # failure, if size could have changed). The Settings page binds
    # the size readout to this property next to the guarded clear
    # control.
    fileSizeChanged = Signal()

    def __init__(
        self,
        log_dir: Optional[Union[str, Path]] = None,
        parent: Optional[QObject] = None,
    ) -> None:
        super().__init__(parent)

        # File path. Default = <project_root>/session_logs/session_log.jsonl,
        # mirroring the templates-dir pattern in
        # :class:`CryoActivitiesController`. Inject ``log_dir`` for
        # smoke testing against a temp directory.
        if log_dir is None:
            project_root = Path(__file__).resolve().parent.parent.parent
            log_dir = project_root / "session_logs"
        self._log_dir = Path(log_dir)
        self._file_path = self._log_dir / _DEFAULT_LOG_FILENAME

        # In-memory state — the source of truth for the UI.
        self._sessions: List[Session] = []
        # When a workflow is running, this points at its session_id.
        # Cleared on workflow_finished. Used by on_activity_recorded to
        # locate the session to append into.
        self._current_session_id: Optional[str] = None
        # True after any successful write — drives the self-heal
        # decision. False = "file legitimately doesn't exist yet";
        # True = "file existed last we knew, so missing-on-write means
        # vanished out-of-band → rewrite from model."
        self._has_been_written: bool = False
        # True when a prior write (append or rewrite) failed after the
        # in-memory model had already been updated, so disk now lags
        # memory. The next write does a full atomic rewrite from the
        # model to reconcile, rather than a plain append onto a stale
        # file. Without this, a transient append failure (external
        # lock, brief disk-full, network-share hiccup) while the file
        # still exists would drop the entry from disk permanently — the
        # vanish-only self-heal below never re-emits it. Cleared on any
        # successful write.
        self._resync_pending: bool = False
        # Cached file size in bytes. Refreshed after every write so
        # fileSizeChanged consumers see a current value.
        self._file_size: int = 0

        # The model. Parented to ``self`` so Qt's ownership chain
        # keeps it alive for the SessionLog's lifetime.
        self._model = SessionLogModel(parent=self)

        # Initial load from disk. Populates self._sessions, the
        # model, file_size, and _has_been_written.
        self._initial_load()

    # --- QML-visible properties ------------------------------------------

    @Property(QObject, constant=True)
    def model(self) -> SessionLogModel:
        """The list model bound by the Session Log page."""
        return self._model

    @Property(str, constant=True)
    def filePath(self) -> str:
        """Absolute path to the session log file. For the page's path label."""
        return str(self._file_path)

    @Property(QUrl, constant=True)
    def directoryUrl(self) -> QUrl:
        """``QUrl`` pointing at the containing directory.

        For QML's ``Qt.openUrlExternally`` to drive the "reveal in
        folder" affordance. Mirrors the
        :attr:`CryoActivitiesController.templatesDirUrl` pattern.
        """
        return QUrl.fromLocalFile(str(self._log_dir))

    @Property(str, constant=True)
    def displayPath(self) -> str:
        """Truncated path for the "Saved to:" label on the page.

        Format: ``"...{sep}<project_root>{sep}<log_dir_name>{sep}<filename>"``
        where ``{sep}`` is the platform path separator — backslash
        on Windows, forward slash on Unix. The leading ``"..."``
        signals that the absolute prefix is intentionally hidden;
        anyone needing the full path gets it from the tooltip
        (which binds to :attr:`filePath`).

        The "project root" is :attr:`_log_dir`'s parent directory —
        the natural anchor since :attr:`_log_dir` is constructed
        as ``<project_root>/session_logs``. For tests or
        non-default ``log_dir`` values, this still works: the
        parent's directory name is shown regardless of where the
        log lives. ``"..."`` is constant since the path is
        constant — no ``Changed`` signal needed.
        """
        sep = os.sep
        project_root_name = self._log_dir.parent.name
        log_dir_name = self._log_dir.name
        file_name = self._file_path.name
        return f"...{sep}{project_root_name}{sep}{log_dir_name}{sep}{file_name}"

    @Property(int, notify=fileSizeChanged)
    def fileSize(self) -> int:
        """Current size of the log file in bytes.

        Refreshed after every write attempt. ``0`` when the file
        doesn't exist (first run, or after a manual clear). Used by
        the Settings page's size readout — the cue that pairs with
        the guarded delete control.
        """
        return self._file_size

    # --- QML-callable formatting helpers ---------------------------------
    #
    # The model stores canonical values (raw SI for numerics, ISO
    # strings for timestamps, dicts for params, snake_case ids for
    # workflows / activities). Display formatting lives here so the
    # delegate is one HTML/QML layer thinking about presentation,
    # not five. Rule-of-three applies: extract to a shared utils
    # module if a third surface needs them.

    @Slot(float, result=str)
    def formatDuration(self, seconds: float) -> str:
        """Format a duration in seconds as ``"X s"`` or ``"X min Y s"``.

        Duplicates the same shape as :func:`workflows.runner._format_duration`
        rather than importing across packages — the function is tiny,
        the duplication is bounded at two call sites, and a single
        shared utils module isn't yet warranted (rule of three).
        """
        try:
            total_seconds = round(float(seconds))
        except (TypeError, ValueError):
            return ""
        if total_seconds < 60:
            return f"{total_seconds} s"
        minutes, secs = divmod(total_seconds, 60)
        return f"{minutes} min {secs} s"

    @Slot(str, result=str)
    def formatTimestamp(self, iso: str) -> str:
        """Format an ISO 8601 local timestamp for display.

        Returns ``"DD-MM-YYYY HH:MM:SS"`` for every timestamp,
        regardless of recency. Day-month-year ordering matches the
        scientific / international convention; the same dash
        separator the storage format uses keeps the display
        visually consistent with the underlying ISO strings.

        Returns the input unchanged if parsing fails — better to
        show the raw ISO than to crash or hide it. Empty input
        returns empty string.
        """
        if not iso:
            return ""
        try:
            dt = datetime.fromisoformat(iso)
        except (TypeError, ValueError):
            return iso
        return dt.strftime("%d-%m-%Y %H:%M:%S")

    @Slot(str, result=str)
    def formatWorkflowName(self, workflow_id: str) -> str:
        """``"cryo_prep"`` → ``"Cryo Prep"``, etc.

        Falls back to a title-cased version of the id for any
        unknown workflow — so a future workflow shows up readable
        without requiring a code change here.
        """
        known = {
            "rt_prep": "RT Prep",
            "cryo_prep": "Cryo Prep",
        }
        if workflow_id in known:
            return known[workflow_id]
        return workflow_id.replace("_", " ").title() if workflow_id else ""

    @Slot(str, result=str)
    def formatActivityName(self, activity_id: str) -> str:
        """``"sputter_coat"`` → ``"Sputter Coat"``, etc.

        Same fallback shape as :meth:`formatWorkflowName` — a future
        activity gets a readable rendering automatically.
        """
        known = {
            "sputter_coat": "Sputter Coat",
            "gis_deposition": "GIS Deposition",
            "gis_purge": "GIS Purge",
            "home_stage": "Home Stage",
            # Synthetic record emitted when the end-of-run PFIB
            # conditions restore fails or is abandoned (not a real
            # activity — see CPWorkflow._after_run).
            "pfib_restore": "PFIB Restore",
        }
        if activity_id in known:
            return known[activity_id]
        return activity_id.replace("_", " ").title() if activity_id else ""

    @Slot(str, "QVariant", result=str)
    def formatParameters(self, activity_id: str, params: Any) -> str:
        """Format an activity's parameter dict as a one-line display string.

        Per-activity-type formatting because parameter shapes differ —
        Sputter Coat speaks positions and pA, GIS Deposition speaks
        port names and seconds, Home Stage speaks a single bool. The
        delegate doesn't care; it just shows what comes back.

        Unknown activity types get a generic ``key=value`` rendering,
        which keeps the page informative for any future activity
        without a code change here.
        """
        if not params:
            return ""
        # QML passes ``params`` across the Slot boundary as a
        # ``QJSValue`` wrapper, not the raw Python dict that the
        # model stores. Unwrap explicitly via ``toVariant()``; without
        # this, ``isinstance(params, dict)`` falls through and the
        # delegate shows ``<PySide6.QtQml.QJSValue object at 0x...>``
        # instead of the formatted parameter line.
        if isinstance(params, QJSValue):
            params = params.toVariant()
        if not isinstance(params, dict):
            return str(params)

        if activity_id == "sputter_coat":
            parts = []
            if "position_name" in params:
                parts.append(str(params["position_name"]))
            if "ion_species" in params:
                parts.append(str(params["ion_species"]))
            if "ion_current_a" in params:
                parts.append(_format_ion_current(params["ion_current_a"]))
            if "duration_s" in params:
                parts.append(f"{params['duration_s']} s")
            if params.get("chamber_recovery_s"):
                parts.append(f"recovery {params['chamber_recovery_s']} s")
            return ", ".join(parts)

        if activity_id == "gis_deposition":
            parts = []
            if "gis_port_name" in params:
                parts.append(str(params["gis_port_name"]))
            if "position_name" in params:
                parts.append(str(params["position_name"]))
            if "duration_s" in params:
                parts.append(f"{params['duration_s']} s")
            if params.get("chamber_recovery_s"):
                parts.append(f"recovery {params['chamber_recovery_s']} s")
            return ", ".join(parts)

        if activity_id == "gis_purge":
            parts = []
            if "gis_port_name" in params:
                parts.append(str(params["gis_port_name"]))
            if "purge_duration_s" in params:
                parts.append(f"{params['purge_duration_s']} s")
            if params.get("chamber_recovery_s"):
                parts.append(f"recovery {params['chamber_recovery_s']} s")
            return ", ".join(parts)

        # Home Stage intentionally renders no parameter line. The
        # activity takes no parameters (``parameter_summary()`` returns
        # ``{}``); whether the stage returns to its original position is
        # a workflow-level concern — the runner's StageRecorder, driven
        # by the "Move Stage To Original Position" setting — not
        # something this per-activity line should report. Its empty
        # params dict falls through to the generic rendering below and
        # yields "". (A former ``move_to_original`` param branch lived
        # here; it was removed with the activity parameter.)

        # Unknown activity type — generic rendering. Sorted keys so
        # the output is stable across runs.
        return ", ".join(
            f"{k}={v}" for k, v in sorted(params.items())
        )

    @Slot(int, result=str)
    def formatFileSize(self, num_bytes: int) -> str:
        """Format a byte count as a human-readable file size.

        Returns ``"X B"`` for < 1 KiB, ``"X.X KB"`` for < 1 MiB,
        ``"X.X MB"`` for < 1 GiB, ``"X.X GB"`` otherwise. Uses
        1024-based units under the casual ``KB`` / ``MB`` / ``GB``
        labels — the convention most file managers use (Windows
        Explorer, macOS Finder), and the one the user will be most
        familiar with when comparing against what the OS shows for
        the same file on disk.

        Used by the Settings page's "Session Log Size" readout,
        which binds to :attr:`fileSize` (the live, notifiable byte
        count) and re-renders through this slot whenever a write
        happens.
        """
        try:
            n = float(num_bytes)
        except (TypeError, ValueError):
            return "0 B"
        if n < 1024:
            return f"{int(n)} B"
        if n < 1024 ** 2:
            return f"{n / 1024:.1f} KB"
        if n < 1024 ** 3:
            return f"{n / (1024 ** 2):.1f} MB"
        return f"{n / (1024 ** 3):.1f} GB"

    # --- QML-callable slots (mutating) -----------------------------------

    @Slot(str, str)
    def editNote(self, session_id: str, new_notes: str) -> None:
        """Update a session's notes field. Atomic-rewrite path.

        The only user-mutable field in the entire log (factual
        fields are write-once). Updates the in-memory session, the
        model row, then triggers a full atomic rewrite so the disk
        catches up. Crash-safe via the ``.tmp`` + ``os.replace``
        pattern in :func:`atomic_rewrite`.

        A note edit on a session that doesn't exist (stale id from
        a manually-edited file) drops with a warning. Best-effort
        per the architecture.
        """
        target = self._find_session(session_id)
        if target is None:
            logger.warning(
                "SessionLog.editNote: unknown session_id=%r; ignoring",
                session_id,
            )
            return

        # In-memory update first (UI's source of truth).
        idx = self._sessions.index(target)
        updated = replace(target, notes=new_notes)
        self._sessions[idx] = updated

        # Update the corresponding model row. _row_for preserves the
        # live badge if the user is annotating the running session.
        row_idx = self._model.find_session_row(session_id)
        if row_idx >= 0:
            self._model.update_row_at(
                row_idx, self._row_for(updated),
            )

        # Then the disk rewrite. Failure logs and emits writeFailed
        # but leaves the in-memory note edit visible to the user.
        self._safe_rewrite_all()

    @Slot()
    def clearAll(self) -> None:
        """Clear the entire log — for the Settings page guarded delete.

        Atomic-rewrites the file to empty, resets the model and
        in-memory state, and clears ``_has_been_written`` so the
        next event-driven write looks like a fresh install rather
        than a self-heal trigger.
        """
        logger.info("SessionLog: clearing entire log (user-requested)")
        # In-memory state first.
        self._sessions = []
        self._current_session_id = None
        # Model.
        self._model.replace_all([])
        # Disk. Only reset _has_been_written to "fresh install" if the
        # rewrite-to-empty actually succeeded. On failure the old
        # non-empty file is still fully intact on disk, so forcing the
        # flag False would disarm the self-heal-on-vanish path AND make
        # the next append plain-append a new session onto the stale
        # content. Leaving the flag (and the _resync_pending flag that
        # _safe_rewrite_all set) untouched means the next write does a
        # full rewrite from the now-empty model, dropping the old
        # content the user asked to delete.
        if self._safe_rewrite_all():
            self._has_been_written = False

    # --- Runner-signal handlers (wired by AppController) -----------------

    @Slot(str)
    def on_workflow_started(self, workflow_id: str) -> None:
        """Open a new session. Called from ``WorkflowRunner.workflowStarted``.

        Defensive: if a previous session never received its
        ``workflow_finished`` (shouldn't happen given the cross-page
        workflow lock, but defense-in-depth), the new session opens
        and the previous one stays in the model as effectively
        interrupted — the disk record will reflect the same shape
        because no session_end was written for it. Its model row is
        re-rendered below so the badge flips from "Running" to
        "Interrupted" immediately, matching what reconciliation
        would show after a restart; without that re-render the
        orphan's last-built row would keep a stale ``is_running``
        for the rest of the app session.
        """
        orphan_id = self._current_session_id
        if orphan_id is not None:
            logger.warning(
                "SessionLog: workflow_started while session %r still "
                "open; leaving it interrupted and opening a new one",
                orphan_id,
            )

        session_id = _new_session_id()
        started_at = _now_iso_local()
        entry = SessionBeginEntry(
            session_id=session_id,
            workflow_id=workflow_id,
            started_at=started_at,
            notes="",
        )

        # In-memory: build a fresh Session view object, append.
        session = Session(
            session_id=session_id,
            workflow_id=workflow_id,
            started_at=started_at,
            notes="",
            activities=[],
            ended_at=None,
            all_complete=None,
            total_duration_s=None,
        )
        self._sessions.append(session)
        # Pointer assignment must precede the append_row below —
        # _row_for derives is_running from the pointer, and the new
        # session's first render must badge as "Running".
        self._current_session_id = session_id

        # Model.
        self._model.append_row(self._row_for(session))

        # Orphan re-render (defensive path only). The pointer now
        # targets the new session, so _row_for(orphan) computes
        # is_running=False naturally — same single source of truth,
        # no special-case flag. Nothing on disk changes (the orphan
        # already has no session_end), so no write is needed.
        if orphan_id is not None:
            orphan = self._find_session(orphan_id)
            if orphan is not None:
                orphan_row_idx = self._model.find_session_row(orphan_id)
                if orphan_row_idx >= 0:
                    self._model.update_row_at(
                        orphan_row_idx, self._row_for(orphan),
                    )

        # Disk. Append is the common path; self-heal handled inside
        # _safe_append_entry.
        self._safe_append_entry(entry)

    @Slot(str, bool, float)
    def on_workflow_finished(
        self,
        workflow_id: str,
        all_complete: bool,
        total_duration_s: float,
    ) -> None:
        """Close the current session. Called from ``workflowFinished``.

        ``workflow_id`` is forwarded for log forensics (the actual
        link is via :attr:`_current_session_id`); ``total_duration_s``
        comes from the runner's :meth:`totalDuration` accessor read
        by the wiring layer.

        If no session is currently open (shouldn't happen — workflow
        lifecycle is paired by construction), logs and ignores: we
        can't synthesize a session_end without a session_id to bind
        it to.
        """
        if self._current_session_id is None:
            logger.warning(
                "SessionLog: workflow_finished with no current session; "
                "ignoring (workflow_id=%r)", workflow_id,
            )
            return

        ended_at = _now_iso_local()
        session_id = self._current_session_id

        # In-memory update: find the current session and replace it
        # with one carrying the end fields populated.
        target = self._find_session(session_id)
        if target is None:
            # Defense — this would mean current_session_id pointed at
            # a session that's no longer in self._sessions. Pathological
            # but recoverable: just clear the pointer and bail.
            logger.error(
                "SessionLog: current_session_id=%r not in sessions list; "
                "clearing", session_id,
            )
            self._current_session_id = None
            return

        idx = self._sessions.index(target)
        updated = replace(
            target,
            ended_at=ended_at,
            all_complete=all_complete,
            total_duration_s=total_duration_s,
        )
        self._sessions[idx] = updated

        # Model: update the session header row in place. _row_for
        # computes is_running=False here even though the pointer
        # hasn't been cleared yet — ``updated.ended_at`` is already
        # populated, and the helper requires both conditions. See
        # _row_for's docstring; the pointer must stay set until
        # after _safe_append_entry below.
        row_idx = self._model.find_session_row(session_id)
        if row_idx >= 0:
            self._model.update_row_at(
                row_idx, self._row_for(updated),
            )

        # Disk.
        entry = SessionEndEntry(
            session_id=session_id,
            ended_at=ended_at,
            all_complete=all_complete,
            total_duration_s=total_duration_s,
        )
        self._safe_append_entry(entry)

        # Clear the pointer last — _safe_append_entry might self-heal,
        # which serializes the current state, and we want the just-
        # closed session to be represented as ended in that serialization.
        self._current_session_id = None

    @Slot(str, str, str, float, str, object)
    def on_activity_recorded(
        self,
        activity_id: str,
        instance_id: str,
        result: str,
        duration_s: float,
        last_status: str,
        params: Dict[str, Any],
    ) -> None:
        """Record one finished activity. Called from ``activityRecorded``.

        Activities that arrive when no session is open are dropped
        with a warning — the writer always emits ``workflowStarted``
        first, so an activity with no open session is an out-of-order
        or out-of-band signal.
        """
        if self._current_session_id is None:
            logger.warning(
                "SessionLog: activity_recorded with no current session; "
                "dropping (activity_id=%r, instance_id=%r)",
                activity_id, instance_id,
            )
            return

        session_id = self._current_session_id
        finished_at = _now_iso_local()

        entry = ActivityLogEntry(
            session_id=session_id,
            finished_at=finished_at,
            activity_id=activity_id,
            instance_id=instance_id,
            result=result,
            duration_s=duration_s,
            last_status=last_status,
            params=dict(params) if params else {},
            notes="",
        )

        # In-memory: append to the current session's activities list.
        target = self._find_session(session_id)
        if target is None:
            logger.error(
                "SessionLog: current_session_id=%r not in sessions; "
                "dropping activity (activity_id=%r)",
                session_id, activity_id,
            )
            return
        target.activities.append(entry)

        # Model: update the session row in place. The fresh row's
        # ``activities`` list (built from ``target.activities``) now
        # includes the new entry; the row-level ``dataChanged`` signal
        # propagates to the delegate's ``Repeater`` over the activities,
        # which renders the new activity inline within the session
        # card. A single mutation suffices because the activities list
        # is nested in the session row rather than held as separate
        # rows.
        row_idx = self._model.find_session_row(session_id)
        if row_idx >= 0:
            self._model.update_row_at(
                row_idx, self._row_for(target),
            )

        # Disk.
        self._safe_append_entry(entry)

    # --- Internal helpers ------------------------------------------------

    def _initial_load(self) -> None:
        """Populate self._sessions and the model from disk at startup.

        Empty file / missing file is the normal first-run case and
        produces an empty model with no error. Corrupt / partial
        files degrade gracefully via the tolerant
        :func:`load_all` + :func:`entry_from_dict` layers — survivors
        get reconciled, anomalies log warnings.
        """
        raw_records = load_all(self._file_path)

        loaded_entries: List[LogEntry] = []
        for d in raw_records:
            try:
                loaded_entries.append(entry_from_dict(d))
            except ValueError as exc:
                logger.warning(
                    "SessionLog: skipping invalid entry on load (%s)", exc,
                )

        # Reconcile produces sessions in chronological / file order;
        # this codebase's design choice is to display chronologically
        # (oldest at top, newest at bottom — journalctl-style), so we
        # use the reconciled list directly without reversal.
        self._sessions = reconcile(loaded_entries)

        # Build the model rows — one per session. Activities are
        # nested inside each session's row dict (via _row_for), so a
        # simple list comprehension is all this needs. The pointer is
        # None at startup, so every loaded row gets
        # is_running=False — a crash-orphaned session (begin entry,
        # no end entry) renders "Interrupted", never "Running".
        rows = [self._row_for(s) for s in self._sessions]
        self._model.replace_all(rows)

        # _has_been_written reflects whether the file exists on disk
        # right now. We use file existence, not "did we load any
        # entries," because an empty (but present) file should still
        # trigger self-heal on subsequent vanish — the file existed,
        # the act of writing to it succeeded at some point.
        self._has_been_written = self._file_path.exists()
        self._refresh_file_size()

    def _find_session(self, session_id: str) -> Optional[Session]:
        """Linear scan of self._sessions for the matching id, or None."""
        for s in self._sessions:
            if s.session_id == session_id:
                return s
        return None

    def _row_for(self, session: Session) -> Dict[str, Any]:
        """Build the model row for ``session``, deriving ``is_running``.

        The single chokepoint between :class:`Session` and the model;
        :meth:`SessionLogModel.session_to_row` enforces it with a
        required ``is_running`` keyword.

        ``is_running`` is true only when *both* hold:

        * ``session_id`` matches :attr:`_current_session_id` — the
          one piece of runtime state the disk knows nothing about.
        * ``ended_at is None`` — the session hasn't been closed.

        The second clause is load-bearing: :meth:`on_workflow_finished`
        clears the pointer *last* (after :meth:`_safe_append_entry`, so
        a self-heal rewrite serializes the session as ended), so its
        row update runs while the pointer still targets the closing
        session. ``ended_at`` is already populated by then.
        """
        is_running = (
            session.session_id == self._current_session_id
            and session.ended_at is None
        )
        return SessionLogModel.session_to_row(session, is_running=is_running)

    def _serialize_all(self) -> List[Dict[str, Any]]:
        """Serialize the entire in-memory model to entry dicts.

        Used by the atomic-rewrite paths (note edit, clearAll,
        self-heal). Output order is chronological — same as the file
        on disk — by walking ``self._sessions`` in order and emitting,
        per session: the ``SessionBeginEntry``, then activity entries
        in order, then the ``SessionEndEntry`` if the session is
        closed (interrupted sessions intentionally have no end entry).
        """
        out: List[Dict[str, Any]] = []
        for session in self._sessions:
            out.append(SessionBeginEntry(
                session_id=session.session_id,
                workflow_id=session.workflow_id,
                started_at=session.started_at,
                notes=session.notes,
            ).to_dict())
            for activity in session.activities:
                out.append(activity.to_dict())
            if session.ended_at is not None:
                out.append(SessionEndEntry(
                    session_id=session.session_id,
                    ended_at=session.ended_at,
                    all_complete=bool(session.all_complete),
                    total_duration_s=float(session.total_duration_s or 0.0),
                ).to_dict())
        return out

    def _safe_append_entry(self, entry: LogEntry) -> None:
        """Append entry; self-heal if disk has fallen behind the model.

        Common path is a plain :func:`append_record`. A full
        :func:`atomic_rewrite` from the in-memory model is substituted
        in two cases:

        * **File vanished out-of-band** (``_has_been_written`` is set
          but the file is missing). The intentional-clear path is
          :meth:`clearAll`; a quiet removal is not honored.
        * **A prior write failed** (``_resync_pending``), so disk lags
          the model. The rewrite re-emits every entry still in memory.

        Callers update ``self._sessions`` before invoking this, so
        ``_serialize_all()`` already includes ``entry``; the rewrite
        must not append it again.

        ``OSError`` failures log, emit :attr:`writeFailed`, and arm
        ``_resync_pending``. The in-memory model and the running
        workflow are unaffected.
        """
        try:
            vanished = self._has_been_written and not self._file_path.exists()
            if self._resync_pending or vanished:
                logger.warning(
                    "SessionLog: %s; self-healing via full rewrite "
                    "from in-memory model",
                    "file vanished out-of-band" if vanished
                    else "prior write failed",
                )
                atomic_rewrite(self._file_path, self._serialize_all())
            else:
                append_record(self._file_path, entry.to_dict())
            self._has_been_written = True
            self._resync_pending = False
        except OSError as exc:
            logger.exception(
                "SessionLog: write failed (%s)", exc,
            )
            # Transient I/O failure: disk now lags the in-memory model;
            # arm a full rewrite on the next write so the dropped entry
            # is re-emitted rather than lost permanently.
            self._resync_pending = True
            # Short status bar, console log for the why: the
            # logger.exception above already captured the full
            # traceback and exception text. The user-facing message
            # stays terse — same convention as
            # StageScanController._on_rotate_finished.
            self.writeFailed.emit("Session log not saved (see console log)")
        except (TypeError, ValueError) as exc:
            # A non-JSON-serializable record (e.g. a set / datetime in
            # params) — shouldn't happen, since parameter_summary returns
            # JSON primitives by contract, but it would otherwise escape
            # this same-thread slot. Deliberately do NOT arm
            # _resync_pending: re-serializing the same bad record on the
            # next write would fail identically, and on the resync /
            # file-vanished path that re-serialize would be the *whole
            # model*, so a single bad record would then block every write.
            # Leaving the flag clear keeps the common plain-append path
            # working for later (serializable) entries; the unserializable
            # one is simply absent from disk. (If a record type ever could
            # carry non-JSON params, the right fix is to sanitize at the
            # source — json.dumps(default=str) — not to arm a resync that
            # cannot succeed.)
            logger.exception(
                "SessionLog: could not serialize entry; dropping it (%s)",
                exc,
            )
            self.writeFailed.emit("Session log not saved (see console log)")
        finally:
            # Refresh size on every attempt — a partial write that
            # raised OSError might still have changed the file size,
            # and the UI's cue should match disk reality.
            self._refresh_file_size()

    def _safe_rewrite_all(self) -> bool:
        """Atomic-rewrite the file from the full in-memory model.

        Used by :meth:`editNote` and :meth:`clearAll`. Same failure
        isolation as :meth:`_safe_append_entry`: log + emit
        :attr:`writeFailed`, don't propagate. The atomic-rewrite
        primitive itself is crash-safe via the ``.tmp`` +
        ``os.replace`` pattern.

        Returns ``True`` if the rewrite succeeded, ``False`` if it
        failed. Callers that gate state transitions on the on-disk
        result (e.g. :meth:`clearAll` resetting ``_has_been_written``)
        must check this; a failed rewrite leaves the old file intact on
        disk and arms ``_resync_pending`` so the next write reconciles.
        """
        try:
            atomic_rewrite(self._file_path, self._serialize_all())
            self._has_been_written = self._file_path.exists()
            self._resync_pending = False
            return True
        except OSError as exc:
            logger.exception(
                "SessionLog: atomic rewrite failed (%s)", exc,
            )
            # Disk still holds the pre-rewrite content; arm a full
            # rewrite on the next write to reconcile.
            self._resync_pending = True
            # See sibling comment in _safe_append_entry: short status
            # bar text, console log carries the exception detail.
            self.writeFailed.emit("Session log not saved (see console log)")
            return False
        finally:
            self._refresh_file_size()

    def _refresh_file_size(self) -> None:
        """Update :attr:`_file_size` from disk and emit if changed."""
        try:
            new_size = (
                self._file_path.stat().st_size
                if self._file_path.exists() else 0
            )
        except OSError:
            new_size = 0
        if new_size != self._file_size:
            self._file_size = new_size
            self.fileSizeChanged.emit()