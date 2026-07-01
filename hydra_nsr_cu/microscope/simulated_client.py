"""Simulated microscope client for offline UI development."""
from __future__ import annotations

import logging
from typing import Optional

from .electron_beam_ops import SimulatedElectronBeamOps
from .gis_ops import SimulatedGISOps
from .ion_beam_ops import SimulatedIonBeamOps
from .patterning_ops import SimulatedPatterningOps
from .stage_ops import SimulatedStageOps

logger = logging.getLogger(__name__)


class SimulatedMicroscopeClient:
    """Pretends to be a microscope client.

    Hardware-ops accessors return simulated ops objects, constructed
    eagerly in :meth:`connect` (on the single connecting thread) and
    cached for the lifetime of this client. Eager construction matters
    because the accessors are read concurrently (rotation worker, Z
    worker, GUI); a lazy check-then-set could race and build two
    instances, and ops that hold mutable state —
    :class:`SimulatedStageOps`, :class:`SimulatedIonBeamOps`, and
    :class:`SimulatedElectronBeamOps` track positions/conditions across
    calls — would lose the history held by the discarded instance.
    """

    def __init__(self) -> None:
        self._connected: bool = False
        self._host: str = ""
        self._gis: Optional[SimulatedGISOps] = None
        self._stage: Optional[SimulatedStageOps] = None
        self._electron_beam: Optional[SimulatedElectronBeamOps] = None
        self._ion_beam: Optional[SimulatedIonBeamOps] = None
        self._patterning: Optional[SimulatedPatterningOps] = None

    # --- State -------------------------------------------------------------

    @property
    def is_connected(self) -> bool:
        return self._connected

    @property
    def server_host(self) -> str:
        return self._host

    # --- Hardware-ops accessors -------------------------------------------

    def _require_connected(self) -> None:
        if not self._connected:
            raise RuntimeError("SimulatedMicroscopeClient is not connected")

    # All ops are built eagerly in connect(), so the accessors just
    # return the cached instance after asserting the client is connected.

    @property
    def gis(self) -> SimulatedGISOps:
        self._require_connected()
        return self._gis

    @property
    def stage(self) -> SimulatedStageOps:
        self._require_connected()
        return self._stage

    @property
    def electron_beam(self) -> SimulatedElectronBeamOps:
        self._require_connected()
        return self._electron_beam

    @property
    def ion_beam(self) -> SimulatedIonBeamOps:
        self._require_connected()
        return self._ion_beam

    @property
    def patterning(self) -> SimulatedPatterningOps:
        self._require_connected()
        return self._patterning

    # --- Lifecycle --------------------------------------------------------

    def connect(self) -> None:
        logger.info("Simulated microscope: simulating connect()")
        # Construct all ops eagerly here, on the single connecting thread,
        # rather than lazily on first property access. The accessors are
        # read from several threads (rotation worker, Z worker, GUI), so a
        # lazy check-then-set could race and build two instances — losing
        # the mutated simulated state (positions/conditions) one of them
        # holds. Building them all before the workers start removes the
        # race; the constructors only allocate Python objects (no hardware
        # I/O), so eager construction is cheap.
        self._gis = SimulatedGISOps()
        self._stage = SimulatedStageOps()
        self._electron_beam = SimulatedElectronBeamOps()
        self._ion_beam = SimulatedIonBeamOps()
        self._patterning = SimulatedPatterningOps()
        self._connected = True
        self._host = "simulated"

    def disconnect(self) -> None:
        if not self._connected:
            return
        logger.info("Simulated microscope: simulating disconnect()")
        self._gis = None
        self._stage = None
        self._electron_beam = None
        self._ion_beam = None
        self._patterning = None
        self._connected = False
        self._host = ""