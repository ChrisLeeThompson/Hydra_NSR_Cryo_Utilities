"""Records and restores the stage position.

Captures the full stage position (all five axes) at a point in time
and moves the stage back to it on demand. Used by the workflow
runners when the "Move Stage To Original Position" setting is
enabled: capture before the activity loop, restore after a
*successful* run so the stage ends where the session began.

Capture holds the object returned by ``stage.current_position``
verbatim. Restore converts it to a :class:`StageTarget`, which
requires all five axes to be present and finite, so the return move
can never drop an axis (an unset axis is a collision hazard; see
``stage_ops.py``). Both the read and the command use the coordinate
system set once at connect (RAW), so the round trip through floats
keeps the frame.

The restore is a staged move with arrival checks (see
``microscope/stage_moves.py``): tilt to 0°, X/Y/Z/R, tilt to the
captured tilt, each stage read back. The *policy* decision of whether
to restore at all (only on a fully successful run, never after a stop
or exception) lives in the workflow runner, not here; this recorder
just performs the capture and the move when asked.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Any, Callable, Optional

from ..stage_moves import StageTarget, staged_move
from ..stage_ops import StageOpsLike

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class StagePositionSnapshot:
    """Opaque snapshot of a full stage position.

    Holds the value object returned by ``stage.current_position``
    verbatim (a real AutoScript ``StagePosition`` or a
    :class:`SimulatedStagePosition`). Workflows treat it as opaque:
    capture, hold, restore.

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

    def restore(
        self,
        snapshot: StagePositionSnapshot,
        *,
        staged: bool = True,
        should_abandon: Optional[Callable[[], bool]] = None,
        on_status: Optional[Callable[[str], None]] = None,
    ) -> bool:
        """Move the stage back to a previously captured position.

        A staged move (tilt to 0°, X/Y/Z/R, tilt) when ``staged`` is
        True, one five-axis move otherwise; every stage ends with an
        arrival check, so a :class:`StageMoveShortfall` or a hardware
        error propagates. Returns False when ``should_abandon()``
        turned true between stages (nothing further is commanded),
        True when the stage arrived. A captured position missing an
        axis raises ``ValueError`` before anything moves.
        """
        target = StageTarget.from_position(snapshot.position)
        logger.info("Stage position restoring: %r", snapshot)
        arrived = staged_move(
            self._stage, target, staged=staged, name="original position",
            on_status=on_status, should_stop=should_abandon,
        )
        if arrived:
            logger.info("Stage position restore complete")
        return arrived
