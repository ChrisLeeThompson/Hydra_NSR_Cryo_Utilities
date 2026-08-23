"""Pure state machine for the Z-slider safety gate.

The Z height slider on the Stage / Scan page is gated when the stage is
parked outside the safe range, so the user makes a deliberate choice
before nudging Z from an operationally unusual position. This module is
the decision core of that gate: Qt-free and I/O-free, a pure function of
two inputs — ``in_safe_range`` (the radial check shared with the
pre-start gate, fed by the controller's poll) and ``acknowledged`` (the
user clicked Unlock). Hardware reads, the poll, and QML exposure live in
:class:`hydra_nsr_cu.stage_scan.controller.StageScanController`.

States
------
``UNKNOWN``
    No reading yet. Fail-closed: slider disabled, but no lock overlay,
    so the overlay doesn't flash before the first (asynchronous) poll.
``SAFE``
    Within the safe range. Slider live; acknowledgement irrelevant.
``BLOCKED``
    Out of range and not acknowledged. Overlay shown, slider disabled.
``UNLOCKED``
    Out of range and acknowledged. Overlay gone, slider live.

Returning to the safe range clears the acknowledgement (RESET_ON_PASS),
so a later excursion prompts afresh; the acknowledgement is scoped to
the out-of-range episode, not the session.
"""
from __future__ import annotations

import enum


class ZSafetyState(enum.Enum):
    """The states of the Z-slider safety gate."""

    # No reading yet: fail-closed (slider disabled) but no alarm overlay.
    # Distinct from BLOCKED so the overlay doesn't flash before the first
    # poll lands on initial page activation.
    UNKNOWN = enum.auto()
    SAFE = enum.auto()
    BLOCKED = enum.auto()
    UNLOCKED = enum.auto()


class ZSafetyGate:
    """Decide the Z-slider gate state from safe-range and ack inputs.

    Fail-closed: a freshly constructed gate has not yet seen a reading, so
    it reports :attr:`ZSafetyState.UNKNOWN` until the first
    :meth:`observe_safe_range` call. UNKNOWN keeps the slider disabled
    (fail-closed) but is distinct from :attr:`ZSafetyState.BLOCKED`, so the
    lock overlay is not shown during that window.

    The distinction matters because the first reading arrives
    asynchronously: the controller arms a worker-thread poll when the page
    becomes active, and the first sample lands about one tick later.
    Collapsing UNKNOWN into BLOCKED would flash the lock overlay for that
    tick on first activation even when the stage is actually in range.
    """

    def __init__(self) -> None:
        # Fail-closed: the slider stays disabled until the first reading
        # confirms the stage is in range. ``_observed`` separates "no
        # reading yet" (UNKNOWN -- disabled, no alarm) from a confirmed
        # out-of-range reading (BLOCKED -- disabled, lock overlay shown).
        self._observed: bool = False
        self._in_safe_range: bool = False
        self._acknowledged: bool = False

    def observe_safe_range(self, in_safe_range: bool) -> None:
        """Record the latest safe-range reading.

        Called by the controller on each worker poll tick (the first such
        tick is requested when the page becomes active). The first call
        moves the gate out of :attr:`ZSafetyState.UNKNOWN`. Returning to
        the safe range clears the acknowledgement (RESET_ON_PASS) so a
        subsequent excursion re-prompts.
        """
        self._observed = True
        self._in_safe_range = in_safe_range
        if in_safe_range:
            self._acknowledged = False

    def acknowledge(self) -> None:
        """Acknowledge the current out-of-range condition (Unlock).

        A no-op unless there is a confirmed out-of-range condition to
        acknowledge. Acknowledging while in range -- or before the first
        reading has arrived (UNKNOWN) -- must not pre-authorise the next
        excursion, so both are ignored.
        """
        if self._observed and not self._in_safe_range:
            self._acknowledged = True

    @property
    def state(self) -> ZSafetyState:
        """Derive the current gate state from the inputs.

        ``UNKNOWN`` until the first :meth:`observe_safe_range` call, so the
        slider stays fail-closed during the brief pre-first-reading window
        without showing the BLOCKED lock overlay.
        """
        if not self._observed:
            return ZSafetyState.UNKNOWN
        if self._in_safe_range:
            return ZSafetyState.SAFE
        if self._acknowledged:
            return ZSafetyState.UNLOCKED
        return ZSafetyState.BLOCKED