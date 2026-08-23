"""Session log — durable provenance logging for workflows.

A user-facing backup for the operator who forgot to save a template
before running a session. Captures, per workflow run, what was
actually done: which activities ran, what parameters they used, how
each finished, how long each took, and the same for the whole
session. Complements the Templates feature (which is a *plan*); the
session log is an automatic, time-anchored *outcome* record.

Invariants
----------
High-level events only — never a second console log. Observational,
never load-bearing: a write failure must never abort a run. The
in-memory model is the UI's source of truth; the file is a
best-effort durable mirror.

Modules
-------
* :mod:`records` — JSON-serializable dataclasses for the three entry
  kinds (``session_begin``, ``session_end``, ``activity``) and the
  ``kind`` dispatcher :func:`records.entry_from_dict`. *Entry* (an
  activity that has run) is deliberately distinct from the cryo
  package's *Record* (an activity to run).
* :mod:`persistence` — pure JSONL file operations: tolerant load,
  single-line append, atomic full rewrite. No Qt, no model awareness.
* :mod:`reconciliation` — pure function pairing entries into
  :class:`reconciliation.Session` objects with activity children
  attached and interrupted sessions detected.
* :mod:`model` — :class:`model.SessionLogModel`, a
  :class:`QAbstractListModel` with one row per session; each row
  exposes its activities as a nested list via the ``activities`` role.
* :mod:`controller` — :class:`controller.SessionLog`, the QObject that
  owns the model, the file path, and the runner-signal handlers.
"""