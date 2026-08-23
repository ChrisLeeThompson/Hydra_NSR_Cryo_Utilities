"""Stage Positions — saved positions feature.

The :class:`StagePositionsController` and its underlying
:class:`StagePositionsModel` manage the list of named stage positions
that persists across sessions. Activities that need to target specific
stage positions (GIS Deposition, for example)
reference saved positions by stable id rather than by index, so the
reference survives any reordering.

Persistence
-----------
Positions are serialized as a JSON-encoded string in a single
QSettings key (``v3/stagePositions/list``). JSON was chosen over
QSettings' native array support so the same code path can later
serialize positions into workflow template files; the format is
intentionally portable.
"""