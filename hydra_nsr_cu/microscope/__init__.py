"""Microscope hardware access layer.

Exposes a real microscope client (wrapping AutoScript's
SdbMicroscopeClient) and a simulated client for offline development.
Activities take ops objects directly (e.g. :class:`GISOps` or
:class:`SimulatedGISOps`) via the ``OpsLike`` type aliases defined
in each ops module.

For consumers that take the whole microscope client (workflow runners,
the stage positions controller), :data:`MicroscopeClientLike` is the
top-level type alias accepting either the real or simulated client.
"""
from .client import MicroscopeClient
from .simulated_client import SimulatedMicroscopeClient

# Type alias for callers that accept either client implementation.
MicroscopeClientLike = MicroscopeClient | SimulatedMicroscopeClient

__all__ = [
    "MicroscopeClient",
    "SimulatedMicroscopeClient",
    "MicroscopeClientLike",
]