"""RT Prep workflow runner.

Holds the parameters and per-activity enabled-state for the RT Prep
page, and constructs activities on demand as the workflow runner asks
for the next pending activity. Exposes two activities in a fixed
order: GIS Purge, then Home Stage. Mid-run switch toggles take effect
at the next fetch; the page has no mechanism for adding activities.

Validation is lighter than :class:`CPWorkflow`'s. Parameter values
are clamped at write time by the Property setters and the activity
list is static, so the only start-time pass is the pre-start checks
declared by the enabled activity classes (REFUSE / ASK_CONFIRM via
the runner's two-step Start). The same checks are re-evaluated per
activity at fetch time, honoring confirmations the user already
accepted at Start (:attr:`WorkflowRunner._confirmed_check_types`).
GIS Purge declares no checks — it doesn't move the stage and has no
Z-link dependency.

Parameter values (durations, recovery times) persist across sessions
via :class:`QSettings` under ``v3/rtWorkflow/``. Activity-enabled
flags are intentionally *not* persisted — the user opts in fresh each
session so hardware activities never auto-run at startup. Workflow
templates serialize the same parameter values through a separate
JSON path; the parameters are the source of truth regardless of where
they were loaded from.
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
from .runner import (
    FetchOutcome,
    PreStartValidationResult,
    WorkflowRunner,
    build_abandon_predicate,
)
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

        RT has no parameter-validation stage (values are clamped by
        the Property setters and the activity list is static), so this
        gathers the checks from the enabled activity classes and routes
        the outcome:

        * REFUSE → emit :attr:`preStartCheckRefused`, status
          breadcrumb ``"Pre-start check failed"``, return ``"refused"``.
        * ASK_CONFIRM → emit :attr:`preStartCheckNeedsConfirmation`,
          return ``"needs_confirmation"`` with the pending check types.
        * PASS / no checks gathered → return ``"ok"``.

        State capture happens in :meth:`_on_commit_to_run`, not here.
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
        check (GIS Purge declares none). The orchestrator deduplicates
        by type, so multiple activities requesting the same check
        produce one evaluation.

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
        """Capture the workflow-settings snapshot and set up the stage recorder.

        Fires on every path that commits to a run — an outright
        ``"ok"`` from :meth:`_validate_pre_start` or an accepted
        confirmation via :meth:`WorkflowRunner.respondToConfirmation`
        — and never on refused or cancelled paths. Capturing here
        rather than in ``_validate_pre_start`` means the snapshot
        reflects the settings as of the moment the run actually
        commits, however long the confirm dialog stayed open. See
        :class:`WorkflowSettingsSnapshot`.
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
        # Latched Stop-press abandon semantics — a Stop press while
        # only this cleanup is running (or a second press on the stop
        # path) skips the stage move. See build_abandon_predicate.
        should_abandon = build_abandon_predicate(
            self._stop_event, self._abandon_event,
        )

        if completed and should_abandon():
            logger.warning(
                "RTWorkflow: cleanup abandoned; skipping stage-position "
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
    ) -> FetchOutcome:
        """Return the next enabled activity not yet executed in this run.

        RT exposes two single-instance activities in fixed order — GIS
        Purge (key ``"gis_purge/"``), then Home Stage (key
        ``"home_stage/"``). An activity toggled off after Start is
        simply not returned at the next fetch.

        Each candidate goes through :meth:`_mid_run_pre_start_check_passes`
        before being built; a failure aborts the workflow. If GIS Purge
        can't be built (empty port name in settings), its key is marked
        executed and the fetch falls through to Home Stage — a
        build-time misconfiguration skips that activity, whereas a
        pre-start check failure aborts the whole run.
        """
        gis_purge_key = f"{GISPurgeService.activity_id}/"
        home_stage_key = f"{HomeStageService.activity_id}/"

        if (self._gis_purge_enabled
                and gis_purge_key not in executed_keys):
            # Mid-run pre-start check. GIS Purge has no checks today
            # but the call is included for consistency. The helper
            # records the abort reason — no message here.
            if not self._mid_run_pre_start_check_passes(
                GISPurgeService, self._microscope,
            ):
                return self._abort_fetch()
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
                return self._abort_fetch()
            return self._build_home_stage()

        return None

    def _build_gis_purge(self) -> Optional[ActivityService]:
        """Construct a GISPurgeService from current parameter values.

        Returns ``None`` if the configured GIS port name is empty
        (a settings-level misconfiguration), with a logged warning.
        The caller (:meth:`_next_pending_activity`) treats a ``None``
        result as "handled but skipped" — it marks the key as
        executed and falls through to the next activity, so the rest
        of the workflow still runs.
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

        Home Stage takes no parameters; returning the stage to its
        original position is handled by this runner's
        :class:`StageRecorder`, gated on a successful run.
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