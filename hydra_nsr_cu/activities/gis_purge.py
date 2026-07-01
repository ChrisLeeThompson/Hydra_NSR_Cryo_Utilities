"""GIS Purge activity.

Procedure:
    1. Look up the configured GIS port and open its valve.
    2. Hold the valve open for the user-specified duration, ticking the
       progress callback once per second.
    3. Close the valve.
    4. Wait for chamber recovery, ticking progress once per second.

Stop semantics:
    The user can interrupt the purge at any second boundary in either
    the purge loop or the chamber-recovery loop. On stop, the valve is
    closed (if currently open) and the activity returns
    :attr:`ActivityResult.STOP`. On exception, the activity attempts to
    close the valve before re-raising.

Progress reporting:
    Mirrors the v2.1 worker pattern. Each phase emits progress for
    seconds 0..N at the start of each second, then a final N tick after
    the loop, followed by a 1-second hold so the GUI can settle on the
    100% value before the next phase resets the bar to 0%.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any, Dict

from ..microscope.gis_ops import GISOpsLike
from .base import (
    ActivityResult,
    ActivityService,
    ProgressCallback,
    StatusCallback,
)

logger = logging.getLogger(__name__)


class GISPurgeService(ActivityService):
    """Open a GIS valve, hold for a duration, close it, wait for recovery."""

    activity_id = "gis_purge"

    def __init__(
        self,
        gis: GISOpsLike,
        port_name: str,
        purge_duration_s: int,
        chamber_recovery_s: int,
    ) -> None:
        super().__init__()
        self._gis = gis
        self._port_name = port_name
        self._purge_duration_s = int(purge_duration_s)
        self._chamber_recovery_s = int(chamber_recovery_s)

    def run(
        self,
        stop_event: threading.Event,
        on_progress: ProgressCallback,
        on_status: StatusCallback,
    ) -> ActivityResult:
        # Resolve the port up front. If the name is wrong, this raises
        # before we touch hardware — better than discovering it
        # mid-procedure with a valve already open.
        on_status(f"GIS purge: opening {self._port_name}")
        port = self._gis.get_port(self._port_name)

        # Track whether the valve is open so cleanup paths (stop /
        # exception) can close it without a redundant close-call when
        # we never opened in the first place.
        valve_opened = False
        try:
            port.open()
            valve_opened = True
            logger.info("GIS purge: opened port %r", self._port_name)

            # --- Purge loop ---
            #
            # Mirrors v2.1's pattern: emit progress at the start of each
            # second (0, 1, 2, ..., N-1), then sleep. After the loop,
            # emit the final N tick and hold for one second so the
            # ProgressBar reaches and visibly displays 100% before the
            # next phase begins.
            for elapsed in range(self._purge_duration_s):
                if stop_event.is_set():
                    on_status("GIS purge: stopped")
                    return ActivityResult.STOP
                on_progress(elapsed, self._purge_duration_s)
                on_status(
                    f"GIS purge: {elapsed}/{self._purge_duration_s} s"
                )
                time.sleep(1)
            on_progress(self._purge_duration_s, self._purge_duration_s)
            on_status(
                f"GIS purge: {self._purge_duration_s}/{self._purge_duration_s} s"
            )
            time.sleep(1)

            # --- Close valve ---
            on_status("GIS purge: closing valve")
            port.close()
            valve_opened = False
            logger.info("GIS purge: closed port %r", self._port_name)

            # --- Chamber recovery ---
            #
            # Same pattern as the purge loop. Skipped entirely when the
            # configured recovery time is 0 — there's no progress to
            # show, no time to wait, no reason to emit a degenerate
            # (0, 0) progress tick that QML would have to special-case.
            if self._chamber_recovery_s > 0:
                for elapsed in range(self._chamber_recovery_s):
                    if stop_event.is_set():
                        on_status("Chamber recovery: stopped")
                        return ActivityResult.STOP
                    on_progress(elapsed, self._chamber_recovery_s)
                    on_status(
                        f"Chamber recovery: {elapsed}/{self._chamber_recovery_s} s"
                    )
                    time.sleep(1)
                on_progress(
                    self._chamber_recovery_s, self._chamber_recovery_s
                )
                on_status(
                    f"Chamber recovery: {self._chamber_recovery_s}/{self._chamber_recovery_s} s"
                )
                time.sleep(1)

            on_status("GIS purge: complete")
            return ActivityResult.COMPLETE

        finally:
            # Best-effort cleanup. Runs on every exit path: normal
            # completion (valve already closed, no-op via the flag),
            # stop request (close the valve we left open), and exception
            # (close the valve before the exception propagates).
            if valve_opened:
                try:
                    port.close()
                    logger.info(
                        "GIS purge: cleanup closed port %r", self._port_name
                    )
                except Exception:
                    # Cleanup failure is logged but doesn't mask whatever
                    # is being propagated from the try block (if anything).
                    logger.exception(
                        "GIS purge: error during cleanup close of port %r "
                        "(non-fatal)",
                        self._port_name,
                    )

    def parameter_summary(self) -> Dict[str, Any]:
        """Provenance summary — captured from the activity as constructed.

        The constructor attribute is ``_port_name``, but the log key is
        normalized to ``gis_port_name`` for consistency with GIS
        Deposition — the same concept across both GIS activities reads
        as the same key in the log, which makes scanning rows easier.
        The ``purge_duration_s`` / ``duration_s`` asymmetry between
        purge and deposition is preserved because they're genuinely
        different concepts.
        """
        return {
            "gis_port_name": self._port_name,
            "purge_duration_s": self._purge_duration_s,
            "chamber_recovery_s": self._chamber_recovery_s,
        }