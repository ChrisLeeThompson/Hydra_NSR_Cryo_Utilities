"""Electron beam hardware ops.

Wraps AutoScript's ``microscope.beams.electron_beam`` with a narrow,
intention-revealing surface. Two implementations live side by side:

* :class:`ElectronBeamOps` — real, backed by ``SdbMicroscopeClient.beams.electron_beam``.
* :class:`SimulatedElectronBeamOps` — in-memory simulation for offline UI development.

The exposed surface is currently minimal — only what the Stage / Scan
page (Session 6) needs: scan rotation. Future consumers (Cryo Prep
imaging activities, beam-condition recorders) may grow this file with
high voltage, beam current, on/off, etc.; the class structure mirrors
:mod:`ion_beam_ops` so additions stay symmetric across the two beams.
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


class ElectronBeamOps:
    """Real electron beam ops backed by ``SdbMicroscopeClient.beams.electron_beam``."""

    def __init__(self, sdb: Any) -> None:
        self._sdb = sdb

    @property
    def scan_rotation_rad(self) -> float:
        """Current scan rotation, in radians.

        Used by the Stage / Scan page's "Scan Rotate SEM and FIB
        180°" operation, which canonically toggles between 0 and π.
        """
        return float(self._sdb.beams.electron_beam.scanning.rotation.value)

    @scan_rotation_rad.setter
    def scan_rotation_rad(self, value: float) -> None:
        logger.info("Electron beam: setting scan rotation to %.4f rad", value)
        self._sdb.beams.electron_beam.scanning.rotation.value = float(value)


# --- Simulated implementation -------------------------------------------


class SimulatedElectronBeamOps:
    """Simulated electron beam ops for offline development.

    Tracks state in memory so subsequent reads reflect prior writes.
    Useful for verifying the Stage / Scan page's scan-rotate-180
    toggle without hardware.
    """

    def __init__(self) -> None:
        # Scan rotation starts at 0 rad — the canonical "untoggled"
        # state expected by the Stage / Scan page's flip operation.
        # Pairs with :class:`SimulatedIonBeamOps`'s same default so
        # the two beams start in a canonical (0, 0) pair.
        self._scan_rotation_rad: float = 0.0

    @property
    def scan_rotation_rad(self) -> float:
        return self._scan_rotation_rad

    @scan_rotation_rad.setter
    def scan_rotation_rad(self, value: float) -> None:
        logger.info(
            "Simulated electron beam: setting scan rotation to %.4f rad", value,
        )
        self._scan_rotation_rad = float(value)


# Type alias for callers that accept either implementation.
ElectronBeamOpsLike = ElectronBeamOps | SimulatedElectronBeamOps