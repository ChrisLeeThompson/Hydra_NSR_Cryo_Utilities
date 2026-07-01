"""Cryo Prep — workflow, activities model, and supporting state.

The Cryo Prep page is structurally different from RT Prep: the user
composes a sequence of activities (Sputter Coat, GIS Deposition) with
drag-and-drop reordering, and can have multiple instances of the same
activity type with different parameters.

This package owns:

* :mod:`activity_records` — JSON-serializable parameter records, one
  per activity type. These are the canonical state of a Cryo workflow.
* :mod:`activities_model` — :class:`QAbstractListModel` of activity
  records, exposed to the QML drag-and-drop ListView. Drives the
  whole Cryo page.
* :mod:`controller` — :class:`CryoActivitiesController`, the QML-facing
  wrapper that owns the model, handles persistence, and exposes
  parameter bounds and per-activity-type defaults.

Defaults for activity parameters and the initial activity list live in
:mod:`hydra_nsr_cu.defaults` alongside other app-wide defaults.

Workflow execution is in :mod:`hydra_nsr_cu.workflows.cp_workflow`,
arriving in Session 5D.
"""