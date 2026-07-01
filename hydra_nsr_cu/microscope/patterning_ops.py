"""Patterning hardware ops.

Wraps AutoScript's ``microscope.patterning`` with a narrow,
intention-revealing surface. Two implementations live side by side:

* :class:`PatterningOps` — real, backed by ``SdbMicroscopeClient.patterning``.
* :class:`SimulatedPatterningOps` — in-memory simulation for offline UI
  development.

Activities take whichever is appropriate via the :data:`PatterningOpsLike`
type alias and don't care which.

This module exists for the **Hydra NSR** Sputter Coat activity, which
sputters by applying a saved FEI pattern file (.ptf) through the
ion-beam patterning subsystem rather than driving a dedicated
MicroSputter device. The activity:

1. parses the .ptf into polygon vertices, pitch, and an application file
   name (see :mod:`hydra_nsr_cu.activities.pattern_file`),
2. ``clear_patterns()`` then ``create_polygon(vertices, depth_m)``,
   setting ``pitch_x`` / ``pitch_y`` / ``application_file`` / ``beam_type``
   on the returned polygon,
3. runs ``start()`` for a fixed duration, then ``stop()`` and
   ``clear_patterns()``.

The returned polygon object is configured by the caller via plain
attribute assignment (``poly.pitch_x = ...``). On the real client this
is an AutoScript pattern object; on the simulated client it's a
:class:`SimulatedPolygon`. ``ion_beam_type`` is exposed so the activity
can set ``poly.beam_type = patterning.ion_beam_type`` without reaching
into the raw SDB tree.

No timed loop lives here: ``start()`` / ``stop()`` are immediate, and
the per-second interruptible run loop lives in the activity (matching
v2's ``run_sputter_coat_pattern``), so a long sputter stays cancellable.
"""
from __future__ import annotations

import logging
from typing import Any, List, Tuple

logger = logging.getLogger(__name__)


class PatterningOps:
    """Real patterning ops backed by ``SdbMicroscopeClient.patterning``."""

    def __init__(self, sdb: Any) -> None:
        self._sdb = sdb

    def clear_patterns(self) -> None:
        logger.info("Patterning: clear_patterns")
        self._sdb.patterning.clear_patterns()

    def create_polygon(
        self, vertices: List[Tuple[float, float]], depth_m: float,
    ) -> Any:
        """Create a polygon pattern from ``vertices`` at ``depth_m`` (metres).

        Returns the AutoScript pattern object. The caller sets
        ``pitch_x`` / ``pitch_y`` / ``application_file`` / ``beam_type``
        on it before running.
        """
        logger.info(
            "Patterning: create_polygon (%d vertices, depth=%.3e m)",
            len(vertices), depth_m,
        )
        return self._sdb.patterning.create_polygon(vertices, depth_m)

    @property
    def ion_beam_type(self) -> Any:
        """The ION_BEAM member of AutoScript's ``BeamType`` enum.

        Exposed so the activity can do ``poly.beam_type =
        patterning.ion_beam_type`` without importing the enum or
        reaching into ``_sdb``.
        """
        return self._sdb.patterning.BeamType.ION_BEAM

    @property
    def application_file(self) -> Any:
        return self._sdb.patterning.application_file

    @application_file.setter
    def application_file(self, value: Any) -> None:
        logger.info("Patterning: setting default application file to %r", value)
        self._sdb.patterning.application_file = value

    def start(self) -> None:
        logger.info("Patterning: start")
        self._sdb.patterning.start()

    def stop(self) -> None:
        logger.info("Patterning: stop")
        self._sdb.patterning.stop()


# --- Simulated implementation -------------------------------------------


# Sentinel returned by SimulatedPatterningOps.ion_beam_type. The
# activity only ever assigns it to a SimulatedPolygon.beam_type and
# never inspects it, so an opaque object is enough to mirror the real
# enum member.
_SIM_ION_BEAM_TYPE = object()


class SimulatedPolygon:
    """Stand-in for an AutoScript polygon pattern.

    Tracks the attributes the Sputter Coat activity sets on it
    (``pitch_x``, ``pitch_y``, ``application_file``, ``beam_type``) as
    plain instance state. Nothing reads them back today — they exist so
    the activity's attribute assignments behave identically against the
    simulated and real clients.
    """

    def __init__(self, vertices: List[Tuple[float, float]], depth_m: float) -> None:
        self.vertices = list(vertices)
        self.depth_m = float(depth_m)
        self.pitch_x: float = 0.0
        self.pitch_y: float = 0.0
        self.application_file: Any = None
        self.beam_type: Any = None


class SimulatedPatterningOps:
    """Simulated patterning ops for offline development.

    Tracks created patterns and run state in memory. ``start()`` /
    ``stop()`` flip a flag with INFO logs but do not sleep — the
    Sputter Coat activity owns the per-second run loop, so the
    simulated busy state is observable there.
    """

    def __init__(self) -> None:
        self._patterns: List[SimulatedPolygon] = []
        self._is_running: bool = False
        self._application_file: Any = None

    def clear_patterns(self) -> None:
        logger.info("Simulated patterning: clear_patterns")
        self._patterns = []

    def create_polygon(
        self, vertices: List[Tuple[float, float]], depth_m: float,
    ) -> SimulatedPolygon:
        logger.info(
            "Simulated patterning: create_polygon (%d vertices, depth=%.3e m)",
            len(vertices), depth_m,
        )
        poly = SimulatedPolygon(vertices, depth_m)
        self._patterns.append(poly)
        return poly

    @property
    def ion_beam_type(self) -> Any:
        return _SIM_ION_BEAM_TYPE

    @property
    def application_file(self) -> Any:
        return self._application_file

    @application_file.setter
    def application_file(self, value: Any) -> None:
        logger.info(
            "Simulated patterning: setting default application file to %r",
            value,
        )
        self._application_file = value

    def start(self) -> None:
        logger.info("Simulated patterning: start")
        self._is_running = True

    def stop(self) -> None:
        logger.info("Simulated patterning: stop")
        self._is_running = False


# Type alias for callers that accept either implementation.
PatterningOpsLike = PatterningOps | SimulatedPatterningOps
