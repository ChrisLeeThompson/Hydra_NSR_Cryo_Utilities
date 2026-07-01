"""RT Prep workflow runner.

Holds the parameters and per-activity enabled-state for the RT Prep page,
and constructs activities on demand as the workflow runner asks for the
next pending activity.

Currently exposes two activities: GIS Purge and Home Stage.

Iterative-fetch architecture
----------------------------
RT walks a static two-element ordered list (GIS Purge, then Home Stage)
in :meth:`_next_pending_activity`. Mid-run switch toggles fold in for
free — a disabled activity isn't pending and isn't returned. The page
has no mechanism for adding activities mid-run (unlike the Cryo page),
so the static order is fixed.

Validation
----------
Two passes, both lighter than :class:`CPWorkflow`'s:

* **Pre-start check at start** — :meth:`_validate_pre_start` gathers
  the checks declared by each enabled activity class and runs them
  through the orchestrator. A REFUSE outcome (none possible today
  on RT — Home Stage's only check is the stage-position advisory)
  refuses the start with :attr:`preStartCheckRefused`. An
  ASK_CONFIRM outcome (e.g. stage in an unusual position when Home
  Stage is enabled) pauses the start pending user confirmation via
  the runner's two-step Start state machine. GIS Purge contributes
  no checks per the pre-start check design (RT-page GIS purge
  doesn't move the stage and has no Z-link dependency).

  RT has no separate parameter-validation pass — parameter values
  are clamped at write time by the Property setters and the
  activity list is static, so there's nothing to validate beyond
  the pre-start checks.

* **Mid-run (planned)** — TODO step 9: per-activity pre-start
  check re-evaluation at fetch time, consulting
  :attr:`WorkflowRunner._confirmed_check_types` to avoid reprompting
  for confirmations the user already accepted at Start.

Persistence
-----------
Parameter values (durations, recovery times) persist across sessions via
:class:`QSettings`, mirroring the pattern used by
:class:`SettingsController`. Keys are namespaced under ``v3/rtWorkflow/``
so they don't collide with app-level settings.

Activity-enabled flags (the container switches) are intentionally *not*
persisted — the user opts in fresh each session, matching v2.1's
behavior. Auto-running hardware activities at startup is the kind of
surprise we want to avoid.

Future Templates feature
------------------------
Workflow templates will serialize the same parameter values via a
different code path (a JSON file the user names) — the parameters are
the source of truth, regardless of whether they're loaded from
QSettings, a template file, or default values.
"""
from __future__ import annotations

import logging
from typing import List, Optional

from PySide6.QtCore import Property, QObject, QSettings, Signal, Slot

from .. import defaults
from ..activities.base import ActivityService, StatusCallback
from ..activities.gis_purge import GISPurgeService
from ..activities.home_stage import HomeStageService
from ..microscope import MicroscopeClientLike
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
from .runner import PreStartValidationResult, WorkflowRunner
from .settings_snapshot import WorkflowSettingsSnapshot

logger = logging.getLogger(__name__)


# Settings-key namespace for RT-workflow parameters. Mirrors the
# SettingsController prefix (defaults.SCHEMA_PREFIX); the additional
# `rtWorkflow/` segment partitions workflow parameters from app
# preferences.
_PREFIX = defaults.SCHEMA_PREFIX + "rtWorkflow/"
_K_GIS_PURGE_DURATION_S = _PREFIX + "gisPurgeDurationS"
_K_GIS_PURGE_CHAMBER_RECOVERY_S = _PREFIX + "gisPurgeChamberRecoveryS"


class RTWorkflow(WorkflowRunner):
    """Workflow runner for the RT Prep page."""

    # Notify signals for the page's parameter properties.
    gisPurgeEnabledChanged = Signal()
    gisPurgeDurationChanged = Signal()
    gisPurgeChamberRecoveryChanged = Signal()
    homeStageEnabledChanged = Signal()

    # Notify signal for canStart. Aggregates: any-activity-enabled +
    # is-not-running.
    canStartChanged = Signal()

    def __init__(
        self,
        microscope: MicroscopeClientLike,
        settings: SettingsController,
        parent: Optional[QObject] = None,
    ) -> None:
        super().__init__(parent=parent)
        self._microscope = microscope
        self._settings = settings

        # Per-activity enabled flags — driven by the ActivityContainer
        # switches. Default off; the user opts in by flipping the switch.
        # Not persisted (see module docstring).
        self._gis_purge_enabled: bool = False
        self._home_stage_enabled: bool = False

        # QSettings handle for parameter persistence. The Organization
        # and Application names are set on QCoreApplication by the entry
        # point, so no explicit path arguments are needed here.
        self._qs = QSettings()

        # Per-activity parameters, loaded from QSettings with defaults.
        self._gis_purge_duration: int = self._read_int_clamped(
            _K_GIS_PURGE_DURATION_S,
            defaults.GIS_PURGE_DURATION_S,
            defaults.GIS_PURGE_DURATION_MIN_S,
            defaults.GIS_PURGE_DURATION_MAX_S,
        )
        self._gis_purge_chamber_recovery: int = self._read_int_clamped(
            _K_GIS_PURGE_CHAMBER_RECOVERY_S,
            defaults.GIS_PURGE_CHAMBER_RECOVERY_S,
            defaults.CHAMBER_RECOVERY_MIN_S,
            defaults.CHAMBER_RECOVERY_MAX_S,
        )

        # Home Stage has no per-activity parameters (its only
        # configuration is the moveStageToOriginalPosition setting,
        # which lives in SettingsController and is read at the moment
        # the activity is constructed for a workflow run).

        # Workflow-settings snapshot — captured in
        # :meth:`_on_commit_to_run` once the runner has committed to
        # the run, consumed by per-activity ``_build_*`` methods,
        # cleared in :meth:`_after_run`. Mirrors the CPWorkflow /
        # PFIB recorder lifecycle even though RT has no recorder of
        # its own; keeps the two workflows symmetric.
        self._settings_snapshot: Optional[WorkflowSettingsSnapshot] = None

        # Stage-position recorder state. RT has no PFIB recorder, but
        # it shares the move-stage-to-original behavior: construct in
        # :meth:`_on_commit_to_run`, capture in :meth:`_before_run`,
        # restore in :meth:`_after_run` on a successful run only.
        self._stage_recorder: Optional[StageRecorder] = None
        self._stage_snapshot: Optional[StagePositionSnapshot] = None

        # canStart depends on isRunning (inherited) — re-emit when it
        # transitions so QML's Start-button binding refreshes.
        self.isRunningChanged.connect(self.canStartChanged)

    # --- canStart aggregator ----------------------------------------------

    @Property(bool, notify=canStartChanged)
    def canStart(self) -> bool:
        """True when Start is meaningful right now.

        Conditions:
            * at least one activity is enabled
            * the workflow isn't already running

        Microscope-connected and other-page-not-running checks are
        handled at the QML page level (the page binds to
        ``appController.isConnected`` and the cross-page disable flag).
        Keeping those checks out of canStart means RTWorkflow doesn't
        need to know about AppController, which keeps the dependency
        graph clean.
        """
        if self.isRunning:
            return False
        if not self._any_activity_enabled():
            return False
        return True

    def _any_activity_enabled(self) -> bool:
        return self._gis_purge_enabled or self._home_stage_enabled

    # --- Mid-run opt-out -------------------------------------------------
    #
    # Mid-run switch toggles are honored automatically: each iteration,
    # the runner calls _next_pending_activity, which consults the
    # current enabled flags. A switch toggled off after Start makes
    # the activity invisible to the next fetch — no separate
    # skip-callback needed.
    #
    # The QML binding locks switches on activities past the idle state
    # (running, complete, exception, stop), so toggles only ever fire
    # for activities the user can still meaningfully change.

    # --- Lifecycle hooks --------------------------------------------------

    def _validate_pre_start(self) -> PreStartValidationResult:
        """Pre-start check pass.

        Single-stage pipeline (compared to CPWorkflow's three): RT
        has no parameter-validation pass — values are clamped by the
        Property setters and the activity list is static. The only
        start-time validation is the pre-start check pass.

        Gather checks from enabled activity classes and route the
        outcome:

        * REFUSE → emit :attr:`preStartCheckRefused`, status
          breadcrumb ``"Pre-start check failed"``, return
          ``"refused"``.
        * ASK_CONFIRM → emit :attr:`preStartCheckNeedsConfirmation`,
          return ``"needs_confirmation"`` with the set of check
          types (carried forward into
          :attr:`WorkflowRunner._confirmed_check_types` on accept).
        * PASS / no checks gathered → return ``"ok"``.

        State capture (workflow settings snapshot) moves to
        :meth:`_on_commit_to_run`, which fires only when the runner
        has committed to actually starting the run — either an
        outright OK or an accepted confirmation.
        """
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
                    "RTWorkflow: pre-start hardware read failed; refusing "
                    "to start"
                )
                self.statusUpdated.emit("Cannot start: hardware read failed")
                return PreStartValidationResult(outcome="refused")

            if summary.refused:
                items = to_dialog_items(summary.refused)
                self.preStartCheckRefused.emit(items)
                self.statusUpdated.emit("Pre-start check failed")
                logger.info(
                    "RTWorkflow: refusing to start "
                    "(%d pre-start check%s failed)",
                    len(summary.refused),
                    "" if len(summary.refused) == 1 else "s",
                )
                return PreStartValidationResult(outcome="refused")

            if summary.needs_confirmation:
                items = to_dialog_items(summary.needs_confirmation)
                self.preStartCheckNeedsConfirmation.emit(items)
                logger.info(
                    "RTWorkflow: awaiting user confirmation "
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

        return PreStartValidationResult(outcome="ok")

    def _gather_pre_start_checks(self) -> List[PreStartCheck]:
        """Walk enabled activities, collect their pre-start checks.

        RT has two activities; today only Home Stage contributes a
        check (GIS Purge has none per the pre-start check design).
        The orchestrator deduplicates by type, so this is robust to
        future additions where multiple activities request the same
        check.

        Mirrors :meth:`CPWorkflow._gather_pre_start_checks` in shape
        — separate implementation because RT walks fixed flags
        rather than the cryo-activities record list.
        """
        checks: List[PreStartCheck] = []
        if self._gis_purge_enabled:
            checks.extend(GISPurgeService.pre_start_checks())
        if self._home_stage_enabled:
            checks.extend(HomeStageService.pre_start_checks())
        return checks

    def _on_commit_to_run(self) -> None:
        """Capture the workflow-settings snapshot.

        Fires on every path that commits to a run — either an
        outright ``"ok"`` from :meth:`_validate_pre_start` or an
        accepted confirmation via
        :meth:`WorkflowRunner.respondToConfirmation`. Never fires
        on refused or cancelled paths, so the captured snapshot
        always binds to a run that's about to start.

        See
        :mod:`hydra_nsr_cu.workflows.settings_snapshot.WorkflowSettingsSnapshot`
        for why capture moved here from ``_validate_pre_start``:
        with the two-step Start, ``_validate_pre_start`` may pause
        for an arbitrary amount of time on the confirm dialog, and
        capturing settings there would either lock the user out of
        editing settings during the wait, or capture stale state by
        the time they accept.

        RT has no recorder to set up — only the settings snapshot.
        CPWorkflow's override does both; the symmetry is intentional
        for consumers reading either side of the codebase.
        """
        self._settings_snapshot = WorkflowSettingsSnapshot.from_settings(
            self._settings
        )

        if self._settings_snapshot.move_stage_to_original:
            self._stage_recorder = StageRecorder(self._microscope.stage)
            logger.info(
                "RTWorkflow: move-stage-to-original is enabled"
            )
        else:
            self._stage_recorder = None

    def _before_run(self, on_status: StatusCallback) -> None:
        """Capture the stage position before any activity runs.

        RT has no PFIB recorder; the only capture is the stage
        position, set up in :meth:`_on_commit_to_run` when
        move-stage-to-original is enabled. Best-effort: a capture
        failure is logged and the snapshot left None (restore
        skipped) without aborting the run.
        """
        if self._stage_recorder is not None:
            on_status("Capturing stage position...")
            try:
                self._stage_snapshot = self._stage_recorder.capture()
            except Exception:
                logger.exception(
                    "RTWorkflow: stage capture failed; proceeding without "
                    "restore"
                )
                self._stage_snapshot = None

    def _after_run(self, on_status: StatusCallback, completed: bool) -> None:
        """Restore the stage position (success only) and clear snapshots.

        RT has no PFIB recorder, so the only restore is the stage
        position, and only on a fully successful run (``completed``
        is True). After a stop or exception the stage is left where it
        is — commanding further motion after an abnormal exit is
        unsafe. References (stage recorder/snapshot and the settings
        snapshot) are cleared unconditionally so nothing leaks into a
        subsequent run.
        """
        if completed:
            if (self._stage_recorder is not None
                    and self._stage_snapshot is not None):
                on_status("Returning stage to original position...")
                try:
                    self._stage_recorder.restore(self._stage_snapshot)
                except Exception:
                    logger.exception(
                        "RTWorkflow: stage restore failed; stage may not be "
                        "at its original position"
                    )
        elif self._stage_recorder is not None:
            logger.info(
                "RTWorkflow: skipping stage restore (run did not complete "
                "successfully)"
            )

        self._stage_snapshot = None
        self._stage_recorder = None
        self._settings_snapshot = None

    # --- Iterative activity fetch (used by WorkflowRunner) ---------------

    def _next_pending_activity(
        self, executed_keys,
    ) -> Optional[ActivityService]:
        """Return the next enabled activity not yet executed in this run.

        RT exposes two single-instance activities — GIS Purge first,
        then Home Stage. Both have ``instance_id == ""``, so their
        keys are ``"gis_purge/"`` and ``"home_stage/"``.

        Mid-run toggles fold in here for free: if the user toggles
        an activity off after Start, it's no longer "enabled" at the
        next fetch and we skip past it. The QML binding locks switches
        on activities that have already started, so this only happens
        for activities still in idle state.

        Mid-run additions don't apply to RT — there's no UI for adding
        activities to the page. The static order is fixed.

        Each candidate goes through a mid-run pre-start check pass
        (:meth:`_mid_run_pre_start_check_passes`) before being built.
        REFUSE outcomes — and ASK_CONFIRM outcomes whose check type
        was not pre-confirmed at workflow Start — abort the workflow.
        GIS Purge has no checks declared today, so its helper call
        short-circuits to True; the call is included for consistency
        and to leave room for future GIS-Purge-specific checks
        without changing the call structure.

        If GIS Purge can't be built (e.g. empty port name in settings),
        we mark its key as executed and fall through to Home Stage.
        That matches v2.1 / pre-refactor behavior — Home Stage
        shouldn't be punished for a misconfigured GIS port. Note the
        asymmetry vs. pre-start check failure: build-time misconfig
        is "skip and continue" (per-activity), pre-start check
        failure is "abort workflow" (whole-run). They're different
        kinds of failure.
        """
        gis_purge_key = f"{GISPurgeService.activity_id}/"
        home_stage_key = f"{HomeStageService.activity_id}/"

        if (self._gis_purge_enabled
                and gis_purge_key not in executed_keys):
            # Mid-run pre-start check. GIS Purge has no checks today
            # but the call is included for consistency.
            if not self._mid_run_pre_start_check_passes(
                GISPurgeService, self._microscope,
            ):
                return None
            activity = self._build_gis_purge()
            if activity is not None:
                return activity
            # Build returned None — treat as "skip" so Home Stage can
            # still run. Mark the key so we don't retry next iteration.
            executed_keys.add(gis_purge_key)

        if (self._home_stage_enabled
                and home_stage_key not in executed_keys):
            if not self._mid_run_pre_start_check_passes(
                HomeStageService, self._microscope,
            ):
                return None
            return self._build_home_stage()

        return None

    def _build_gis_purge(self) -> Optional[ActivityService]:
        """Construct a GISPurgeService from current parameter values.

        Returns ``None`` if the configured GIS port name is empty
        (a settings-level misconfiguration), with a logged warning.
        The runner treats a ``None`` result like "no more pending"
        and ends the loop — equivalent to the old behavior of
        silently dropping the activity from the snapshotted list.
        """
        port_name = self._settings_snapshot.gis_gas_port_name.strip()
        if not port_name:
            logger.warning(
                "GIS purge: port name is empty in settings; "
                "skipping activity"
            )
            return None
        return GISPurgeService(
            gis=self._microscope.gis,
            port_name=port_name,
            purge_duration_s=self._gis_purge_duration,
            chamber_recovery_s=self._gis_purge_chamber_recovery,
        )

    def _build_home_stage(self) -> ActivityService:
        """Construct a HomeStageService.

        Home Stage takes no parameters — the former ``move_to_original``
        arg was removed when stage restore became a workflow-level
        concern (handled by this runner's :class:`StageRecorder`, gated
        on a successful run).
        """
        return HomeStageService(
            stage=self._microscope.stage,
        )

    # --- GIS Purge — enabled flag -----------------------------------------

    @Property(bool, notify=gisPurgeEnabledChanged)
    def gisPurgeEnabled(self) -> bool:
        return self._gis_purge_enabled

    @gisPurgeEnabled.setter
    def gisPurgeEnabled(self, value: bool) -> None:
        value = bool(value)
        if value == self._gis_purge_enabled:
            return
        self._gis_purge_enabled = value
        self.gisPurgeEnabledChanged.emit()
        self.canStartChanged.emit()

    # --- GIS Purge — duration ---------------------------------------------

    @Property(int, notify=gisPurgeDurationChanged)
    def gisPurgeDuration(self) -> int:
        return self._gis_purge_duration

    @gisPurgeDuration.setter
    def gisPurgeDuration(self, value: int) -> None:
        value = self._clamp(
            int(value),
            defaults.GIS_PURGE_DURATION_MIN_S,
            defaults.GIS_PURGE_DURATION_MAX_S,
        )
        if value == self._gis_purge_duration:
            return
        self._gis_purge_duration = value
        self._qs.setValue(_K_GIS_PURGE_DURATION_S, value)
        self.gisPurgeDurationChanged.emit()

    # --- GIS Purge — chamber recovery -------------------------------------

    @Property(int, notify=gisPurgeChamberRecoveryChanged)
    def gisPurgeChamberRecovery(self) -> int:
        return self._gis_purge_chamber_recovery

    @gisPurgeChamberRecovery.setter
    def gisPurgeChamberRecovery(self, value: int) -> None:
        value = self._clamp(
            int(value),
            defaults.CHAMBER_RECOVERY_MIN_S,
            defaults.CHAMBER_RECOVERY_MAX_S,
        )
        if value == self._gis_purge_chamber_recovery:
            return
        self._gis_purge_chamber_recovery = value
        self._qs.setValue(_K_GIS_PURGE_CHAMBER_RECOVERY_S, value)
        self.gisPurgeChamberRecoveryChanged.emit()

    # --- Home Stage — enabled flag ----------------------------------------

    @Property(bool, notify=homeStageEnabledChanged)
    def homeStageEnabled(self) -> bool:
        return self._home_stage_enabled

    @homeStageEnabled.setter
    def homeStageEnabled(self, value: bool) -> None:
        value = bool(value)
        if value == self._home_stage_enabled:
            return
        self._home_stage_enabled = value
        self.homeStageEnabledChanged.emit()
        self.canStartChanged.emit()

    # --- QML-callable slots -----------------------------------------------

    @Slot()
    def reset_parameters(self) -> None:
        """Reset all RT workflow parameters to their factory defaults.

        The setters write to QSettings as a side effect, so the
        defaults persist immediately — if the user closes the app
        after clicking Reset Parameters, the next launch will see the
        defaults rather than the prior values.

        Activity-enabled flags (the switches) are intentionally not
        reset. Resetting parameters is about parameter values, not
        about which activities the user has opted into. Home Stage
        currently has no per-activity parameters, so it's not affected
        by this slot at all.
        """
        logger.info("RT workflow: resetting parameters")
        self.gisPurgeDuration = defaults.GIS_PURGE_DURATION_S
        self.gisPurgeChamberRecovery = defaults.GIS_PURGE_CHAMBER_RECOVERY_S

    # --- QSettings type-coercion helpers ----------------------------------

    def _read_int_clamped(
        self, key: str, default: int, lo: int, hi: int
    ) -> int:
        raw = self._qs.value(key)
        if raw is None:
            return default
        try:
            value = int(raw)
        except (TypeError, ValueError):
            logger.warning(
                "Could not parse int from QSettings key %r (got %r); "
                "using default %r",
                key, raw, default,
                exc_info=True,
            )
            return default
        return self._clamp(value, lo, hi)

    @staticmethod
    def _clamp(value: int, lo: int, hi: int) -> int:
        if value < lo:
            return lo
        if value > hi:
            return hi
        return value