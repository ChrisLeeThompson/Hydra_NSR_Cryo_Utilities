"""Cryo Activities controller — QML-facing surface for the activity list.

Owns the :class:`CryoActivitiesModel` and the JSON persistence path.
Mirrors the structure of :class:`StagePositionsController` from
Session 4.

Responsibilities:

* Constructs the model and seeds it on first launch with the default
  activity list (or loads a previously-saved list from QSettings).
* Exposes high-level operations to QML: ``add_sputter_coat``,
  ``add_gis_deposition``, ``remove_at``, ``move``, ``restore_defaults``,
  and per-parameter setters.
* Persists the activity list as a JSON string in QSettings on every
  successful mutation.
* Exposes parameter bounds (min/max) as ``constant=True`` Properties
  for QML SpinBox ``from`` / ``to`` bindings.

The controller is the only mutation path. QML never edits records
directly — it calls a setter, which validates / clamps / persists.

Workflow execution
------------------
This module owns the *state* of the Cryo activity list. The actual
execution logic — translating records into running
:class:`ActivityService` instances — lives in
:class:`CPWorkflow` (Session 5D) and reads from this controller's
model when Start is clicked.
"""
from __future__ import annotations

import json
import logging
import math
import os
import sys
from pathlib import Path
from typing import List, Optional

from PySide6.QtCore import (
    Property,
    QObject,
    QSettings,
    QUrl,
    Signal,
    Slot,
)

from .. import defaults
from ..stage_positions.positions_model import SavedStagePosition
from .activities_model import CryoActivitiesModel
from .activity_records import (
    ACTIVITY_TYPE_GIS_DEPOSITION,
    ACTIVITY_TYPE_SPUTTER_COAT,
    ActivityRecord,
    GISDepositionRecord,
    SputterCoatRecord,
    make_default_record,
    record_from_dict,
)
from . import templates as template_io

logger = logging.getLogger(__name__)


# QSettings key for the persisted activity list.
_K_ACTIVITY_LIST = defaults.SCHEMA_PREFIX + "cryoActivities/list"


def _clamp(value: int, lo: int, hi: int) -> int:
    if value < lo:
        return lo
    if value > hi:
        return hi
    return value


class CryoActivitiesController(QObject):
    """Cryo activity list, exposed to QML as ``appController.cryoActivities``."""

    # Template feature signals — emitted from save_template/load_template.
    # See those slot docstrings for the success/failure contracts.
    templateLoadSucceeded = Signal(str, list)   # template_name, issues
    templateLoadFailed = Signal(str, str)        # template_name, error_message
    templateSaveSucceeded = Signal(str)          # template_name
    templateSaveFailed = Signal(str)             # error_message

    def __init__(self, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._model = CryoActivitiesModel(parent=self)
        self._qs = QSettings()
        # Positions lookup is wired late by AppController via
        # set_positions_lookup() — StagePositionsController is
        # microscope-dependent and doesn't exist at our construction time.
        # Template save/load fail cleanly until the wire-up happens.
        self._positions: Optional[template_io.PositionsLookup] = None
        self._load_or_seed()
    
    def set_positions_lookup(
        self, positions: template_io.PositionsLookup,
    ) -> None:
        """Wire the positions lookup used by template save/load.

        Called by AppController after both this controller and
        StagePositionsController are constructed. Templates are inert
        until this is called.
        """
        self._positions = positions

    # --- QML-visible properties --------------------------------------------

    @Property(QObject, constant=True)
    def model(self) -> CryoActivitiesModel:
        """The list model. Bound to the QML ListView's ``model`` property."""
        return self._model

    # --- Parameter bounds (constant Properties for SpinBox from/to) -------

    @Property(int, constant=True)
    def sputterDurationMin(self) -> int:
        return defaults.SPUTTER_DURATION_MIN_S

    @Property(int, constant=True)
    def sputterDurationMax(self) -> int:
        return defaults.SPUTTER_DURATION_MAX_S

    @Property(int, constant=True)
    def gisDurationMin(self) -> int:
        return defaults.GIS_DEPOSITION_DURATION_MIN_S

    @Property(int, constant=True)
    def gisDurationMax(self) -> int:
        return defaults.GIS_DEPOSITION_DURATION_MAX_S

    @Property(int, constant=True)
    def chamberRecoveryMin(self) -> int:
        return defaults.CHAMBER_RECOVERY_MIN_S

    @Property(int, constant=True)
    def chamberRecoveryMax(self) -> int:
        return defaults.CHAMBER_RECOVERY_MAX_S
    
    @Property(QUrl, constant=True)
    def templatesDirUrl(self) -> QUrl:
        """Default folder URL for the Save / Load template file dialogs.

        Resolves to the ``templates/`` directory at the project root —
        two levels above this controller file
        (``hydra_nsr_cu/cryo/controller.py`` -> ``hydra_nsr_cu/`` ->
        project root). If your deployment layout differs, override this
        method or change the path computation.

        The directory is not auto-created — if it doesn't exist, the
        file dialog falls back to the OS default. First-time saves into
        a missing ``templates/`` directory fail with an actionable
        error; create the directory and retry.
        """
        project_root = Path(__file__).resolve().parent.parent.parent
        templates_dir = project_root / "templates"
        return QUrl.fromLocalFile(str(templates_dir))

    # --- List operations ---------------------------------------------------

    @Slot()
    def add_sputter_coat(self) -> None:
        """Append a fresh Sputter Coat activity with default parameters."""
        record = make_default_record(ACTIVITY_TYPE_SPUTTER_COAT)
        self._model.append_record(record)
        self._save()
        logger.info(
            "Cryo activities: added Sputter Coat (instance=%s)",
            record.instance_id,
        )

    @Slot()
    def add_gis_deposition(self) -> None:
        """Append a fresh GIS Deposition activity with default parameters."""
        record = make_default_record(ACTIVITY_TYPE_GIS_DEPOSITION)
        self._model.append_record(record)
        self._save()
        logger.info(
            "Cryo activities: added GIS Deposition (instance=%s)",
            record.instance_id,
        )

    @Slot(int, result=bool)
    def remove_at(self, index: int) -> bool:
        """Remove the activity at ``index``."""
        rec = self._model.record_at(index)
        if rec is None:
            return False
        ok = self._model.remove_at(index)
        if ok:
            self._save()
            logger.info(
                "Cryo activities: removed %s (instance=%s)",
                rec.activity_type, rec.instance_id,
            )
        return ok

    @Slot(int, int, result=bool)
    def move(self, from_index: int, to_index: int) -> bool:
        """Move the activity at ``from_index`` to ``to_index``."""
        ok = self._model.move(from_index, to_index)
        if ok:
            self._save()
            logger.info(
                "Cryo activities: moved %d → %d", from_index, to_index
            )
        return ok

    @Slot()
    def reset_parameters(self) -> None:
        """Reset every activity's parameters to factory defaults, in place.

        The activity list itself is preserved — same activities, same
        order, same instance ids. Only per-activity parameters are
        reset to the values that ``make_default_record`` would produce.

        For Sputter Coat and GIS Deposition activities, ``position_id``
        is *preserved* (not reset). The position is closer to a
        structural choice (pointing at a saved entity outside the
        activity) than to a per-activity parameter, and preserving it
        matches user intuition for "reset parameters" — the activity
        stays targeting the same place, just with default values.

        Implementation note: rows are updated in place via
        :meth:`CryoActivitiesModel.replace_at` rather than wholesale via
        :meth:`replace_all`. Both reach the same end state for the model,
        but ``replace_all`` triggers ``beginResetModel`` /
        ``endResetModel``, which causes QML to destroy and recreate every
        delegate — discarding delegate-local presentational state,
        including each activity's ``expanded`` flag. ``replace_at`` emits
        a per-row ``dataChanged`` instead, so existing delegates survive
        and any expanded activities stay expanded.

        Used by the page's "Reset Parameters" button. The first-launch
        seeding path uses :meth:`_seed_with_defaults` instead, which
        replaces the entire list (no delegates exist yet).
        """
        records = self._model.all_records()
        changed = 0
        for index, old in enumerate(records):
            fresh = make_default_record(old.activity_type)
            # Preserve the instance id so any future cross-references
            # (templates, status routing) keep working — and any
            # structural fields that aren't parameters per se.
            if isinstance(old, SputterCoatRecord):
                new_rec = fresh.with_changes(
                    instance_id=old.instance_id,
                    # position_id is preserved — see method docstring.
                    position_id=old.position_id,
                )
            elif isinstance(old, GISDepositionRecord):
                new_rec = fresh.with_changes(
                    instance_id=old.instance_id,
                    # position_id is preserved — see method docstring.
                    position_id=old.position_id,
                )
            else:
                # Unknown record type — leave the row untouched.
                logger.warning(
                    "Cryo reset: unknown record type %s; preserving as-is",
                    type(old).__name__,
                )
                continue
            if self._model.replace_at(index, new_rec):
                changed += 1
        self._save()
        logger.info(
            "Cryo activities: reset parameters for %d activit%s",
            changed,
            "y" if changed == 1 else "ies",
        )

    def _seed_with_defaults(self) -> None:
        """Replace the activity list with the factory-default 4-activity list.

        Used only by the first-launch path in :meth:`_load_or_seed`.
        Not exposed to QML — users go through :meth:`reset_parameters`
        for their workflow customization, which preserves the list shape.
        Replacing the list wholesale is reserved for the moment when
        there is no list yet.
        """
        records = self._build_default_records()
        self._model.replace_all(records)
        self._save()
        logger.info(
            "Cryo activities: seeded with %d default activit%s",
            len(records),
            "y" if len(records) == 1 else "ies",
        )

    # --- Sputter Coat parameter setters ------------------------------------

    @Slot(str, str, result=bool)
    def set_sputter_position_id(self, instance_id: str, value: str) -> bool:
        return self._update_record(
            instance_id, SputterCoatRecord, position_id=str(value)
        )

    @Slot(str, int, result=bool)
    def set_sputter_ion_species_index(
        self, instance_id: str, value: int
    ) -> bool:
        return self._update_record(
            instance_id, SputterCoatRecord, ion_species_index=int(value)
        )

    @Slot(str, float, result=bool)
    def set_sputter_ion_current_a(
        self, instance_id: str, value: float
    ) -> bool:
        """Set the sputter beam current in amperes.

        Replaces the older ``set_sputter_ion_current_index`` —
        currents are now stored as the actual amperes value rather
        than a UI-list index, so changing species doesn't invalidate
        the saved current. AutoScript snaps to the nearest available
        current for the active species.

        Rejects a non-finite value (NaN / +-Inf) defensively — it must
        never reach the PFIB plasma beam current. Mirrors the finite
        guard the template loader applies in ``templates._coerce_float``.
        """
        fvalue = float(value)
        if not math.isfinite(fvalue):
            logger.warning(
                "Cryo activities: rejecting non-finite sputter ion "
                "current %r for %r", value, instance_id,
            )
            return False
        return self._update_record(
            instance_id, SputterCoatRecord, ion_current_a=fvalue
        )

    @Slot(str, int, result=bool)
    def set_sputter_duration(self, instance_id: str, value: int) -> bool:
        clamped = _clamp(
            int(value),
            defaults.SPUTTER_DURATION_MIN_S,
            defaults.SPUTTER_DURATION_MAX_S,
        )
        return self._update_record(
            instance_id, SputterCoatRecord, duration_s=clamped
        )

    @Slot(str, int, result=bool)
    def set_sputter_chamber_recovery(
        self, instance_id: str, value: int
    ) -> bool:
        clamped = _clamp(
            int(value),
            defaults.CHAMBER_RECOVERY_MIN_S,
            defaults.CHAMBER_RECOVERY_MAX_S,
        )
        return self._update_record(
            instance_id, SputterCoatRecord, chamber_recovery_s=clamped
        )

    # --- GIS Deposition parameter setters ----------------------------------

    @Slot(str, str, result=bool)
    def set_gis_position_id(self, instance_id: str, value: str) -> bool:
        return self._update_record(
            instance_id, GISDepositionRecord, position_id=str(value)
        )

    @Slot(str, int, result=bool)
    def set_gis_duration(self, instance_id: str, value: int) -> bool:
        clamped = _clamp(
            int(value),
            defaults.GIS_DEPOSITION_DURATION_MIN_S,
            defaults.GIS_DEPOSITION_DURATION_MAX_S,
        )
        return self._update_record(
            instance_id, GISDepositionRecord, duration_s=clamped
        )

    @Slot(str, int, result=bool)
    def set_gis_chamber_recovery(self, instance_id: str, value: int) -> bool:
        clamped = _clamp(
            int(value),
            defaults.CHAMBER_RECOVERY_MIN_S,
            defaults.CHAMBER_RECOVERY_MAX_S,
        )
        return self._update_record(
            instance_id, GISDepositionRecord, chamber_recovery_s=clamped
        )
    
    # --- Template save / load ----------------------------------------------

    @Slot(QUrl, bool, str, result=bool)
    def save_template(
        self,
        file_url: QUrl,
        include_positions: bool = False,
        scope: str = template_io.POSITION_SCOPE_REFERENCED,
    ) -> bool:
        """Save the current activity list as a JSON template file.

        ``include_positions`` bundles stage positions into the file. When
        true, ``scope`` selects which — ``POSITION_SCOPE_REFERENCED``
        (only positions the activities point at) or
        ``POSITION_SCOPE_ALL`` (the whole saved library). When false, the
        template is activities-only, identical in shape to a pre-feature
        template.

        Emits ``templateSaveSucceeded(template_name)`` on success or
        ``templateSaveFailed(error_message)`` on failure. The QML Save
        flow invokes this with the file URL from the FileDialog and the
        options chosen in the save-options dialog.
        """
        if self._positions is None:
            msg = "Cannot save template: stage positions not available."
            logger.warning("Cryo templates: %s", msg)
            self.templateSaveFailed.emit(msg)
            return False

        path = file_url.toLocalFile()
        if not path:
            msg = "Cannot save template: file path is empty."
            logger.warning("Cryo templates: %s", msg)
            self.templateSaveFailed.emit(msg)
            return False

        template_name = Path(path).stem
        records = self._model.all_records()
        stage_positions = self._positions_to_bundle(
            include_positions, scope, records,
        )
        try:
            payload = template_io.build_template_payload(
                records, self._positions, name=template_name,
                stage_positions=stage_positions,
            )
            # Atomic write: stream to a sibling ``.tmp`` on the same
            # volume, then ``os.replace()`` onto the destination.
            # ``os.replace`` is atomic on POSIX and Windows, so a crash,
            # full disk, or external lock mid-write leaves any
            # pre-existing template fully intact rather than truncated —
            # templates are the cross-machine portability artifact and
            # may be the only copy of a hand-built workflow. Mirrors
            # ``session_log.persistence.atomic_rewrite`` (kept inline
            # rather than reused because that helper writes JSONL, while
            # templates are a single indented JSON object).
            tmp_path = Path(path).with_suffix(Path(path).suffix + ".tmp")
            try:
                with open(tmp_path, "w", encoding="utf-8") as f:
                    json.dump(payload, f, indent=4)
                    f.write("\n")
                os.replace(tmp_path, path)
            except BaseException:
                # Best-effort cleanup of the partial tmp file; never
                # mask the original error.
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass
                raise
        except (OSError, TypeError, ValueError) as exc:
            logger.exception("Cryo templates: save failed")
            self.templateSaveFailed.emit(
                f"Could not save template: {exc}"
            )
            return False

        logger.info(
            "Cryo templates: saved %r with %d activit%s (%s)",
            template_name, len(records),
            "y" if len(records) == 1 else "ies",
            "no positions" if stage_positions is None
            else f"{len(stage_positions)} position(s)",
        )
        self.templateSaveSucceeded.emit(template_name)
        return True


    @Slot(QUrl, bool, result=bool)
    def load_template(
        self, file_url: QUrl, load_positions: bool = False,
    ) -> bool:
        """Load a template file and replace the current activity list.

        When ``load_positions`` is true and the file carries bundled
        positions, they are merged into the saved-positions list before
        the activities are resolved — non-destructively (see
        :meth:`StagePositionsController.import_positions`) — so each GIS
        step binds to the template's own copy rather than to a same-named
        position that already happened to exist locally. Position-merge
        notes and activity adjustments are surfaced together in the load
        summary.

        Two-tier validation:

        * Structural failures (file unreadable, not JSON, missing or
        malformed top-level shape, mismatched schema_version) leave
        the existing list — and the saved positions — untouched and
        emit ``templateLoadFailed(template_name, error_message)``.
        * Data failures (out-of-range values, unknown activity types,
        unmatched position names) are repaired into a list of issue
        messages, the activity list is replaced, and
        ``templateLoadSucceeded(template_name, issues)`` is emitted
        with the issues. The caller decides clean vs. adjusted UX
        based on whether the issues list is empty.
        """
        if self._positions is None:
            msg = "Cannot load template: stage positions not available."
            logger.warning("Cryo templates: %s", msg)
            self.templateLoadFailed.emit("", msg)
            return False

        path = file_url.toLocalFile()
        if not path:
            msg = "Cannot load template: file path is empty."
            logger.warning("Cryo templates: %s", msg)
            self.templateLoadFailed.emit("", msg)
            return False

        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except FileNotFoundError:
            msg = f"Template file not found: {path}"
            logger.warning("Cryo templates: %s", msg)
            self.templateLoadFailed.emit("", msg)
            return False
        except (OSError, json.JSONDecodeError) as exc:
            msg = f"Could not read template: {exc}"
            logger.warning("Cryo templates: %s", msg, exc_info=True)
            self.templateLoadFailed.emit("", msg)
            return False

        # First pass: structural validation plus a baseline activity
        # parse. This has no side effects, so a structural failure leaves
        # the current list — and the saved positions — untouched.
        result = template_io.parse_template_payload(data, self._positions)

        if not result.succeeded:
            logger.warning(
                "Cryo templates: load rejected: %s", result.error_message,
            )
            self.templateLoadFailed.emit(
                result.template_name, result.error_message,
            )
            return False

        # Structure is valid — now it is safe to merge bundled positions
        # (if the user opted in) and re-resolve the activities against
        # them, so a GIS step binds to the template's imported copy.
        extra_issues: List[str] = []
        if load_positions:
            bundled, parse_issues = template_io.parse_stage_positions(data)
            extra_issues.extend(parse_issues)
            if bundled:
                name_map, import_issues = self._positions.import_positions(
                    bundled,
                )
                extra_issues.extend(import_issues)
                result = template_io.parse_template_payload(
                    data, self._positions, position_name_remap=name_map,
                )

        issues = extra_issues + list(result.issues)

        # Replace the activity list and persist to QSettings. Order
        # matters: replace_all first so _save() snapshots the new list.
        self._model.replace_all(result.records)
        self._save()

        # Mirror each issue to the log so a power user can dig into them
        # later even after the modal is dismissed.
        for issue in issues:
            logger.info("Cryo templates: load issue: %s", issue)

        logger.info(
            "Cryo templates: loaded %r with %d activit%s "
            "(%d adjustment%s)",
            result.template_name,
            len(result.records),
            "y" if len(result.records) == 1 else "ies",
            len(issues),
            "" if len(issues) == 1 else "s",
        )
        self.templateLoadSucceeded.emit(result.template_name, issues)
        return True

    @Slot(result=int)
    def referenced_position_count(self) -> int:
        """Count of distinct saved positions the activities point at.

        Drives the "Only positions used by these activities (N)" label in
        the save-options dialog.
        """
        return len(self._referenced_positions(self._model.all_records()))

    @Slot(QUrl, result=int)
    def template_position_count(self, file_url: QUrl) -> int:
        """Count of stage positions bundled in a template file (0 if none).

        A cheap peek used by the load flow to decide whether to offer the
        "Load stage positions" choice at all. Any read/parse problem
        returns 0 — the real load path surfaces a proper error.
        """
        path = file_url.toLocalFile()
        if not path:
            return 0
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError):
            return 0
        positions, _ = template_io.parse_stage_positions(data)
        return len(positions)

    # --- Internal helpers --------------------------------------------------

    def _positions_to_bundle(
        self,
        include_positions: bool,
        scope: str,
        records: List[ActivityRecord],
    ) -> Optional[List[SavedStagePosition]]:
        """Resolve which positions a save should bundle.

        Returns ``None`` (omit the key entirely) when positions aren't
        included; otherwise the list selected by ``scope``. An unknown
        scope falls back to 'referenced' — the safer, minimal choice.
        """
        if not include_positions or self._positions is None:
            return None
        if scope == template_io.POSITION_SCOPE_ALL:
            return list(self._positions.all_saved_positions())
        return self._referenced_positions(records)

    def _referenced_positions(
        self, records: List[ActivityRecord],
    ) -> List[SavedStagePosition]:
        """The saved positions the position-based activities point at.

        Covers both GIS Deposition and Sputter Coat (NSR sputter coats
        at a saved position). De-duplicated and order-preserving. An
        activity with no position selected contributes nothing; a stale
        ``position_id`` that no longer resolves is skipped (it can't be
        bundled, and it already surfaces at workflow start).
        """
        if self._positions is None:
            return []
        seen = set()
        out: List[SavedStagePosition] = []
        for rec in records:
            if not isinstance(rec, (GISDepositionRecord, SputterCoatRecord)):
                continue
            pid = rec.position_id
            if not pid or pid in seen:
                continue
            saved = self._positions.get_record_by_id(pid)
            if saved is not None:
                seen.add(pid)
                out.append(saved)
        return out

    def _update_record(
        self,
        instance_id: str,
        expected_type: type,
        **changes: object,
    ) -> bool:
        """Find a record by instance id and apply changes."""
        index = self._model.find_index_by_instance_id(instance_id)
        if index < 0:
            logger.warning(
                "Cryo activities: no record with instance_id=%s; "
                "ignoring update", instance_id,
            )
            return False

        rec = self._model.record_at(index)
        if not isinstance(rec, expected_type):
            logger.error(
                "Cryo activities: setter type mismatch (instance=%s, "
                "expected=%s, got=%s); ignoring update",
                instance_id,
                expected_type.__name__,
                type(rec).__name__,
            )
            return False

        new_rec = rec.with_changes(**changes)
        ok = self._model.replace_at(index, new_rec)
        if ok:
            self._save()
        return ok

    def _build_default_records(self) -> List[ActivityRecord]:
        """Construct the default activity list."""
        records: List[ActivityRecord] = []
        for activity_type, params in defaults.DEFAULT_CRYO_ACTIVITY_LIST:
            try:
                rec = make_default_record(activity_type)
            except ValueError:
                logger.warning(
                    "Cryo defaults: unknown activity_type %r; skipping",
                    activity_type,
                    exc_info=True,
                )
                continue
            if params:
                rec = rec.with_changes(**params)
            records.append(rec)
        return records

    # --- Persistence -------------------------------------------------------

    def _load_or_seed(self) -> None:
        """Load the activity list from QSettings, or seed with defaults."""
        raw = self._qs.value(_K_ACTIVITY_LIST)
        if raw is None:
            logger.info(
                "Cryo activities: first launch — seeding with defaults"
            )
            self._seed_with_defaults()
            return

        try:
            data = json.loads(str(raw))
        except (TypeError, ValueError):
            logger.warning(
                "Cryo activities: could not parse saved list (got %r); "
                "seeding with defaults",
                raw,
                exc_info=True,
            )
            self._seed_with_defaults()
            return

        if not isinstance(data, list):
            logger.warning(
                "Cryo activities: saved list is not an array (got %s); "
                "seeding with defaults",
                type(data).__name__,
            )
            self._seed_with_defaults()
            return

        records: List[ActivityRecord] = []
        for item in data:
            if not isinstance(item, dict):
                logger.warning(
                    "Cryo activities: skipping non-dict entry %r", item
                )
                continue
            try:
                records.append(record_from_dict(item))
            except (TypeError, ValueError):
                logger.warning(
                    "Cryo activities: could not parse entry %r; skipping",
                    item,
                    exc_info=True,
                )

        if not records:
            logger.warning(
                "Cryo activities: saved list yielded no valid records; "
                "seeding with defaults"
            )
            self._seed_with_defaults()
            return

        self._model.replace_all(records)
        # Self-heal: if any corrupt entries were skipped above, persist the
        # cleaned list once so QSettings stops carrying (and re-skipping)
        # them on every launch. Mirrors the replace_all + _save pairing in
        # _seed_with_defaults and load_template.
        if len(records) < len(data):
            logger.info(
                "Cryo activities: dropping %d unparseable entr%s; "
                "re-persisting cleaned list",
                len(data) - len(records),
                "y" if len(data) - len(records) == 1 else "ies",
            )
            self._save()
        logger.info(
            "Cryo activities: loaded %d activit%s from settings",
            len(records),
            "y" if len(records) == 1 else "ies",
        )

    def _save(self) -> None:
        """Persist the current activity list to QSettings."""
        records = self._model.all_records()
        try:
            payload = json.dumps([r.to_dict() for r in records])
        except (TypeError, ValueError):
            logger.exception(
                "Cryo activities: could not serialize list (this is a bug)"
            )
            return
        self._qs.setValue(_K_ACTIVITY_LIST, payload)