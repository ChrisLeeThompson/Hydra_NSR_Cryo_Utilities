"""Holder selection with readback verification, and the supported holders.

The xT **holder** (``microscope.specimen.stage.holder``) is the selected
specimen holder; it governs which stage positions Microscope Control
permits. AutoScript 4.14 can set it, but Microscope Control keeps a
private list of allowed holders and may accept the command and then
silently change the holder back. :func:`select_holder` therefore never
trusts the set alone: it polls the value back until it matches, then
waits a short settle window and reads once more to catch a delayed
override. Any mismatch raises :class:`HolderSwitchError`.

Selecting a holder does not move the stage — it is a software
selection — so callers treat it as cheap and safe to issue on any
exit path (see ``CPWorkflow._after_run``). Callers select the holder a
saved position was recorded with before moving there; the helpers
below read the supported-holder tables in :mod:`hydra_nsr_cu.defaults`.
"""
from __future__ import annotations

import logging
import time
from typing import Callable, Optional

from .. import defaults
from .stage_ops import StageOpsLike

logger = logging.getLogger(__name__)

#: Logged by a caller whose stage move stopped short: the likeliest
#: cause is a position reached under a holder whose limits exclude it
#: (see microscope/stage_moves.py).
SHORTFALL_HOLDER_HINT = (
    "The position may need a different holder: select the holder that "
    "reaches it in Sample Exchange, move there, and use Update Position "
    "to record it."
)


def holder_tag(name: str) -> str:
    """The short tag for a supported holder ("AG", "ST", ...), else ""."""
    return defaults.SUPPORTED_HOLDERS.get(str(name or ""), "")


def is_supported_holder(name: str) -> bool:
    """True when a stage position can be saved with holder ``name``."""
    return str(name or "") in defaults.SUPPORTED_HOLDERS


def needs_confirmation(name: str) -> bool:
    """True when moving to a position saved with ``name`` asks first."""
    return bool(name) and str(name) in defaults.HOLDERS_TO_CONFIRM


#: Slice of the settle wait between stop checks.
_SETTLE_SLICE_S = 0.1


class HolderSwitchError(RuntimeError):
    """A holder selection did not take effect (override, timeout, abort)."""


def select_holder(
    stage: StageOpsLike,
    name: str,
    *,
    should_abort: Optional[Callable[[], bool]] = None,
    on_status: Optional[Callable[[str], None]] = None,
    timeout_s: float = defaults.HOLDER_SWITCH_TIMEOUT_S,
    poll_s: float = defaults.HOLDER_SWITCH_POLL_S,
    settle_s: float = defaults.HOLDER_SWITCH_SETTLE_S,
) -> bool:
    """Select holder ``name`` and verify that it took effect.

    Returns True when a switch was commanded and verified, False when
    ``name`` was already selected (nothing sent, no status). Raises
    :class:`HolderSwitchError` when the readback never matches within
    ``timeout_s``, reverts within ``settle_s`` after matching, or
    ``should_abort()`` turns true while the readback still mismatches.
    During the settle window a stop request only cuts the wait short;
    the final readback still decides. Hardware read/write failures
    propagate as whatever the ops layer raised. ``on_status`` receives
    "Selecting holder: <name>..." only when a switch is commanded.
    """
    name = str(name)
    current = stage.holder
    if current == name:
        logger.info("Holder %r already selected; no switch needed", name)
        return False

    if on_status is not None:
        on_status(f"Selecting holder: {name}...")
    logger.info("Holder switch: %r -> %r", current, name)
    stage.set_holder(name)

    deadline = time.monotonic() + timeout_s
    while True:
        readback = stage.holder
        if readback == name:
            break
        if should_abort is not None and should_abort():
            raise HolderSwitchError(
                f"holder switch to {name!r} interrupted (reads {readback!r})"
            )
        if time.monotonic() >= deadline:
            raise HolderSwitchError(
                f"holder did not change to {name!r} within {timeout_s:.0f} s "
                f"(reads {readback!r}; Microscope Control may not permit it)"
            )
        time.sleep(poll_s)

    # Microscope Control may accept the command and change the holder
    # back a moment later; one more read after a settle window catches
    # that. The wait is sliced so a stop or abandon request cuts it
    # short instead of adding up to settle_s before the caller sees it.
    settle_deadline = time.monotonic() + settle_s
    while True:
        remaining = settle_deadline - time.monotonic()
        if remaining <= 0:
            break
        if should_abort is not None and should_abort():
            logger.info("Holder switch: settle window cut short by a stop")
            break
        time.sleep(min(_SETTLE_SLICE_S, remaining))
    readback = stage.holder
    if readback != name:
        raise HolderSwitchError(
            f"Microscope Control reverted the holder to {readback!r} "
            f"after selecting {name!r}"
        )
    logger.info("Holder switch verified: %r", name)
    return True
