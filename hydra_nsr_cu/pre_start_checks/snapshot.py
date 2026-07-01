"""Hardware snapshot used by the pre-start check system.

A :class:`HardwareSnapshot` is a frozen capture of every piece
of hardware state any pre-start check needs to read. Built once
at the start of an orchestrator pass and passed to every
:meth:`PreStartCheck.evaluate` call in that pass — so checks
become pure functions of state, and a single hardware read
serves all checks that need the same field.

Growing the snapshot
--------------------
New checks may need new hardware fields. Add them to
:class:`HardwareSnapshot` and to :func:`gather_snapshot`. The
dataclass is the explicit registry of "what hardware state
pre-start checks can see" — keeping it as a dataclass rather
than a free-form dict lets us audit the surface in one place.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from ..microscope import MicroscopeClientLike

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class HardwareSnapshot:
    """Frozen capture of hardware state at pre-start evaluation.

    All distances are in metres; all angles in radians — matching
    AutoScript's native units. User-facing display in dialogs
    converts to mm / degrees at the check site, where the
    conversion stays co-located with the threshold constant.

    Attributes:
        stage_x_m: Stage X coordinate, metres.
        stage_y_m: Stage Y coordinate, metres.
        stage_z_m: Stage Z coordinate, metres.
        stage_r_rad: Stage rotation angle, radians.
        stage_t_rad: Stage tilt angle, radians.
        stage_is_linked: True if stage Z is linked to free
            working distance. See
            ``microscope.specimen.stage.is_linked`` in the
            AutoScript reference.
    """
    stage_x_m: float
    stage_y_m: float
    stage_z_m: float
    stage_r_rad: float
    stage_t_rad: float
    stage_is_linked: bool


def gather_snapshot(microscope: MicroscopeClientLike) -> HardwareSnapshot:
    """Read hardware state into a :class:`HardwareSnapshot`.

    One stage position read, one is-linked read — done once per
    orchestrator pass and shared across all checks. Defensive
    ``float`` / ``bool`` casts protect against any SDK quirks
    (e.g., numpy scalars on the real facade); they're cheap and
    keep the snapshot's field types predictable.

    Args:
        microscope: The microscope client (real or simulated).
            Must expose ``stage.current_position`` and
            ``stage.is_linked``.

    Returns:
        A fresh :class:`HardwareSnapshot` reflecting the
        hardware state at this call site.
    """
    pos = microscope.stage.current_position
    snapshot = HardwareSnapshot(
        stage_x_m=float(pos.x),
        stage_y_m=float(pos.y),
        stage_z_m=float(pos.z),
        stage_r_rad=float(pos.r),
        stage_t_rad=float(pos.t),
        stage_is_linked=bool(microscope.stage.is_linked),
    )
    logger.debug("gather_snapshot: %r", snapshot)
    return snapshot