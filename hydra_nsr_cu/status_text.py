"""Helpers that keep user-facing status text short.

The status bar has room for roughly 45 characters while a workflow is
running (the progress bar takes the middle) and about 80 once idle, so
status text is kept to a short summary and the full detail (exception
messages, tracebacks) goes to the console log. An
activity's status-icon tooltip may additionally carry a capped,
single-line exception summary.
"""
from __future__ import annotations

import re

SEE_LOG = "(see console log)"

#: Soft cap on status-bar text. The bar elides visually beyond its
#: width; this cap bounds what is stored and shown elsewhere.
MAX_STATUS_CHARS = 72

#: Cap on the exception summary carried to an activity's tooltip.
MAX_DETAIL_CHARS = 300

_WS = re.compile(r"\s+")


def shorten(text: str, limit: int = MAX_STATUS_CHARS) -> str:
    """Collapse whitespace and truncate ``text`` to ``limit`` characters.

    A truncated string ends with a single ellipsis character and is
    exactly ``limit`` characters long. Never raises: non-string input
    is coerced with :func:`str`.
    """
    try:
        flat = _WS.sub(" ", str(text)).strip()
    except Exception:
        return ""
    if limit <= 0 or len(flat) <= limit:
        return flat
    if limit == 1:
        return "…"
    return flat[: limit - 1].rstrip() + "…"


def failure_status(prefix: str, phase: str) -> str:
    """Compose ``"<prefix>: <phase> failed (see console log)"``."""
    return f"{prefix}: {phase} failed {SEE_LOG}"


def exception_detail(exc: BaseException) -> str:
    """One-line ``"<Type>: <message>"`` summary of ``exc`` for a tooltip."""
    message = shorten(str(exc), MAX_DETAIL_CHARS)
    name = type(exc).__name__
    if not message:
        return name
    return shorten(f"{name}: {message}", MAX_DETAIL_CHARS)
