"""Stage hardware ops.

Wraps AutoScript's ``microscope.specimen.stage`` with a narrow,
intention-revealing surface. Two implementations live side by side:

* :class:`StageOps` — real, backed by ``SdbMicroscopeClient.specimen.stage``.
* :class:`SimulatedStageOps` — in-memory simulation for offline UI development.

``make_position(x, y, z, r, t)`` returns a position object for
``absolute_move`` / ``relative_move`` (AutoScript's ``StagePosition``
or :class:`SimulatedStagePosition`), so callers never import the
AutoScript type. All five axes default to ``None``, not ``0.0``:
AutoScript treats an unset axis as "do not move that axis", and the
simulation preserves that. ``make_position(t=0.0)`` therefore tilts to
zero and leaves X/Y/Z/R alone, whereas ``0.0`` defaults would command
every axis to zero — a chamber-collision risk on real hardware.
Positions read back via ``current_position`` always have concrete
floats on every axis; ``None`` appears only in commanded positions.

Coordinate systems: the app sets the stage's default coordinate system
to RAW at connect (``microscope/client.py``), so ``current_position``
and every move are in the RAW (encoder) frame unless a command says
otherwise. ``make_position(..., coordinate_system=...)`` tags one
command with another frame (a ``CoordinateSystem`` enumeration value,
"Raw" or "Specimen"), and ``position_in(coordinate_system)`` reads the
position in a named frame — the stage reads only in its default frame,
so the read switches the default and restores it.
``absolute_move(..., rotate_compucentric=True)`` sends
``MoveSettings`` so X and Y follow a rotation; left off, the move is
sent with the position alone. The app's own moves are all plain and
in the RAW frame. The simulated stage models the two frames as a
rotation offset (``set_specimen_rotation_offset``), zero unless a
test sets it.

Both implementations expose a read-only ``is_linked``. The real
:class:`StageOps` has no ``link()`` / ``unlink()`` — linking is a
deliberate user action in xT and is never done programmatically. The
simulated class adds ``link()`` / ``unlink()`` and ``force_position()``
as testing escape hatches for the REFUSE / ASK_CONFIRM branches of the
pre-start checks; the ``DEV_FORCE_UNLINKED`` and
``DEV_FORCE_STAGE_OUT_OF_RANGE`` flags in :mod:`hydra_nsr_cu.defaults`
seed those states at construction, and ``home()`` honours the
out-of-range flag. ``DEV_FORCE_STAGE_SHORTFALL`` makes
``absolute_move`` land short on X, standing in for xT clamping a move
at the holder limits.
"""
from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass, replace
from typing import Any, List, Optional

from .. import defaults

logger = logging.getLogger(__name__)


#: ``CoordinateSystem`` enumeration values (AutoScript 4.14): the stage
#: frames a command or a read can name. AutoScript starts on Specimen;
#: the app's default is RAW.
RAW_COORDINATE_SYSTEM = "Raw"
SPECIMEN_COORDINATE_SYSTEM = "Specimen"

# Simulated-mode delay for absolute_move. Lets the UI-side busy
# indicator and page-disable behavior be visible during dev.
_SIM_MOVE_DELAY_S = 1.5

# Simulated-mode delay for relative_move. Shorter than the absolute
# delay because the Stage / Scan page's Z slider issues a stream of
# relative moves while the user holds it (one per worker tick); a
# 1.5 s delay per tick would make the slider feel hopelessly laggy.
# The chosen value is long enough that a multi-phase rotation
# (4 phases × 0.25 s) still produces a visible busy state, while
# short enough that the Z slider stays interactive.
_SIM_RELATIVE_MOVE_DELAY_S = 0.25

# How far short of the command the simulated stage lands while
# DEV_FORCE_STAGE_SHORTFALL is set (X and tilt): well past the arrival
# tolerances.
_SIM_SHORTFALL_M = 50e-6
_SIM_SHORTFALL_RAD = math.radians(1.0)


class StageOps:
    """Real stage ops backed by ``SdbMicroscopeClient.specimen.stage``."""

    def __init__(self, sdb: Any) -> None:
        self._sdb = sdb
        # The stage's default coordinate system, as last set through
        # set_default_coordinate_system(); AutoScript starts on Specimen.
        self._default_coordinate_system: str = SPECIMEN_COORDINATE_SYSTEM

    @property
    def current_position(self) -> Any:
        position = self._sdb.specimen.stage.current_position
        logger.debug("Stage current_position: %r", position)
        return position

    @property
    def is_linked(self) -> bool:
        """True if stage Z is linked to the free working distance.

        Reads ``microscope.specimen.stage.is_linked`` from the
        AutoScript SDK. Linked-vs-unlinked is a deliberate user
        action in xT — there is no setter exposed here, and the
        codebase never programmatically links the real hardware.
        The check system consumes this property via
        :func:`hydra_nsr_cu.pre_start_checks.snapshot.gather_snapshot`.
        """
        is_linked = bool(self._sdb.specimen.stage.is_linked)
        logger.debug("Stage is_linked: %r", is_linked)
        return is_linked

    def absolute_move(
        self, position: Any, *, rotate_compucentric: bool = False,
    ) -> None:
        """Move the stage to ``position``.

        ``rotate_compucentric`` sends ``MoveSettings`` with that flag so
        X and Y follow a rotation (the point under the beam stays put).
        Left False, as the app's moves leave it, the move is sent with
        the position alone.
        """
        if rotate_compucentric:
            # Lazy import — see make_position.
            from autoscript_sdb_microscope_client.structures import MoveSettings
            logger.info(
                "Stage absolute_move to %r (rotate_compucentric)", position,
            )
            self._sdb.specimen.stage.absolute_move(
                position, MoveSettings(rotate_compucentric=True),
            )
            return
        logger.info("Stage absolute_move to %r", position)
        self._sdb.specimen.stage.absolute_move(position)

    def relative_move(
        self,
        position: Any,
        *,
        rotate_compucentric: bool = False,
        link_z_y: bool = False,
    ) -> None:
        """Move the stage by ``position`` (a delta), with optional move flags.

        Kwargs map 1:1 to AutoScript's :class:`MoveSettings` fields.
        Both default to ``False`` rather than relying on whatever
        ``set_default_move_settings`` was last called with — explicit
        values keep behavior predictable across hardware sessions.

        Stage / Scan callers:

        * Stage rotation passes ``rotate_compucentric=True`` for the
          180° rotation phase (so the rotation is centered on the
          specimen's current X/Y).
        * Z slider passes ``link_z_y=True`` for every step (so the
          Y axis compensates for Z motion, matching the xT
          "Link Z to Y" behavior).
        """
        # Lazy import — keeps the module loadable on machines without
        # AutoScript installed (simulation-only environments).
        from autoscript_sdb_microscope_client.structures import MoveSettings
        settings = MoveSettings(
            rotate_compucentric=rotate_compucentric,
            link_z_y=link_z_y,
        )
        logger.info(
            "Stage relative_move by %r (rotate_compucentric=%r, link_z_y=%r)",
            position, rotate_compucentric, link_z_y,
        )
        self._sdb.specimen.stage.relative_move(position, settings)

    def set_default_coordinate_system(self, coordinate_system: Any) -> None:
        """Set the stage's default coordinate system for reads and moves.

        Applies to ``current_position`` and to ``absolute_move`` /
        ``relative_move`` whenever a position doesn't specify its own
        ``coordinate_system``. The app sets this to ``CoordinateSystem.RAW``
        once at connect (see :meth:`MicroscopeClient.connect`) so saved
        stage positions are encoder-based and exactly repeatable, rather
        than in the AutoScript default ("Specimen") frame, whose Z is tied
        to the free working distance and so drifts with focus / link state.
        """
        logger.info(
            "Stage: set default coordinate system to %r", coordinate_system
        )
        self._sdb.specimen.stage.set_default_coordinate_system(coordinate_system)
        self._default_coordinate_system = str(coordinate_system)

    def position_in(self, coordinate_system: str) -> Any:
        """Read the stage position in ``coordinate_system``.

        The stage reads ``current_position`` in its default coordinate
        system only, so a read in another frame switches the default,
        reads, and restores the default — also when the read fails.
        Same as :attr:`current_position` when ``coordinate_system`` is
        the default already (no switch).
        """
        coordinate_system = str(coordinate_system)
        default = self._default_coordinate_system
        if coordinate_system == default:
            return self.current_position
        stage = self._sdb.specimen.stage
        stage.set_default_coordinate_system(coordinate_system)
        try:
            position = stage.current_position
        finally:
            stage.set_default_coordinate_system(default)
        logger.debug(
            "Stage current_position (%s): %r", coordinate_system, position,
        )
        return position

    def home(self) -> None:
        logger.info("Stage home: starting")
        self._sdb.specimen.stage.home()
        logger.info("Stage home: complete")

    # --- Holder (AutoScript 4.14: specimen.stage.holder) ------------------

    @property
    def holder(self) -> str:
        """Name of the holder currently selected in Microscope Control.

        Holder names are plain strings defined by the server; the ones
        a stage position can be saved with are listed in
        ``SUPPORTED_HOLDERS`` in :mod:`hydra_nsr_cu.defaults`.
        """
        value = str(self._sdb.specimen.stage.holder.value)
        logger.debug("Stage holder: %r", value)
        return value

    @property
    def available_holders(self) -> List[str]:
        """Holders xT lists as applicable in the current microscope state."""
        values = [
            str(v) for v in self._sdb.specimen.stage.holder.available_values
        ]
        logger.debug("Stage available holders: %r", values)
        return values

    def set_holder(self, name: str) -> None:
        """Ask Microscope Control to select holder ``name``.

        xT may accept the command and then silently change the holder
        back to one it permits, so callers verify by reading
        :attr:`holder` back — use
        :func:`hydra_nsr_cu.microscope.holder.select_holder` rather
        than calling this directly.
        """
        logger.info("Stage: selecting holder %r", name)
        self._sdb.specimen.stage.holder.value = str(name)

    def make_position(
        self,
        x: Optional[float] = None,
        y: Optional[float] = None,
        z: Optional[float] = None,
        r: Optional[float] = None,
        t: Optional[float] = None,
        coordinate_system: Optional[str] = None,
    ) -> Any:
        """Construct a StagePosition. Axes left at ``None`` are not commanded.

        Per AutoScript: an unset axis on a ``StagePosition`` means
        "do not move that axis". Callers pass only the axes they want
        to command. Saved-position consumers (Stage Positions, GIS
        Deposition) pass all five; rotation phases pass one or two.
        ``coordinate_system`` names the frame the values are in (a
        ``CoordinateSystem`` enumeration value); ``None`` leaves the
        move in the stage's default frame.
        """
        # Lazy import — keeps the module loadable on machines without
        # AutoScript installed (simulation-only environments).
        from autoscript_sdb_microscope_client.structures import StagePosition
        kwargs = {
            k: v for k, v in
            (("x", x), ("y", y), ("z", z), ("r", r), ("t", t))
            if v is not None
        }
        if coordinate_system is not None:
            kwargs["coordinate_system"] = str(coordinate_system)
        return StagePosition(**kwargs)


# --- Simulated implementation -------------------------------------------


@dataclass
class SimulatedStagePosition:
    """Stand-in for AutoScript's ``StagePosition``.

    Axes are ``Optional[float]`` to mirror AutoScript's per-axis
    "don't move" semantic in commanded positions. State read back via
    :attr:`SimulatedStageOps.current_position` always has concrete
    floats on every axis; ``None`` appears only in commands passed
    to ``absolute_move`` / ``relative_move``.
    """
    x: Optional[float] = None
    y: Optional[float] = None
    z: Optional[float] = None
    r: Optional[float] = None
    t: Optional[float] = None
    #: The frame the values are in; ``None`` is the stage default (RAW).
    coordinate_system: Optional[str] = None

    def __repr__(self) -> str:
        def _fmt(v: Optional[float]) -> str:
            return "None" if v is None else f"{v:.4g}"
        frame = (
            "" if self.coordinate_system is None
            else f", {self.coordinate_system}"
        )
        return (
            f"SimulatedStagePosition(x={_fmt(self.x)}, y={_fmt(self.y)}, "
            f"z={_fmt(self.z)}, r={_fmt(self.r)}, t={_fmt(self.t)}{frame})"
        )


_SIM_INITIAL_POSITION = SimulatedStagePosition(
    x=0.0, y=0.0, z=0.005, r=0.0, t=0.0,
)


def _initial_position() -> SimulatedStagePosition:
    """Starting simulated position, honouring DEV_FORCE_STAGE_OUT_OF_RANGE.

    Both :meth:`SimulatedStageOps.__init__` and
    :meth:`SimulatedStageOps.home` seed from here, so the forced
    out-of-range testing state (used to exercise the ASK_CONFIRM
    branch of
    :class:`hydra_nsr_cu.pre_start_checks.checks.StagePositionWithinSafeRangeCheck`)
    is applied consistently on startup and after a Home. With the flag
    off this is just a copy of the normal initial position.
    """
    pos = replace(_SIM_INITIAL_POSITION)
    if defaults.DEV_FORCE_STAGE_OUT_OF_RANGE:
        pos = replace(
            pos,
            x=defaults.DEV_FORCED_OUT_OF_RANGE_RADIAL_M,
            y=0.0,
        )
    return pos


class SimulatedStageOps:
    """Simulated stage ops for offline development.

    Tracks a mutable :class:`SimulatedStagePosition`. ``absolute_move``
    updates it after a simulated delay (so UI testing exercises the
    worker-thread path realistically). ``home`` resets it to the
    initial position.

    Z-link state is tracked via the :attr:`is_linked` property,
    flipped via :meth:`link` / :meth:`unlink`. These mirror the
    AutoScript SDK methods and exist only on the simulated facade
    — see the module docstring for the rationale.
    """

    def __init__(self) -> None:
        self._position: SimulatedStagePosition = _initial_position()
        # Default: linked. Matches the steady-state of a real
        # session and keeps the dev workflow unblocked by REFUSE
        # paths in pre-start checks. Flip via link() / unlink() to
        # exercise the REFUSE branch, or set DEV_FORCE_UNLINKED in
        # defaults.py to start unlinked without editing this line.
        self._is_linked: bool = not defaults.DEV_FORCE_UNLINKED
        # Holder selection. Starts on DEV_SIMULATED_INITIAL_HOLDER (the
        # AutoGrid shuttle holder, the normal cryo state); the available
        # list comes from defaults so it tracks SUPPORTED_HOLDERS. The
        # override fields back DEV_FORCE_HOLDER_OVERRIDE — see
        # set_holder().
        self._holder: str = defaults.DEV_SIMULATED_INITIAL_HOLDER
        self._available_holders: List[str] = list(
            defaults.DEV_SIMULATED_HOLDERS
        )
        self._override_revert_to: Optional[str] = None
        self._override_reads_left: int = 0
        # Specimen rotation = RAW rotation + this offset; see
        # set_specimen_rotation_offset().
        self._specimen_rotation_offset_rad: float = 0.0

    @property
    def current_position(self) -> SimulatedStagePosition:
        return replace(self._position)

    # --- Holder -------------------------------------------------------------

    @property
    def holder(self) -> str:
        """Selected holder name; mirrors :attr:`StageOps.holder`.

        With a pending ``DEV_FORCE_HOLDER_OVERRIDE`` revert, the first
        read after :meth:`set_holder` still shows the new holder and
        the next read reverts it — the sim's stand-in for Microscope
        Control changing the holder back a moment after accepting the
        command.
        """
        if self._override_revert_to is not None:
            if self._override_reads_left > 0:
                self._override_reads_left -= 1
            else:
                logger.info(
                    "Simulated stage: xT override reverts holder to %r",
                    self._override_revert_to,
                )
                self._holder = self._override_revert_to
                self._override_revert_to = None
        return self._holder

    @property
    def available_holders(self) -> List[str]:
        """Mirrors :attr:`StageOps.available_holders`."""
        return list(self._available_holders)

    def set_holder(self, name: str) -> None:
        """Select a holder; an unlisted name raises like a bad server request.

        See :attr:`holder` for the ``DEV_FORCE_HOLDER_OVERRIDE``
        behaviour this arms.
        """
        name = str(name)
        if name not in self._available_holders:
            raise ValueError(f"holder {name!r} is not available")
        previous = self._holder
        self._holder = name
        if defaults.DEV_FORCE_HOLDER_OVERRIDE and name != previous:
            self._override_revert_to = previous
            self._override_reads_left = 1
        logger.info("Simulated stage: holder %r -> %r", previous, name)

    @property
    def is_linked(self) -> bool:
        """True if the simulated stage Z is linked to FWD.

        Mirrors :attr:`StageOps.is_linked` so the pre-start
        check system can treat real and simulated facades
        symmetrically. Flip via :meth:`link` / :meth:`unlink`.
        """
        return self._is_linked

    def link(self) -> None:
        """Sim-only: link Z to FWD.

        Mirrors ``microscope.specimen.stage.link()`` from the
        AutoScript SDK. Not exposed on :class:`StageOps` —
        nothing in the codebase programmatically links real
        hardware (linking is a deliberate user action in the
        Microscope Control application).

        Use in dev workflows to clear an unlinked state after
        exercising the REFUSE branch of
        :class:`hydra_nsr_cu.pre_start_checks.checks.ZLinkedToFreeWorkingDistanceCheck`.
        """
        self._is_linked = True
        logger.info("Simulated stage: link()")

    def unlink(self) -> None:
        """Sim-only: unlink Z from FWD.

        Mirrors ``microscope.specimen.stage.unlink()`` from the
        AutoScript SDK. Same rationale as :meth:`link` for why
        it's sim-only — use this to simulate an unlinked state
        when testing the REFUSE branch of
        :class:`hydra_nsr_cu.pre_start_checks.checks.ZLinkedToFreeWorkingDistanceCheck`.
        """
        self._is_linked = False
        logger.info("Simulated stage: unlink()")

    def force_position(
        self,
        x: Optional[float] = None,
        y: Optional[float] = None,
        z: Optional[float] = None,
        r: Optional[float] = None,
        t: Optional[float] = None,
    ) -> None:
        """Sim-only: set the stage position directly, with no move delay.

        A testing escape hatch mirroring :meth:`link` / :meth:`unlink`:
        it bypasses :meth:`absolute_move` (and its simulated delay) to
        place the stored state wherever a test needs it. Axes left at
        ``None`` keep their current value, matching the per-axis
        "don't move" semantic used everywhere else. Use it to drive
        :class:`hydra_nsr_cu.pre_start_checks.checks.StagePositionWithinSafeRangeCheck`
        — e.g. force an out-of-range radial position and assert the
        check returns ASK_CONFIRM.

        Not exposed on :class:`StageOps`: there is no notion of
        teleporting the real stage, and nothing in the codebase sets a
        real position outside of a commanded move.
        """
        cur = self._position
        self._position = SimulatedStagePosition(
            x=cur.x if x is None else float(x),
            y=cur.y if y is None else float(y),
            z=cur.z if z is None else float(z),
            r=cur.r if r is None else float(r),
            t=cur.t if t is None else float(t),
        )
        logger.info("Simulated stage: force_position() → %r", self._position)

    def absolute_move(
        self, position: Any, *, rotate_compucentric: bool = False,
    ) -> None:
        """Move to ``position``; axes set to ``None`` are not moved.

        Honours AutoScript's per-axis "don't move" semantic — for
        any axis where the commanded position has ``None``, the
        current value is carried forward into the new state. The
        stored state always has concrete floats on every axis.
        ``rotate_compucentric`` is accepted for API parity with
        :class:`StageOps` and has no geometric effect (see
        :meth:`relative_move`).
        """
        def _coalesce(commanded: Any, current: float) -> float:
            return current if commanded is None else float(commanded)

        cur = self._position
        commanded_r = getattr(position, "r", None)
        if (commanded_r is not None
                and getattr(position, "coordinate_system", None)
                == SPECIMEN_COORDINATE_SYSTEM):
            # A Specimen-frame rotation lands offset in the RAW state.
            commanded_r = float(commanded_r) - self._specimen_rotation_offset_rad
        new_position = SimulatedStagePosition(
            x=_coalesce(getattr(position, "x", None), cur.x),
            y=_coalesce(getattr(position, "y", None), cur.y),
            z=_coalesce(getattr(position, "z", None), cur.z),
            r=_coalesce(commanded_r, cur.r),
            t=_coalesce(getattr(position, "t", None), cur.t),
        )
        if defaults.DEV_FORCE_STAGE_SHORTFALL:
            # Stand-in for xT clamping a move at the holder limits: the
            # move returns normally but the commanded axes land short.
            if getattr(position, "x", None) is not None:
                new_position = replace(
                    new_position, x=new_position.x - _SIM_SHORTFALL_M,
                )
            if getattr(position, "t", None) is not None:
                new_position = replace(
                    new_position, t=new_position.t - _SIM_SHORTFALL_RAD,
                )
            logger.info(
                "Simulated stage: DEV_FORCE_STAGE_SHORTFALL lands X %.0f µm "
                "and tilt %.1f° short of the command",
                _SIM_SHORTFALL_M * 1e6, math.degrees(_SIM_SHORTFALL_RAD),
            )
        logger.info(
            "Simulated stage: absolute_move to %r%s", new_position,
            " (rotate_compucentric; ignored in sim)" if rotate_compucentric else "",
        )
        time.sleep(_SIM_MOVE_DELAY_S)
        self._position = new_position

    def relative_move(
        self,
        position: Any,
        *,
        rotate_compucentric: bool = False,
        link_z_y: bool = False,
    ) -> None:
        """Integrate a position delta into the simulated stage position.

        Honours AutoScript's per-axis "don't move" semantic — axes
        with ``None`` deltas are treated as zero deltas, leaving the
        current value of that axis unchanged.

        Flags are accepted for API parity with :class:`StageOps` but
        have no geometric effect — the simulation is coordinate-only
        and doesn't model compucentric rotation or Z-Y link
        kinematics. Real hardware will produce subtly different X/Y
        coordinates after a compucentric-rotated relative move; tests
        and UI work that depend on those geometric effects must run
        against actual AutoScript.
        """
        def _delta(commanded: Any) -> float:
            return 0.0 if commanded is None else float(commanded)

        cur = self._position
        new_position = SimulatedStagePosition(
            x=cur.x + _delta(getattr(position, "x", None)),
            y=cur.y + _delta(getattr(position, "y", None)),
            z=cur.z + _delta(getattr(position, "z", None)),
            r=cur.r + _delta(getattr(position, "r", None)),
            t=cur.t + _delta(getattr(position, "t", None)),
        )
        logger.info(
            "Simulated stage: relative_move by %r → %r "
            "(rotate_compucentric=%r, link_z_y=%r; ignored in sim)",
            position, new_position, rotate_compucentric, link_z_y,
        )
        time.sleep(_SIM_RELATIVE_MOVE_DELAY_S)
        self._position = new_position

    def set_default_coordinate_system(self, coordinate_system: Any) -> None:
        """Sim no-op (kept for StageOpsLike parity).

        The simulated state is in the RAW frame and the simulation
        doesn't model the free-working-distance link; the Specimen
        frame differs only by the rotation offset of
        :meth:`set_specimen_rotation_offset`, which :meth:`position_in`
        and a Specimen-tagged command apply. Accepted so the connect
        path can call it symmetrically on either facade.
        """
        logger.info(
            "Simulated stage: set default coordinate system to %r (no-op)",
            coordinate_system,
        )

    def set_specimen_rotation_offset(self, offset_rad: float) -> None:
        """Testing escape hatch: Specimen rotation = RAW rotation + offset.

        The simulation is coordinate-only; this one offset stands in
        for the difference between xT's Specimen and RAW rotation so a
        move or a read in the Specimen frame can be checked. Zero by
        default, so the frames coincide.
        """
        self._specimen_rotation_offset_rad = float(offset_rad)
        logger.info(
            "Simulated stage: Specimen rotation offset set to %.2f°",
            math.degrees(self._specimen_rotation_offset_rad),
        )

    def position_in(self, coordinate_system: str) -> SimulatedStagePosition:
        """Read the simulated position in ``coordinate_system``.

        Mirrors :meth:`StageOps.position_in`: the RAW state as is, or
        the Specimen frame with the rotation offset applied and the
        position tagged with that frame.
        """
        coordinate_system = str(coordinate_system)
        if coordinate_system != SPECIMEN_COORDINATE_SYSTEM:
            return self.current_position
        cur = self._position
        return replace(
            cur, r=cur.r + self._specimen_rotation_offset_rad,
            coordinate_system=coordinate_system,
        )

    def home(self) -> None:
        logger.info("Simulated stage: home() — resetting to initial position")
        self._position = _initial_position()

    def make_position(
        self,
        x: Optional[float] = None,
        y: Optional[float] = None,
        z: Optional[float] = None,
        r: Optional[float] = None,
        t: Optional[float] = None,
        coordinate_system: Optional[str] = None,
    ) -> SimulatedStagePosition:
        """Construct a SimulatedStagePosition. Axes left at ``None`` are not commanded.

        Mirrors :meth:`StageOps.make_position` — see there for the
        per-axis "don't move" rationale and the ``coordinate_system``
        tag.
        """
        return SimulatedStagePosition(
            x=x, y=y, z=z, r=r, t=t,
            coordinate_system=(
                None if coordinate_system is None else str(coordinate_system)
            ),
        )


# Type alias for callers that accept either implementation.
StageOpsLike = StageOps | SimulatedStageOps