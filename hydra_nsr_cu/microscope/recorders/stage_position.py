"""Records and restores the stage position.

Captures the full stage position (all five axes) at a point in time
and moves the stage back to it on demand. Used by the workflow
runners when the "Move Stage To Original Position" setting is
enabled: capture before the activity loop, restore after a
*successful* run so the stage ends where the session began.

Why hold the position object verbatim
--------------------------------------
Unlike :class:`PFIBConditionsRecorder`, which decomposes ion-beam
state into scalar fields, this recorder captures one atomic value —
the object returned by ``stage.current_position`` — and replays it
unchanged to ``stage.absolute_move``. Two reasons:

* **Safety.** Reconstructing the position via
  ``stage.make_position(x=..., y=..., ...)`` would route the restore
  through the same factory whose per-axis ``None`` defaults are a
  known collision hazard if an axis is ever accidentally dropped.
  Replaying the captured object can't omit an axis — it's the exact
  position the hardware reported.
* **Coordinate-frame fidelity.** ``current_position`` returns a
  position in the stage's active coordinate system; passing that
  same object back to ``absolute_move`` preserves the frame.
  Decomposing to floats and rebuilding would re-enter whatever the
  *default* coordinate system is at restore time — correct only so
  long as nobody calls ``set_default_coordinate_system`` between
  capture and restore. Replaying the object sidesteps that latent
  fragility entirely.

Restore is a single blocking ``absolute_move``. It is not
interruptible mid-move, but a single absolute move is fast on real
hardware and trivial on the simulated
client. The *policy* decision of whether to restore at all (only on
a fully successful run — never after a stop or exception) lives in
the workflow runner, not here; this recorder just performs the
capture and the move when asked.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Any

from ..stage_ops import StageOpsLike

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class StagePositionSnapshot:
    """Opaque snapshot of a full stage position.

    Holds the value object returned by ``stage.current_position``
    verbatim (a real AutoScript ``StagePosition`` or a
    :class:`SimulatedStagePosition`). Workflows treat it as opaque —
    capture, hold, restore — without reconstructing it.

    The ``__repr__`` decomposes the position's axes for the log trail
    without the snapshot depending on a particular concrete position
    type; it reads ``.x/.y/.z/.r/.t`` defensively since both backing
    types expose those attributes. A captured position always has
    concrete floats on every axis (``None`` only appears in
    *commanded* positions), but the formatter tolerates ``None``
    anyway so a malformed capture can't break logging.
    """
    position: Any

    def __repr__(self) -> str:
        p = self.position

        def _mm(v: Any) -> str:
            return "?" if v is None else f"{v * 1e3:.3f}mm"

        def _deg(v: Any) -> str:
            return "?" if v is None else f"{math.degrees(v):.2f}deg"

        try:
            return (
                f"StagePositionSnapshot("
                f"x={_mm(getattr(p, 'x', None))}, "
                f"y={_mm(getattr(p, 'y', None))}, "
                f"z={_mm(getattr(p, 'z', None))}, "
                f"r={_deg(getattr(p, 'r', None))}, "
                f"t={_deg(getattr(p, 't', None))})"
            )
        except Exception:
            # A repr must never raise — fall back to the raw object.
            return f"StagePositionSnapshot(position={p!r})"


class StageRecorder:
    """Captures and restores the stage position."""

    def __init__(self, stage: StageOpsLike) -> None:
        self._stage = stage

    def capture(self) -> StagePositionSnapshot:
        """Read the current stage position into a snapshot.

        The read goes through the ops layer (a thin wrapper over
        AutoScript on the real client). Failures propagate to the
        caller — the workflow decides whether to proceed without a
        restore.
        """
        snapshot = StagePositionSnapshot(
            position=self._stage.current_position,
        )
        logger.info("Stage position captured: %r", snapshot)
        return snapshot

    def restore(self, snapshot: StagePositionSnapshot) -> None:
        """Move the stage back to a previously-captured position.

        A single blocking ``absolute_move`` to the captured position
        object. Not interruptible mid-move. The caller owns the policy
        decision of *whether* to restore — the workflow runners restore
        only on a fully successful run, never after a stop or exception,
        since commanding further motion after an abnormal exit is
        unsafe.
        """
        logger.info("Stage position restoring: %r", snapshot)
        self._stage.absolute_move(snapshot.position)
        logger.info("Stage position restore complete")