"""Cryo Prep workflow runner.

Runs a user-composed list of Cryo activities (Sputter Coat, GIS
Deposition) plus an optional Home Stage at the end. The activity list
and per-activity parameters are owned by :class:`CryoActivitiesController`;
this runner reads them as the workflow runner asks for the next
pending activity, so additions, deletions, and switch toggles made
mid-run take effect at the next fetch. Switches are session-only and
default to off; :attr:`canStart` is true iff at least one activity (or
Home Stage) is enabled and the workflow isn't already running.

Validation runs in three passes: parameter validation of every enabled
activity at Start (all-or-nothing, every failure reported via
:attr:`validationFailed`); the pre-start checks declared by each
enabled activity class, routed through the runner's two-step Start
(REFUSE / ASK_CONFIRM); and a per-fetch repeat of both for the activity
about to run. A mid-run failure aborts the workflow rather than
skipping the activity — successive Sputter Coat / GIS Deposition
activities lay down dependent material layers, and a missing
intermediate layer is worse than running fewer activities.

PFIB conditions are captured once in :meth:`_before_run` and restored
in :meth:`_after_run` when either PFIB-restore setting is on. The two
settings map to the recorder's two restore groups — beam-electrical
(high voltage, beam current, on/off) and ion species (plasma gas) —
so the user can revert one without the other. Every restore (PFIB and
stage position) fires only after a fully successful run; a stopped,
failed, or aborted run leaves the microscope exactly as it is.
"""
from __future__ import annotations

import logging
import time
from typing import List, Optional, Set

from PySide6.QtCore import (
    Property,
    QObject,
    Signal,
    Slot,
)

from .. import defaults
from ..activities.base import ActivityResult, ActivityService, StatusCallback
from ..activities.gis_deposition import GISDepositionService
from ..activities.home_stage import HomeStageService
from ..activities.pattern_file import resolve_pattern_path
from ..activities.sputter_coat import SputterCoatService
from ..cryo.activity_records import (
    ACTIVITY_TYPE_GIS_DEPOSITION,
    ACTIVITY_TYPE_SPUTTER_COAT,
    GISDepositionRecord,
    SputterCoatRecord,
)
from ..cryo.controller import CryoActivitiesController
from ..microscope import MicroscopeClientLike
from ..microscope.ion_beam_ops import resolve_plasma_gas_enum
from ..status_text import SEE_LOG
from ..microscope.recorders.pfib_conditions import (
    ABANDONED_FIELD,
    PFIBConditionsRecorder,
    PFIBConditionsSnapshot,
    RestoreFailure,
)
from ..microscope.recorders.stage_position import (
    StagePositionSnapshot,
    StageRecorder,
)
from ..pre_start_checks import (
    PreStartCheck,
    gather_snapshot,
    run_pre_start_checks,
    to_dialog_items,
)
from ..settings.settings_controller import SettingsController
from ..stage_positions.controller import StagePositionsController
from .runner import (
    FetchOutcome,
    PreStartValidationResult,
    WorkflowRunner,
    build_abandon_predicate,
)
from .settings_snapshot import WorkflowSettingsSnapshot

logger = logging.getLogger(__name__)


def _compose_pfib_restore_warning(failures: List[RestoreFailure]) -> str:
    """Short user-facing summary of a partial/failed PFIB restore.

    Folded into the final status-bar text by the runner, so it stays
    one short line; the per-field errors are in the console log and
    the session-log record. Shapes:

    * abandoned:  ``"PFIB restore abandoned before completion"``
    * one field:  ``"PFIB restore failed: beam on/off (see console log)"``
    * multiple:   ``"PFIB restore failed: 3 settings (see console log)"``
    """
    if any(f.field == ABANDONED_FIELD for f in failures):
        return "PFIB restore abandoned before completion"
    if len(failures) == 1:
        what = failures[0].field
    else:
        what = f"{len(failures)} settings"
    return f"PFIB restore failed: {what} {SEE_LOG}"


def _build_pfib_restore_record(
    failures: List[RestoreFailure],
    *,
    restore_species: bool,
    restore_beam_electrical: bool,
    duration_s: float,
) -> dict:
    """Synthetic session-log record for a failed/abandoned PFIB restore.

    The key set is the contract consumed (with bare subscripts) by
    :meth:`WorkflowRunner._on_finished` when it emits the record
    through ``activityRecorded`` — pinned by a producer→consumer test
    in tests/test_pfib_restore_recovery.py so a rename on either side
    fails fast rather than only on a rare failure path in the field.
    """
    abandoned = any(f.field == ABANDONED_FIELD for f in failures)
    return {
        "activity_id": "pfib_restore",
        # A user abandon is a stop, not a hardware failure.
        "result": (
            ActivityResult.STOP.value if abandoned
            else ActivityResult.EXCEPTION.value
        ),
        "duration_s": duration_s,
        "last_status": _compose_pfib_restore_warning(failures),
        "params": {
            "restore_ion_species": restore_species,
            "restore_voltage_current": restore_beam_electrical,
            "failed_fields": ", ".join(f.field for f in failures),
        },
    }


class CPWorkflow(WorkflowRunner):
    """Cryo Prep workflow runner."""

    # Validation failure for a specific activity instance. Emitted
    # before start when an activity's parameters are invalid (e.g. a
    # GIS Deposition referencing a deleted position). The QML page
    # uses these to set the offending row's activityState to
    # "exception" and the error as the statusMessage tooltip.
    #
    # Args: instance_id (string), message (string).
    validationFailed = Signal(str, str)

    # canStart notify. Emitted whenever a switch toggles or the
    # underlying state changes such that canStart's value flips.
    canStartChanged = Signal()

    def __init__(
        self,
        microscope: MicroscopeClientLike,
        cryo_activities: CryoActivitiesController,
        stage_positions: StagePositionsController,
        settings: SettingsController,
        parent: Optional[QObject] = None,
    ) -> None:
        super().__init__(parent)
        self._microscope = microscope
        self._cryo_activities = cryo_activities
        self._stage_positions = stage_positions
        self._settings = settings

        # Session-only enabled state. Not persisted across app launches —
        # at every fresh start, every switch is off. Toggling a switch
        # in the UI calls set_activity_enabled / set_home_stage_enabled.
        self._enabled_instance_ids: Set[str] = set()
        self._home_stage_enabled: bool = False

        # PFIB recorder state — used across before_run / after_run.
        # The snapshot is captured by _before_run on the worker thread
        # and consumed by _after_run on the same thread; no cross-thread
        # sync needed since they're sequential within one run. The
        # recorder itself is constructed in :meth:`_on_commit_to_run`.
        self._pfib_recorder: Optional[PFIBConditionsRecorder] = None
        self._pfib_snapshot: Optional[PFIBConditionsSnapshot] = None

        # Stage-position recorder state — same lifecycle as the PFIB
        # recorder (construct in _on_commit_to_run, capture in
        # _before_run, restore in _after_run), but the restore is
        # gated on a successful run. Used when the "Move Stage To
        # Original Position" setting is on.
        self._stage_recorder: Optional[StageRecorder] = None
        self._stage_snapshot: Optional[StagePositionSnapshot] = None

        # Workflow-settings snapshot — captured in
        # :meth:`_on_commit_to_run` once the runner has committed to
        # the run, consumed by per-activity ``_build_*`` methods,
        # cleared in :meth:`_after_run`. Mirrors the PFIB recorder
        # lifecycle.
        self._settings_snapshot: Optional[WorkflowSettingsSnapshot] = None

        # canStart depends on isRunning (inherited) — re-emit when it
        # transitions so QML's Start-button binding refreshes. Same
        # pattern as RTWorkflow.
        self.isRunningChanged.connect(self.canStartChanged)

    # --- QML-visible properties -------------------------------------------

    @Property(bool, notify=canStartChanged)
    def canStart(self) -> bool:
        """True when Start is meaningful right now.

        Conditions:
            * at least one cryo activity OR home stage is enabled
            * the workflow isn't already running

        Microscope-connected and other-page-not-running checks are
        handled at the QML page level, mirroring RTWorkflow's pattern.
        """
        if self.isRunning:
            return False
        return bool(self._enabled_instance_ids) or self._home_stage_enabled

    # --- Enabled-state setters --------------------------------------------

    @Slot(str, bool)
    def set_activity_enabled(self, instance_id: str, value: bool) -> None:
        """Toggle a cryo activity's switch state by instance id.

        Called by the QML delegate when its switch toggles. Storing
        as a set rather than a per-instance flag means a removed
        activity's id naturally goes stale — no entry, no enabled.
        On removal, the QML calls :meth:`forget_activity` to clean up
        the set explicitly.
        """
        had_any = self.canStart
        if value:
            self._enabled_instance_ids.add(instance_id)
        else:
            self._enabled_instance_ids.discard(instance_id)
        if self.canStart != had_any:
            self.canStartChanged.emit()

    @Slot(str, result=bool)
    def is_activity_enabled(self, instance_id: str) -> bool:
        """Read a cryo activity's current switch state."""
        return instance_id in self._enabled_instance_ids

    @Slot(bool)
    def set_home_stage_enabled(self, value: bool) -> None:
        """Toggle the Home Stage switch state."""
        had_any = self.canStart
        self._home_stage_enabled = bool(value)
        if self.canStart != had_any:
            self.canStartChanged.emit()

    @Slot(result=bool)
    def is_home_stage_enabled(self) -> bool:
        """Read the Home Stage switch state."""
        return self._home_stage_enabled

    @Slot(str)
    def forget_activity(self, instance_id: str) -> None:
        """Remove an instance id from the enabled set.

        Called by QML when an activity is deleted from the model so
        the set doesn't accumulate stale entries. No-op if the id
        was never enabled.
        """
        had_any = self.canStart
        self._enabled_instance_ids.discard(instance_id)
        if self.canStart != had_any:
            self.canStartChanged.emit()

    # --- Microscope-state reads (UI helpers) ------------------------------

    @Slot(result=int)
    def current_microscope_ion_species_index(self) -> int:
        """Best-effort: map the microscope's live plasma_gas to a
        :data:`defaults.SPUTTER_ION_SPECIES` index.

        Used by :file:`SputterCoat.qml`'s ``syncIonSpeciesToMicroscope()``
        to pre-fill a newly-toggled-on sputter activity's ion species
        with whatever gas the microscope is currently loaded with —
        the common UX problem of "I'm on Oxygen but every activity
        defaults to Xenon."

        Returns -1 in three failure cases, all of which are silent
        no-ops at the call site:

        * Microscope not connected — logged at INFO. The user can
          toggle off and on again after connecting.
        * Reading ``ion_beam.plasma_gas`` raises (e.g. mid-disconnect
          race, transient SDK glitch) — logged at EXCEPTION with full
          traceback (short status bar / console for the why).
        * Microscope reports a plasma gas outside the four-element
          catalog (Xenon / Argon / Oxygen / Nitrogen) — logged at
          WARNING with the offending value. Could happen if AutoScript
          ships a new gas.

        The reverse lookup uses ``==`` against the opaque value
        produced by :func:`resolve_plasma_gas_enum` for each catalog
        name — the contract documented in
        :mod:`hydra_nsr_cu.microscope.ion_beam_ops`'s "Plasma-gas
        opacity" section.
        """
        if not self._microscope.is_connected:
            logger.info(
                "Sputter species sync: microscope not connected; "
                "returning -1"
            )
            return -1

        try:
            current = self._microscope.ion_beam.plasma_gas
        except Exception:
            logger.exception(
                "Sputter species sync: read plasma_gas raised; "
                "returning -1"
            )
            return -1

        for idx, name in enumerate(defaults.PLASMA_GAS_SPECIES_NAMES):
            if resolve_plasma_gas_enum(name) == current:
                logger.info(
                    "Sputter species sync: matched %r to catalog "
                    "index %d (%s)",
                    current, idx, name,
                )
                return idx

        logger.warning(
            "Sputter species sync: microscope reports plasma_gas %r "
            "which is not in PLASMA_GAS_SPECIES_NAMES; returning -1",
            current,
        )
        return -1

    # --- WorkflowRunner overrides -----------------------------------------
    #
    # Mid-run switch toggles are honored automatically: each iteration,
    # the runner calls _next_pending_activity, which reads the current
    # _enabled_instance_ids and _home_stage_enabled. A switch toggled
    # off after Start makes the activity invisible to the next fetch.
    # No separate skip-callback needed.
    #
    # The QML binding locks switches on activities past the idle state,
    # so toggles only ever fire for activities the user can still
    # meaningfully change.

    def _validate_pre_start(self) -> PreStartValidationResult:
        """All-or-nothing validation pass at start.

        Three stages, each refusing the start outright on failure;
        cheap GUI-state checks precede hardware reads:

        1. :attr:`canStart` guard (defensive — QML disables Start when
           nothing is enabled).
        2. Parameter validation of every enabled cryo activity. All
           failures are collected and emitted via
           :attr:`validationFailed` so the user sees every problem at
           once; returns ``"refused"``.
        3. Pre-start checks gathered from the enabled activity classes.
           REFUSE → :attr:`preStartCheckRefused` and ``"refused"``;
           ASK_CONFIRM → :attr:`preStartCheckNeedsConfirmation` and
           ``"needs_confirmation"`` with the pending check types;
           PASS → ``"ok"``.

        State capture (settings snapshot, recorders) happens in
        :meth:`_on_commit_to_run`, not here.
        """
        # --- Stage 1: canStart guard ---
        if not self.canStart:
            logger.info(
                "CPWorkflow: no activities enabled; refusing to start"
            )
            self.statusUpdated.emit(
                "Cannot start: no activities enabled"
            )
            return PreStartValidationResult(outcome="refused")

        # Prune enabled instance ids that no longer exist in the live model.
        # _enabled_instance_ids is otherwise pruned only on forget_activity,
        # so after enabling an activity and then loading a template (which
        # mints fresh instance ids — templates.py omits instance_id), the
        # set holds stale ids matching zero records. Without this, Stage 2/3
        # below and _next_pending_activity all skip every record and the run
        # reports "Workflow complete" having done nothing. Intersect against
        # the live ids here, at Start, to close that gap.
        live_ids = {
            record.instance_id
            for record in self._cryo_activities.model.all_records()
        }
        if not self._enabled_instance_ids <= live_ids:
            stale_count = len(self._enabled_instance_ids - live_ids)
            self._enabled_instance_ids &= live_ids
            logger.info(
                "CPWorkflow: pruned %d stale enabled instance id(s) at start",
                stale_count,
            )
            self.canStartChanged.emit()
            # Pruning may have emptied the set; if Home Stage isn't enabled
            # either, there's now nothing to run.
            if not self.canStart:
                self.statusUpdated.emit(
                    "Cannot start: no activities enabled"
                )
                return PreStartValidationResult(outcome="refused")

        # --- Stage 2: parameter validation (collect-all pass) ---
        validation_errors: List[tuple[str, str]] = []
        for record in self._cryo_activities.model.all_records():
            if record.instance_id not in self._enabled_instance_ids:
                continue
            error = self._validate_record(record)
            if error is not None:
                validation_errors.append((record.instance_id, error))
                logger.warning(
                    "CPWorkflow: pre-start validation failed for "
                    "instance=%r: %s",
                    record.instance_id, error,
                )

        if validation_errors:
            for instance_id, message in validation_errors:
                self.validationFailed.emit(instance_id, message)
            count = len(validation_errors)
            self.statusUpdated.emit(
                f"Cannot start: {count} "
                f"activit{'y' if count == 1 else 'ies'} need attention"
            )
            logger.info(
                "CPWorkflow: refusing to start (%d validation error%s)",
                count, "" if count == 1 else "s",
            )
            return PreStartValidationResult(outcome="refused")

        # --- Stage 3: pre-start checks ---
        checks = self._gather_pre_start_checks()
        if checks:
            try:
                snapshot = gather_snapshot(self._microscope)
                summary = run_pre_start_checks(checks, snapshot)
            except Exception:
                # gather_snapshot reads live hardware; a transient glitch
                # must not propagate out of this @Slot-invoked path and
                # leave Start enabled with only a console traceback. Refuse
                # cleanly with a user-facing breadcrumb instead.
                logger.exception(
                    "CPWorkflow: pre-start hardware read failed; refusing "
                    "to start"
                )
                self.statusUpdated.emit("Cannot start: hardware read failed")
                return PreStartValidationResult(outcome="refused")

            if summary.refused:
                items = to_dialog_items(summary.refused)
                self.preStartCheckRefused.emit(items)
                self.statusUpdated.emit("Pre-start check failed")
                logger.info(
                    "CPWorkflow: refusing to start "
                    "(%d pre-start check%s failed)",
                    len(summary.refused),
                    "" if len(summary.refused) == 1 else "s",
                )
                return PreStartValidationResult(outcome="refused")

            if summary.needs_confirmation:
                items = to_dialog_items(summary.needs_confirmation)
                self.preStartCheckNeedsConfirmation.emit(items)
                logger.info(
                    "CPWorkflow: awaiting user confirmation "
                    "(%d pre-start check%s ask for confirmation)",
                    len(summary.needs_confirmation),
                    "" if len(summary.needs_confirmation) == 1 else "s",
                )
                return PreStartValidationResult(
                    outcome="needs_confirmation",
                    pending_confirmed_check_types=(
                        summary.needs_confirmation_types
                    ),
                )

        # All clear. State capture happens in _on_commit_to_run, not
        # here — see the method docstring for the rationale.
        return PreStartValidationResult(outcome="ok")

    def _gather_pre_start_checks(self) -> List[PreStartCheck]:
        """Walk enabled cryo activities + Home Stage, collect their checks.

        Returns a flat list of :class:`PreStartCheck` instances
        contributed by each currently-enabled activity. The
        orchestrator deduplicates by type, so contributing the same
        check from multiple activities (e.g. both Sputter Coat and
        Home Stage want :class:`StagePositionWithinSafeRangeCheck`)
        is fine and produces one evaluation per type.

        Inline dispatch on record type mirrors the pattern in
        :meth:`_next_pending_activity` — no separate registry class.
        Unknown record types are silently skipped (also mirroring
        the fetch behavior).
        """
        checks: List[PreStartCheck] = []
        for record in self._cryo_activities.model.all_records():
            if record.instance_id not in self._enabled_instance_ids:
                continue
            if isinstance(record, SputterCoatRecord):
                checks.extend(SputterCoatService.pre_start_checks())
            elif isinstance(record, GISDepositionRecord):
                checks.extend(GISDepositionService.pre_start_checks())
            # Unknown record types: silently skip. _next_pending_activity
            # will also skip them at fetch time and log a warning then;
            # no point logging twice for the same record.
        if self._home_stage_enabled:
            checks.extend(HomeStageService.pre_start_checks())
        return checks

    def _validate_record(self, record) -> Optional[str]:
        """Validate a single cryo activity record.

        Returns an error message describing the issue, or ``None`` if
        the record is valid. Both Sputter Coat and GIS Deposition are
        position-based and are checked for stale or empty position
        references.

        Used by both :meth:`_validate_pre_start` (start-time pass) and
        :meth:`_next_pending_activity` (mid-run pass), so the
        validation logic stays in one place.
        """
        if isinstance(record, SputterCoatRecord):
            resolved = self._stage_positions.get_record_by_id(
                record.position_id
            )
            if resolved is None:
                return (
                    "Sputter Coat references an unknown stage "
                    "position. Please choose a position from the list."
                )
        elif isinstance(record, GISDepositionRecord):
            resolved = self._stage_positions.get_record_by_id(
                record.position_id
            )
            if resolved is None:
                return (
                    "GIS Deposition references an unknown stage "
                    "position. Please choose a position from the list."
                )
        return None

    def _on_commit_to_run(self) -> None:
        """Capture workflow settings and set up the recorders.

        Fires on every path that commits to a run — an outright
        ``"ok"`` from :meth:`_validate_pre_start` or an accepted
        confirmation via :meth:`WorkflowRunner.respondToConfirmation`
        — and never on refused or cancelled paths. Capturing here
        rather than in ``_validate_pre_start`` matters because the
        confirm dialog may stay open for an arbitrary time: the
        snapshot reflects the settings as of the moment the run
        actually commits. See :class:`WorkflowSettingsSnapshot`.
        """
        self._settings_snapshot = WorkflowSettingsSnapshot.from_settings(
            self._settings
        )

        if (self._settings_snapshot.restore_pfib_voltage_current
                or self._settings_snapshot.restore_pfib_ion_species):
            self._pfib_recorder = PFIBConditionsRecorder(
                self._microscope.ion_beam
            )
            logger.info(
                "CPWorkflow: PFIB restore enabled "
                "(voltage_current=%s, ion_species=%s)",
                self._settings_snapshot.restore_pfib_voltage_current,
                self._settings_snapshot.restore_pfib_ion_species,
            )
        else:
            self._pfib_recorder = None

        if self._settings_snapshot.move_stage_to_original:
            self._stage_recorder = StageRecorder(self._microscope.stage)
            logger.info(
                "CPWorkflow: move-stage-to-original is enabled"
            )
        else:
            self._stage_recorder = None

    def _next_pending_activity(
        self, executed_keys: Set[str],
    ) -> FetchOutcome:
        """Return the next enabled activity not yet executed in this run.

        Walks the cryo activity model in current order, skipping
        disabled and already-executed activities, so mid-run toggles
        and additions take effect at the next fetch. Each candidate
        goes through :meth:`_validate_record` and then
        :meth:`_mid_run_pre_start_check_passes`; either failure aborts
        the workflow via :meth:`_abort_fetch` (dependent deposition
        layers make skipping worse than stopping).

        Home Stage is always considered last (key ``"home_stage/"``)
        and gets the same mid-run pre-start check. Unknown record
        types are marked in ``executed_keys`` and skipped.
        """
        for record in self._cryo_activities.model.all_records():
            # Skip if not switched on (handles mid-run toggle-off).
            if record.instance_id not in self._enabled_instance_ids:
                continue

            # Determine the activity class from the record type. Used
            # for both the executed-key formatting and the mid-run
            # pre-start check call. Unknown record types are skipped;
            # we mark them so we don't retry on every iteration.
            # Choosing a synthetic prefix ("unknown/") avoids any
            # collision with real activity ids.
            if isinstance(record, SputterCoatRecord):
                activity_class = SputterCoatService
            elif isinstance(record, GISDepositionRecord):
                activity_class = GISDepositionService
            else:
                logger.warning(
                    "CPWorkflow: unknown activity record type %s; skipping",
                    type(record).__name__,
                )
                executed_keys.add(f"unknown/{record.instance_id}")
                continue

            activity_id = activity_class.activity_id
            key = f"{activity_id}/{record.instance_id}"
            if key in executed_keys:
                continue

            # Stage 1: parameter validation. Failure aborts the
            # workflow (see method docstring for the layered-
            # deposition rationale).
            error = self._validate_record(record)
            if error is not None:
                self.validationFailed.emit(record.instance_id, error)
                logger.warning(
                    "CPWorkflow: aborting workflow — activity %r "
                    "(instance=%r) failed mid-run validation: %s",
                    activity_id, record.instance_id, error,
                )
                return self._abort_fetch(
                    "Workflow aborted: an activity failed validation"
                )

            # Stage 2: mid-run pre-start check. ASK_CONFIRM outcomes
            # whose type was pre-confirmed at workflow Start proceed
            # silently; everything else aborts. Helper emits its own
            # diagnostics (preStartCheckRefused + status breadcrumb)
            # and records the abort reason — no message here.
            if not self._mid_run_pre_start_check_passes(
                activity_class, self._microscope,
            ):
                return self._abort_fetch()

            # Build the service. Construction reads the record's
            # current parameter values; edits made between Start and
            # this fetch are picked up. Both activity types are
            # position-based, so resolve the position once here.
            resolved = self._stage_positions.get_record_by_id(
                record.position_id
            )
            if isinstance(record, SputterCoatRecord):
                return self._build_sputter_coat(record, resolved)
            # Must be GISDepositionRecord (we'd have continued above
            # for any other type).
            return self._build_gis_deposition(record, resolved)

        # Home Stage is always last, opt-in. Mid-run pre-start
        # check applies here too.
        home_stage_key = f"{HomeStageService.activity_id}/"
        if (self._home_stage_enabled
                and home_stage_key not in executed_keys):
            if not self._mid_run_pre_start_check_passes(
                HomeStageService, self._microscope,
            ):
                return self._abort_fetch()
            return self._build_home_stage()

        return None

    # --- Worker hooks (run on the worker thread) --------------------------

    def _before_run(self, on_status: StatusCallback) -> None:
        """Capture restore-state before any activity runs.

        Captures whichever recorders were set up in
        :meth:`_on_commit_to_run` — PFIB conditions and/or stage
        position. Each capture is independent and best-effort: a
        failure is logged and that recorder's snapshot left None
        (so its restore is skipped), without aborting the run or
        preventing the other capture.
        """
        if self._pfib_recorder is not None:
            on_status("Capturing PFIB conditions...")
            try:
                self._pfib_snapshot = self._pfib_recorder.capture()
            except Exception:
                # Capture failure is logged but doesn't abort the run —
                # the user gets the run, just without restore at the end.
                logger.exception(
                    "CPWorkflow: PFIB capture failed; proceeding without restore"
                )
                self._pfib_snapshot = None
                # Surfaced in the final status text by _on_finished so
                # the skipped restore isn't silent at workflow end.
                self._after_run_warning = (
                    f"PFIB capture failed — not restored {SEE_LOG}"
                )

        if self._stage_recorder is not None:
            on_status("Capturing stage position...")
            try:
                self._stage_snapshot = self._stage_recorder.capture()
            except Exception:
                logger.exception(
                    "CPWorkflow: stage capture failed; proceeding without restore"
                )
                self._stage_snapshot = None

    def _after_run(self, on_status: StatusCallback, completed: bool) -> None:
        """Restore captured state after the activity loop exits.

        Every restore is gated on a fully successful run
        (``completed`` is True). After a stop, exception, or mid-run
        abort the microscope is left exactly as it is:

        * **PFIB conditions** (voltage/current and ion species) — an
          abnormal exit is not the moment to command more hardware
          changes, and the species revert in particular is expensive:
          a plasma-gas switch re-strikes the source and can take
          minutes to settle. The skip is logged so the exit isn't
          silent.
        * **Stage position** — commanding further motion after an
          abnormal exit is unsafe (a stop may be a reaction to a
          collision risk; an exception may mean the stage isn't where
          we think it is).

        Both restores are best-effort and independently guarded — one
        failing doesn't prevent the other. If a capture failed (its
        snapshot is None), that restore is skipped. All recorder /
        snapshot / settings-snapshot references are cleared
        unconditionally at the end so nothing leaks into a subsequent
        run, regardless of which restores fired.
        """
        # Latched Stop-press abandon semantics for ALL cleanup in this
        # hook (PFIB restore and stage restore) — see the helper. With
        # every restore success-gated, this only bites when a first
        # Stop press lands during cleanup after a successful run (or a
        # second press escalates during that cleanup).
        should_abandon = build_abandon_predicate(
            self._stop_event, self._abandon_event,
        )

        # PFIB conditions — success only, same policy as the stage
        # below.
        if (completed
                and self._pfib_recorder is not None
                and self._pfib_snapshot is not None):
            restore_vc = self._settings_snapshot.restore_pfib_voltage_current
            restore_sp = self._settings_snapshot.restore_pfib_ion_species
            # Tailor the status message to the enabled group(s) so the
            # bar reflects what's actually being reverted rather than a
            # generic "conditions". At least one flag is true here — the
            # recorder is only constructed when one is — so one branch
            # always fires.
            if restore_vc and restore_sp:
                on_status(
                    "Restoring PFIB voltage, current, and species..."
                )
            elif restore_vc:
                on_status("Restoring PFIB voltage and current...")
            elif restore_sp:
                on_status("Restoring PFIB ion species...")
            t0_restore = time.monotonic()
            try:
                failures = self._pfib_recorder.restore(
                    self._pfib_snapshot,
                    restore_species=restore_sp,
                    restore_beam_electrical=restore_vc,
                    should_abandon=should_abandon,
                )
            except Exception as exc:
                # restore() reports per-field hardware failures via its
                # return value; reaching here means a bug in the
                # restore machinery itself. Keep the guard (an
                # after_run failure must never mask the workflow
                # result) but surface it the same way.
                logger.exception(
                    "CPWorkflow: PFIB restore raised unexpectedly; "
                    "conditions may not have been fully restored"
                )
                failures = [RestoreFailure(
                    field="restore",
                    error=f"{type(exc).__name__}: {exc}",
                )]
            restore_duration_s = time.monotonic() - t0_restore

            if failures:
                # Read by _on_finished on the GUI thread after the
                # worker's queued `finished` emit — ordering
                # guaranteed; see the attribute declarations in
                # WorkflowRunner.__init__.
                self._after_run_warning = _compose_pfib_restore_warning(
                    failures,
                )
                self._after_run_record = _build_pfib_restore_record(
                    failures,
                    restore_species=restore_sp,
                    restore_beam_electrical=restore_vc,
                    duration_s=restore_duration_s,
                )
        elif not completed and self._pfib_recorder is not None:
            # The user asked for a restore, but the run ended by
            # stop / exception / abort — leave the ion beam exactly
            # as it is. Reverting now would command more hardware
            # churn at the worst moment, and a species switch-back
            # in particular re-strikes the plasma source and can
            # take minutes. Log-only: the status bar stays on the
            # stop/error/abort message.
            logger.info(
                "CPWorkflow: skipping PFIB conditions restore (run did "
                "not complete successfully); ion beam left at current "
                "settings"
            )

        # Stage position — success only, and never after an abandon:
        # commanding stage motion right after the user asked to
        # abandon cleanup would contradict the request (and the same
        # Stop-safety rationale that skips it on the stop path).
        if completed and should_abandon():
            logger.warning(
                "CPWorkflow: cleanup abandoned; skipping stage-position "
                "restore"
            )
            if not self._after_run_warning:
                self._after_run_warning = (
                    "Cleanup abandoned — stage not restored"
                )
        elif completed:
            if (self._stage_recorder is not None
                    and self._stage_snapshot is not None):
                on_status("Returning stage to original position...")
                try:
                    self._stage_recorder.restore(self._stage_snapshot)
                except Exception:
                    logger.exception(
                        "CPWorkflow: stage restore failed; stage may not be "
                        "at its original position"
                    )
        elif self._stage_recorder is not None:
            logger.info(
                "CPWorkflow: skipping stage restore (run did not complete "
                "successfully)"
            )

        # Drop all references — next run creates fresh ones.
        self._pfib_snapshot = None
        self._pfib_recorder = None
        self._stage_snapshot = None
        self._stage_recorder = None
        self._settings_snapshot = None

    # --- Activity builders -------------------------------------------------

    def _build_sputter_coat(
        self,
        record: SputterCoatRecord,
        resolved_position,
    ) -> SputterCoatService:
        """Construct a SputterCoatService from a record + resolved position.

        ``resolved_position`` is the :class:`SavedStagePosition` looked
        up from :class:`StagePositionsController` by id. The coater port
        name, HFW, and pattern file come from the workflow settings
        snapshot captured at commit time. Mirrors
        :meth:`_build_gis_deposition`.
        """
        target_position = self._microscope.stage.make_position(
            x=resolved_position.x,
            y=resolved_position.y,
            z=resolved_position.z,
            r=resolved_position.r,
            t=resolved_position.t,
        )
        return SputterCoatService(
            ion_beam_ops=self._microscope.ion_beam,
            gis_ops=self._microscope.gis,
            patterning_ops=self._microscope.patterning,
            stage_ops=self._microscope.stage,
            gis_port_name=self._settings_snapshot.sputter_coat_port_name,
            target_position=target_position,
            position_name=resolved_position.name,
            ion_species_index=record.ion_species_index,
            ion_current_a=record.ion_current_a,
            hfw_um=self._settings_snapshot.sputter_coat_hfw_um,
            pattern_path=resolve_pattern_path(
                self._settings_snapshot.sputter_coat_pattern_file
            ),
            duration_s=record.duration_s,
            chamber_recovery_s=record.chamber_recovery_s,
            instance_id=record.instance_id,
        )

    def _build_gis_deposition(
        self,
        record: GISDepositionRecord,
        resolved_position,
    ) -> GISDepositionService:
        """Construct a GISDepositionService from a record + resolved position.

        ``resolved_position`` is the :class:`SavedStagePosition` looked
        up from :class:`StagePositionsController` by id. We use its
        coordinates to construct the AutoScript-shape position object
        passed to the activity.
        """
        target_position = self._microscope.stage.make_position(
            x=resolved_position.x,
            y=resolved_position.y,
            z=resolved_position.z,
            r=resolved_position.r,
            t=resolved_position.t,
        )
        return GISDepositionService(
            gis_ops=self._microscope.gis,
            stage_ops=self._microscope.stage,
            gis_port_name=self._settings_snapshot.gis_gas_port_name,
            target_position=target_position,
            position_name=resolved_position.name,
            duration_s=record.duration_s,
            chamber_recovery_s=record.chamber_recovery_s,
            zero_tilt_first=(
                self._settings_snapshot.zero_tilt_before_gis_deposition
            ),
            instance_id=record.instance_id,
        )

    def _build_home_stage(self) -> HomeStageService:
        """Construct a HomeStageService for the end-of-workflow home.

        Home Stage takes no parameters; returning the stage to its
        original position is handled by this runner's
        :class:`StageRecorder`, gated on a successful run.
        """
        return HomeStageService(
            stage=self._microscope.stage,
        )