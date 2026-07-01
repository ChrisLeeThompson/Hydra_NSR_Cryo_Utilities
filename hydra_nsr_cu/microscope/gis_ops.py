"""Gas Injection System hardware ops.

Wraps AutoScript's ``microscope.gas`` with a narrow, intention-revealing
surface. Two implementations live side by side:

* :class:`GISOps` — real, backed by ``SdbMicroscopeClient.gas``.
* :class:`SimulatedGISOps` — in-memory simulation for offline UI development.

Activities take whichever is appropriate via the :data:`GISOpsLike`
type alias and don't care which.

The port pattern mirrors AutoScript's: ``get_port(name)`` returns a
port object with valve and needle control. Activities fetch the port
once at the start of the procedure and hold the reference for the
duration, so cleanup ``close()`` always closes exactly the port that
was opened — even if the configured port name changes mid-run.

Heater control (``turn_heater_on`` / ``turn_heater_off``) exists on
AutoScript's ``GisPort`` but is not currently exposed — Hydra NSR
GIS deposition assumes the heater is already on, managed externally.
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


class GISOps:
    """Real GIS ops backed by ``SdbMicroscopeClient.gas``."""

    def __init__(self, sdb: Any) -> None:
        self._sdb = sdb

    def get_port(self, port_name: str) -> Any:
        port = self._sdb.gas.get_gis_port(port_name)
        logger.debug("Got GIS port %r", port_name)
        return port


# --- Simulated implementation -------------------------------------------


class SimulatedGISPort:
    """Stand-in for an AutoScript GIS port. Tracks valve and needle state."""

    def __init__(self, port_name: str) -> None:
        self._name = port_name
        self._is_open = False
        self._is_inserted = False

    def open(self) -> None:
        logger.info("Simulated GIS port %r: open()", self._name)
        self._is_open = True

    def close(self) -> None:
        logger.info("Simulated GIS port %r: close()", self._name)
        self._is_open = False

    def insert(self) -> None:
        logger.info("Simulated GIS port %r: insert()", self._name)
        self._is_inserted = True

    def retract(self) -> None:
        logger.info("Simulated GIS port %r: retract()", self._name)
        self._is_inserted = False


class SimulatedGISOps:
    """Simulated GIS ops for offline development.

    Caches port objects by name so repeated ``get_port(name)`` calls
    return the same object — preserves valve / needle state across
    lookups, just as the real AutoScript would.
    """

    def __init__(self) -> None:
        self._ports: dict[str, SimulatedGISPort] = {}

    def get_port(self, port_name: str) -> SimulatedGISPort:
        if port_name not in self._ports:
            self._ports[port_name] = SimulatedGISPort(port_name)
        return self._ports[port_name]


# Type alias for callers that accept either implementation.
GISOpsLike = GISOps | SimulatedGISOps