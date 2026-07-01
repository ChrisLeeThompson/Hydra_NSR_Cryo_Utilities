"""Stage / Scan page controller.

Manual-assist surface that backs the Stage / Scan page: stage rotation,
scan rotate SEM and FIB, and the Stage Z slider. Operations are direct
user gestures rather than workflow-style activities, so this subpackage
has no activity records, no recorder, and no per-step state capture.

Consumers import :class:`StageScanController` directly from
``.controller`` — no re-exports here.
"""