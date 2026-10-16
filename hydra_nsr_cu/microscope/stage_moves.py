"""Staged stage moves with arrival checks.

A move to a saved or captured position is done in stages so that the
rotation happens flat and the tilt is applied only once the stage is
at the target rotation:

1. Tilt to 0° (skipped when the stage is already flat).
2. Move X, Y, Z and rotation together.
3. Tilt to the target tilt (skipped when already there).

A single five-axis ``absolute_move`` gives the app no control over the
order in which xT applies rotation and tilt, and tilting a shuttle at
the wrong rotation can drive it into the pole piece. The Stage / Scan
page relies on the same premise (tilt to 0° before rotating).

Every stage ends with an **arrival check**: the position is read back
and the deviation of each axis that stage commanded is logged on one
line. xT clamps a move that leaves the selected holder's limits
without raising an error, so a move that returned normally may have
stopped short. The axes listed in ``defaults.STAGE_MOVE_ARRIVAL_CHECK_AXES``
(rotation and tilt by default: the axes the holder limits clamp, read
back exactly by their encoders) are enforced against the tolerances in
:mod:`hydra_nsr_cu.defaults`; a miss raises :class:`StageMoveShortfall`.
X/Y/Z are logged only unless listed, because stage precision and the
microscope's own compensation moves make them unreliable as a hard
check. ``defaults.STAGE_MOVE_ARRIVAL_CHECK`` off turns every check
into log only.

This module knows nothing about holders. A caller that changes the
holder for a move tilts flat first (:func:`tilt_to_zero`), selects the
holder while the stage is flat, then makes the move, whose own tilt
stage is then skipped (see ``microscope/holder.py``).

Every move is in the stage's default coordinate system (RAW, set at
connect) unless a caller names another one: :func:`rotate_to` takes a
``coordinate_system`` so a caller can rotate in xT's Specimen frame,
and its arrival check reads the rotation back in that same frame
(``stage_ops.position_in``).
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional

from .. import defaults
from .stage_ops import StageOpsLike

logger = logging.getLogger(__name__)

StatusCallback = Callable[[str], None]
StopPredicate = Callable[[], bool]

_AXES = ("x", "y", "z", "r", "t")
_LINEAR_AXES = ("x", "y", "z")


def angle_delta_rad(a: float, b: float) -> float:
    """Smallest absolute difference between two angles, in [0, π].

    A readback that differs from the command by a whole turn (RAW
    rotation can be reported in a different branch) compares as equal.
    """
    return abs(_signed_angle_delta_rad(a, b))


def _signed_angle_delta_rad(a: float, b: float) -> float:
    """``a - b`` wrapped into [−π, π)."""
    return (a - b + math.pi) % math.tau - math.pi


class StageMoveShortfall(RuntimeError):
    """The stage did not reach a commanded position within tolerance.

    ``axis`` is the first enforced axis found out of tolerance (x, y,
    z, r, t order); ``target``, ``reached`` and ``shortfall`` are in
    metres for X/Y/Z and radians for R/T. The message carries the same
    values in millimetres and degrees.
    """

    def __init__(self, axis: str, target: float, reached: float) -> None:
        self.axis = axis
        self.target = target
        self.reached = reached
        if axis in _LINEAR_AXES:
            self.shortfall = abs(target - reached)
            detail = (
                f"target {target * 1e3:.3f} mm, reached {reached * 1e3:.3f} mm "
                f"(shortfall {self.shortfall * 1e6:.1f} µm)"
            )
        else:
            self.shortfall = angle_delta_rad(target, reached)
            detail = (
                f"target {math.degrees(target):.2f}°, "
                f"reached {math.degrees(reached):.2f}° "
                f"(shortfall {math.degrees(self.shortfall):.2f}°)"
            )
        super().__init__(f"stage stopped short on {axis.upper()}: {detail}")


@dataclass(frozen=True)
class StageTarget:
    """A fully specified stage position: all five axes, metres and radians.

    Unlike a commanded position from ``make_position``, every axis is
    required and finite, so a staged move can never drop one (an
    unset axis is a collision hazard; see ``stage_ops.py``).
    """

    x: float
    y: float
    z: float
    r: float
    t: float

    def __post_init__(self) -> None:
        for axis in _AXES:
            value = getattr(self, axis)
            if value is None or not math.isfinite(float(value)):
                raise ValueError(f"stage target axis {axis!r} is {value!r}")
            object.__setattr__(self, axis, float(value))

    @classmethod
    def from_position(cls, position: Any) -> "StageTarget":
        """Build from anything with ``x``/``y``/``z``/``r``/``t`` attributes.

        Accepts a :class:`SavedStagePosition` and a captured
        ``current_position`` object alike. Raises ``ValueError`` when
        an axis is missing or not finite.
        """
        return cls(*(getattr(position, axis, None) for axis in _AXES))

    def __repr__(self) -> str:
        return (
            f"StageTarget(x={self.x * 1e3:.3f}mm, y={self.y * 1e3:.3f}mm, "
            f"z={self.z * 1e3:.3f}mm, r={math.degrees(self.r):.2f}deg, "
            f"t={math.degrees(self.t):.2f}deg)"
        )


def _tolerances(
    tolerance_xyz_m: Optional[float], tolerance_angle_deg: Optional[float],
) -> tuple[float, float]:
    """Resolve the tolerances, reading the defaults at call time."""
    xyz = (defaults.STAGE_MOVE_TOLERANCE_XYZ_M
           if tolerance_xyz_m is None else float(tolerance_xyz_m))
    deg = (defaults.STAGE_MOVE_TOLERANCE_ANGLE_DEG
           if tolerance_angle_deg is None else float(tolerance_angle_deg))
    return xyz, math.radians(deg)


def _enforced_axes() -> str:
    """Axes whose shortfall fails a move, read from defaults at call time."""
    if not defaults.STAGE_MOVE_ARRIVAL_CHECK:
        return ""
    return str(defaults.STAGE_MOVE_ARRIVAL_CHECK_AXES).lower()


def _axis_within(
    axis: str, want: float, got: float, tol_xyz_m: float, tol_angle_rad: float,
) -> bool:
    if axis in _LINEAR_AXES:
        return abs(want - got) <= tol_xyz_m
    return angle_delta_rad(want, got) <= tol_angle_rad


def _deviation_text(axis: str, want: float, got: float) -> str:
    if axis in _LINEAR_AXES:
        return f"d{axis}={(got - want) * 1e6:+.1f}µm"
    return f"d{axis}={math.degrees(_signed_angle_delta_rad(got, want)):+.2f}°"


def _check(
    stage: StageOpsLike,
    wants: Dict[str, float],
    *,
    tol_xyz_m: float,
    tol_angle_rad: float,
    coordinate_system: Optional[str] = None,
) -> None:
    """Read the position once; log the deviations; enforce listed axes.

    ``wants`` maps the axes this stage commanded to their targets, in
    ``coordinate_system`` (the stage's default frame when ``None``);
    the position is read back in that same frame. One INFO line per
    call, e.g.
    ``Stage arrival (xyzr): dx=+0.3µm dy=-168.2µm dz=+0.1µm dr=+0.00° [checked: r]``.
    Raises :class:`StageMoveShortfall` for the first enforced axis out
    of tolerance.
    """
    if coordinate_system is None:
        reached = stage.current_position
    else:
        reached = stage.position_in(coordinate_system)
    enforced = _enforced_axes()
    parts = []
    checked = []
    shortfall: Optional[StageMoveShortfall] = None
    for axis in _AXES:
        if axis not in wants:
            continue
        want = float(wants[axis])
        got = float(getattr(reached, axis))
        parts.append(_deviation_text(axis, want, got))
        if axis in enforced:
            checked.append(axis)
            if (shortfall is None
                    and not _axis_within(axis, want, got, tol_xyz_m, tol_angle_rad)):
                shortfall = StageMoveShortfall(axis, want, got)
    logger.info(
        "Stage arrival (%s%s): %s [checked: %s]",
        "".join(a for a in _AXES if a in wants),
        "" if coordinate_system is None else f", {coordinate_system}",
        " ".join(parts), "".join(checked) or "none",
    )
    if shortfall is not None:
        raise shortfall


def verify_arrival(
    stage: StageOpsLike,
    target: StageTarget,
    *,
    axes: str = "xyzrt",
    tolerance_xyz_m: Optional[float] = None,
    tolerance_angle_deg: Optional[float] = None,
) -> None:
    """Arrival check for the axes in ``axes`` against ``target``.

    Logs the deviation of every axis in ``axes`` and raises
    :class:`StageMoveShortfall` for the first one that is both listed
    in ``defaults.STAGE_MOVE_ARRIVAL_CHECK_AXES`` and out of tolerance.
    """
    tol_xyz, tol_angle = _tolerances(tolerance_xyz_m, tolerance_angle_deg)
    wants = {axis: getattr(target, axis) for axis in _AXES if axis in axes}
    _check(stage, wants, tol_xyz_m=tol_xyz, tol_angle_rad=tol_angle)


def _say(on_status: Optional[StatusCallback], text: str) -> None:
    if on_status is not None:
        on_status(text)


def _deg_text(angle_rad: float) -> str:
    """Whole degrees for status text, with U+2212 for negatives."""
    value = int(round(math.degrees(angle_rad)))
    return f"−{-value}" if value < 0 else str(value)


def _tilt_to(
    stage: StageOpsLike,
    tilt_rad: float,
    *,
    on_status: Optional[StatusCallback],
    tol_xyz_m: float,
    tol_angle_rad: float,
) -> bool:
    """Tilt to ``tilt_rad`` unless already there; arrival-checked.

    Returns True when a tilt move was commanded, False when the stage
    was already within tolerance.
    """
    current = float(stage.current_position.t)
    if angle_delta_rad(current, tilt_rad) <= tol_angle_rad:
        logger.info(
            "Stage tilt already at %.2f°; no tilt move needed",
            math.degrees(tilt_rad),
        )
        return False
    _say(on_status, f"Tilting stage to {_deg_text(tilt_rad)}°...")
    stage.absolute_move(stage.make_position(t=tilt_rad))
    _check(stage, {"t": tilt_rad}, tol_xyz_m=tol_xyz_m, tol_angle_rad=tol_angle_rad)
    return True


def tilt_to_zero(
    stage: StageOpsLike,
    *,
    on_status: Optional[StatusCallback] = None,
    tolerance_xyz_m: Optional[float] = None,
    tolerance_angle_deg: Optional[float] = None,
) -> bool:
    """Tilt the stage to 0° if it is not already flat; arrival-checked.

    Stage 1 of a staged move on its own, for callers that need the
    stage flat before handing it to a routine that moves it (Home
    Stage homes from flat) or before selecting another holder.
    Returns True when a tilt move was commanded.
    """
    tol_xyz, tol_angle = _tolerances(tolerance_xyz_m, tolerance_angle_deg)
    return _tilt_to(
        stage, 0.0, on_status=on_status,
        tol_xyz_m=tol_xyz, tol_angle_rad=tol_angle,
    )


def rotate_to(
    stage: StageOpsLike,
    rotation_rad: float,
    *,
    coordinate_system: Optional[str] = None,
    compucentric: bool = False,
    on_status: Optional[StatusCallback] = None,
    tolerance_xyz_m: Optional[float] = None,
    tolerance_angle_deg: Optional[float] = None,
) -> bool:
    """Rotate the stage to ``rotation_rad`` unless already there; arrival-checked.

    Commands rotation only; Z and tilt stay where they are, and so do
    X and Y unless ``compucentric`` is set, which has xT move X and Y
    with the rotation so the point under the beam stays put
    (``MoveSettings.rotate_compucentric``). Callers tilt the stage flat
    first (:func:`tilt_to_zero`) so the rotation happens flat.
    ``coordinate_system`` names the frame of ``rotation_rad`` (a
    ``CoordinateSystem`` enumeration value); the current rotation and
    the arrival check (rotation only) are read in that frame. ``None``
    is the stage's default frame. Returns True when a rotation move
    was commanded.
    """
    tol_xyz, tol_angle = _tolerances(tolerance_xyz_m, tolerance_angle_deg)
    frame = "" if coordinate_system is None else f" ({coordinate_system})"
    if coordinate_system is None:
        current = float(stage.current_position.r)
    else:
        current = float(stage.position_in(coordinate_system).r)
    if angle_delta_rad(current, rotation_rad) <= tol_angle:
        logger.info(
            "Stage rotation already at %.2f°%s; no rotation move needed",
            math.degrees(rotation_rad), frame,
        )
        return False
    _say(on_status, f"Rotating stage to {_deg_text(rotation_rad)}°...")
    logger.info(
        "Stage rotation to %.2f°%s from %.2f°%s",
        math.degrees(rotation_rad), frame, math.degrees(current),
        " (compucentric)" if compucentric else "",
    )
    position = stage.make_position(
        r=rotation_rad, coordinate_system=coordinate_system,
    )
    if compucentric:
        stage.absolute_move(position, rotate_compucentric=True)
    else:
        stage.absolute_move(position)
    _check(
        stage, {"r": rotation_rad}, tol_xyz_m=tol_xyz, tol_angle_rad=tol_angle,
        coordinate_system=coordinate_system,
    )
    if coordinate_system is not None:
        # Log the reading in the stage's default frame too, so the
        # log shows the rotation in both frames.
        logger.info(
            "Stage rotation reads %.2f° in the stage's default frame",
            math.degrees(float(stage.current_position.r)),
        )
    return True


def staged_move(
    stage: StageOpsLike,
    target: StageTarget,
    *,
    staged: bool,
    name: str,
    on_status: Optional[StatusCallback] = None,
    should_stop: Optional[StopPredicate] = None,
    tolerance_xyz_m: Optional[float] = None,
    tolerance_angle_deg: Optional[float] = None,
) -> bool:
    """Move to ``target`` with arrival checks; staged or in one move.

    ``staged`` selects the three-stage sequence (tilt to 0°, X/Y/Z/R,
    tilt to target) or a single five-axis move. ``name`` is the
    position's name for the status text ("Moving stage to <name>...").
    ``should_stop`` is polled before each stage; when it turns true the
    move returns False with nothing further commanded. A
    :class:`StageMoveShortfall` or a hardware error propagates.
    Returns True when the stage arrived.
    """
    tol_xyz, tol_angle = _tolerances(tolerance_xyz_m, tolerance_angle_deg)

    def stopped() -> bool:
        if should_stop is not None and should_stop():
            logger.info("Stage move to %r stopped between stages", name)
            return True
        return False

    def verify(axes: str) -> None:
        verify_arrival(
            stage, target, axes=axes,
            tolerance_xyz_m=tol_xyz, tolerance_angle_deg=math.degrees(tol_angle),
        )

    if not staged:
        if stopped():
            return False
        _say(on_status, f"Moving stage to {name}...")
        logger.info("Stage move to %r (single move): %r", name, target)
        stage.absolute_move(stage.make_position(
            x=target.x, y=target.y, z=target.z, r=target.r, t=target.t,
        ))
        verify("xyzrt")
        return True

    logger.info("Staged stage move to %r: %r", name, target)
    if stopped():
        return False
    _tilt_to(
        stage, 0.0, on_status=on_status,
        tol_xyz_m=tol_xyz, tol_angle_rad=tol_angle,
    )
    if stopped():
        return False
    _say(on_status, f"Moving stage to {name}...")
    stage.absolute_move(stage.make_position(
        x=target.x, y=target.y, z=target.z, r=target.r,
    ))
    verify("xyzr")
    if stopped():
        return False
    _tilt_to(
        stage, target.t, on_status=on_status,
        tol_xyz_m=tol_xyz, tol_angle_rad=tol_angle,
    )
    verify("xyzrt")
    return True
