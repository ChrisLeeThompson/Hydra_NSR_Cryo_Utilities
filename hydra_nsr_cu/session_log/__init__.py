"""Session log — durable provenance logging for workflows.

A user-facing backup for the operator who forgot to save a template
before running a session. Captures, per workflow run, what was
actually done: which activities ran, what parameters they used, how
each finished, how long each took, and the same for the whole
session. Complements the Templates feature (which is a *plan*); the
session log is an automatic, time-anchored *outcome* record.

Scope and tone
--------------
High-level events only — never a second console log. Low-stakes,
unbounded growth (size is negligible at this granularity), and
observational: nothing in this package is ever load-bearing for a
workflow. Write failures must never abort a run; the in-memory model
is the UI's source of truth and the file is a best-effort durable
mirror.

Modules
-------
* :mod:`records` — JSON-serializable dataclasses for the three entry
  kinds (``session_begin``, ``session_end``, ``activity``) plus a
  discriminator-based dispatcher (:func:`records.entry_from_dict`).
  Naming uses *Entry* rather than *Record* to avoid collision with
  :data:`hydra_nsr_cu.cryo.activity_records.ActivityRecord`, and to
  reflect a real domain distinction: cryo *Records* describe activities
  to RUN; session-log *Entries* record activities that HAVE RUN.

* :mod:`persistence` — pure file operations over record dicts:
  tolerant line-by-line load, single-line append (the crash-durable
  common path), and atomic full rewrite (the rare note-edit path).
  No Qt, no model awareness. Step-4 self-heal composes these.

* :mod:`reconciliation` — pure function that pairs entries into
  :class:`reconciliation.Session` view objects with their activity
  children attached and interrupted sessions detected.

* :mod:`model` — :class:`model.SessionLogModel`, a flat
  :class:`QAbstractListModel` whose rows interleave session headers
  and their activity children in chronological order. Delegates
  branch on the ``kind`` role.

* :mod:`controller` — :class:`controller.SessionLog`, the QObject
  that owns the model, the file path, and the runner-signal
  handlers. Constructed eagerly in :class:`AppController` (no
  microscope dependency); runner signals are wired later in
  ``_wire_workflow`` once the workflow runners exist.
"""