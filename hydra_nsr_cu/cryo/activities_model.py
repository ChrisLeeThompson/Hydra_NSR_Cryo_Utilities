"""``QAbstractListModel`` for Cryo activities.

Holds a sequence of :class:`ActivityRecord` instances (Sputter Coat or
GIS Deposition). Each row exposes per-activity-type-specific fields as
roles, so QML delegates can bind directly to ``model.duration``,
``model.sputterPositionId``, etc.

Drag-and-drop reorder is supported via :meth:`move`. The QML side
already implements the visual drag mechanics; it just calls
``model.move(from, to)`` at drop time.

QML interaction
---------------
The model exposes:

* Standard ``QAbstractListModel`` machinery — delegate consumption via
  custom roles. Role names are designed to match what the existing
  QML expects (``activityType``, ``title``, ``instanceId``,
  ``parameterSummary``), supplemented with per-parameter roles.
* :attr:`count` Property for QML bindings that need to know how many
  activities are present.
* :meth:`get(int)` returning a JS dict, matching the convention from
  :class:`StagePositionsModel`.
* :meth:`find_index_by_instance_id` for translating instance ids to
  indices.

Mutation methods (:meth:`append_record`, :meth:`replace_at`,
:meth:`remove_at`, :meth:`move`, :meth:`replace_all`) are intended to
be called from the controller. QML drives the controller, which in
turn drives the model.

Per-activity-type roles
-----------------------
Both activity types share ``InstanceIdRole`` and ``ActivityTypeRole``.
Beyond that, the role set is the union of all type-specific
parameters. For a row of one type, roles defined on the *other* type
return type-correct defaults (0 for ints, 0.0 for floats, "" for
strings) so QML's ``required property`` declarations don't trip on
the irrelevant roles.

This "wide model" approach keeps the model class simple at the cost
of some role declarations that aren't always meaningful. The
alternative (separate models per activity type, or per-row dynamic
roles) would complicate the QML side without commensurate benefit
for the small number of activity types we expect to ever support.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from PySide6.QtCore import (
    Property,
    QAbstractListModel,
    QByteArray,
    QModelIndex,
    Qt,
    Signal,
    Slot,
)

from .activity_records import (
    ACTIVITY_TYPE_GIS_DEPOSITION,
    ACTIVITY_TYPE_SPUTTER_COAT,
    ActivityRecord,
    GISDepositionRecord,
    SputterCoatRecord,
)

logger = logging.getLogger(__name__)


# Display titles for the activity types. The QML delegate currently
# reads ``model.title`` to populate the ActivityContainer header. Kept
# here so the model can return them as a TitleRole — the alternative
# would be to derive the title in QML from activityType, but mapping
# strings to display labels is exactly what the role system is for.
_ACTIVITY_TYPE_TITLES = {
    ACTIVITY_TYPE_SPUTTER_COAT: "Sputter Coat",
    ACTIVITY_TYPE_GIS_DEPOSITION: "GIS Deposition",
}


class CryoActivitiesModel(QAbstractListModel):
    """List model for Cryo activity records."""

    # --- Role enum ---
    #
    # Starting at Qt.UserRole keeps these clear of standard Qt roles.
    # The role byte-strings (returned by roleNames) are the property
    # names QML delegates use against the model.

    # Universal roles — meaningful for every row.
    InstanceIdRole = Qt.UserRole + 1
    ActivityTypeRole = Qt.UserRole + 2
    TitleRole = Qt.UserRole + 3

    # Sputter Coat parameters. (Role +10 was the removed Hydra NSR
    # grid index; the NSR sputter is position-based, so +15 carries the
    # new position id instead. High voltage is fixed, not a role.)
    IonSpeciesIndexRole = Qt.UserRole + 11
    IonCurrentARole = Qt.UserRole + 12
    SputterDurationRole = Qt.UserRole + 13
    SputterChamberRecoveryRole = Qt.UserRole + 14
    SputterPositionIdRole = Qt.UserRole + 15

    # GIS Deposition parameters.
    PositionIdRole = Qt.UserRole + 20
    GISDurationRole = Qt.UserRole + 21
    GISChamberRecoveryRole = Qt.UserRole + 22

    countChanged = Signal()

    def __init__(self, parent: Optional[Any] = None) -> None:
        super().__init__(parent)
        self._records: List[ActivityRecord] = []

    # --- QAbstractListModel interface --------------------------------------

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        if parent.isValid():
            return 0
        return len(self._records)

    def data(self, index: QModelIndex, role: int = Qt.DisplayRole) -> Any:
        if not index.isValid():
            return None
        row = index.row()
        if not (0 <= row < len(self._records)):
            return None

        rec = self._records[row]

        # Universal roles
        if role == self.InstanceIdRole:
            return rec.instance_id
        if role == self.ActivityTypeRole:
            return rec.activity_type
        if role == self.TitleRole:
            return _ACTIVITY_TYPE_TITLES.get(rec.activity_type, "")

        # Type-specific roles. We return type-correct defaults for
        # roles that don't apply to this row's activity type — 0 for
        # ints, 0.0 for floats, "" for strings — rather than None /
        # undefined. This lets the QML delegate declare these as
        # `required property int` / `required property real` /
        # `required property string` without the unused-role values
        # tripping the type check on rows of the "wrong" type.
        if isinstance(rec, SputterCoatRecord):
            if role == self.SputterPositionIdRole:
                return rec.position_id
            if role == self.IonSpeciesIndexRole:
                return rec.ion_species_index
            if role == self.IonCurrentARole:
                return rec.ion_current_a
            if role == self.SputterDurationRole:
                return rec.duration_s
            if role == self.SputterChamberRecoveryRole:
                return rec.chamber_recovery_s
            # Roles that belong to GIS Deposition — return defaults.
            if role == self.PositionIdRole:
                return ""
            if role == self.GISDurationRole:
                return 0
            if role == self.GISChamberRecoveryRole:
                return 0
        elif isinstance(rec, GISDepositionRecord):
            if role == self.PositionIdRole:
                return rec.position_id
            if role == self.GISDurationRole:
                return rec.duration_s
            if role == self.GISChamberRecoveryRole:
                return rec.chamber_recovery_s
            # Roles that belong to Sputter Coat — return defaults.
            if role == self.SputterPositionIdRole:
                return ""
            if role == self.IonSpeciesIndexRole:
                return 0
            if role == self.IonCurrentARole:
                return 0.0
            if role == self.SputterDurationRole:
                return 0
            if role == self.SputterChamberRecoveryRole:
                return 0

        return None

    def roleNames(self) -> Dict[int, QByteArray]:
        # Names are chosen to match what QML expects. The activityType /
        # title roles match the existing delegate's `required property`
        # declarations; per-parameter roles use camelCase to match QML
        # convention for property names on model rows.
        return {
            self.InstanceIdRole: QByteArray(b"instanceId"),
            self.ActivityTypeRole: QByteArray(b"activityType"),
            self.TitleRole: QByteArray(b"title"),

            self.SputterPositionIdRole: QByteArray(b"sputterPositionId"),
            self.IonSpeciesIndexRole: QByteArray(b"ionSpeciesIndex"),
            self.IonCurrentARole: QByteArray(b"ionCurrentA"),
            self.SputterDurationRole: QByteArray(b"sputterDuration"),
            self.SputterChamberRecoveryRole: QByteArray(b"sputterChamberRecovery"),

            self.PositionIdRole: QByteArray(b"positionId"),
            self.GISDurationRole: QByteArray(b"gisDuration"),
            self.GISChamberRecoveryRole: QByteArray(b"gisChamberRecovery"),
        }

    # --- QML conveniences --------------------------------------------------

    @Property(int, notify=countChanged)
    def count(self) -> int:
        return len(self._records)

    @Slot(int, result="QVariant")
    def get(self, index: int) -> Dict[str, Any]:
        """Return a dict representation of the row at ``index``."""
        if not (0 <= index < len(self._records)):
            return {}
        return self._records[index].to_dict()

    @Slot(str, result=int)
    def find_index_by_instance_id(self, instance_id: str) -> int:
        """Return the row index of the activity with this instance id, or -1."""
        for i, rec in enumerate(self._records):
            if rec.instance_id == instance_id:
                return i
        return -1

    # --- Mutation (called by the controller, not directly by QML) ---------

    def replace_all(self, records: List[ActivityRecord]) -> None:
        """Replace the entire list. Used at startup and Restore Defaults."""
        self.beginResetModel()
        self._records = list(records)
        self.endResetModel()
        self.countChanged.emit()

    def append_record(self, record: ActivityRecord) -> None:
        """Append a new record. Used by Add Sputter / Add GIS."""
        row = len(self._records)
        self.beginInsertRows(QModelIndex(), row, row)
        self._records.append(record)
        self.endInsertRows()
        self.countChanged.emit()

    def replace_at(self, index: int, record: ActivityRecord) -> bool:
        """Replace the record at ``index``. Used when parameters change.

        Records are immutable — every parameter edit creates a new
        record with the change applied (via ``record.with_changes``)
        and replaces the row. This makes ``dataChanged`` emission
        precise: the diff between old and new tells us exactly which
        roles changed.
        """
        if not (0 <= index < len(self._records)):
            return False
        old = self._records[index]
        if type(old) is not type(record):
            logger.error(
                "replace_at: type mismatch (old=%s, new=%s); refusing",
                type(old).__name__, type(record).__name__,
            )
            return False
        self._records[index] = record
        # Emit dataChanged for all roles. We could compute the
        # minimum-changed set by comparing fields, but the cost of
        # over-emitting is negligible (QML re-evaluates a few bindings)
        # and the cost of under-emitting is a stale UI.
        model_index = self.index(index, 0)
        self.dataChanged.emit(
            model_index, model_index, list(self.roleNames().keys())
        )
        return True

    def remove_at(self, index: int) -> bool:
        """Remove the row at ``index``."""
        if not (0 <= index < len(self._records)):
            return False
        self.beginRemoveRows(QModelIndex(), index, index)
        del self._records[index]
        self.endRemoveRows()
        self.countChanged.emit()
        return True

    def move(self, from_index: int, to_index: int) -> bool:
        """Move the row at ``from_index`` to ``to_index``."""
        n = len(self._records)
        if not (0 <= from_index < n):
            return False
        if not (0 <= to_index < n):
            return False
        if from_index == to_index:
            return False

        # Qt's beginMoveRows uses a slightly different convention:
        # ``destinationChild`` is where the row would be inserted in
        # the source's coordinate system, which means moving down by
        # one position requires destinationChild = to_index + 1.
        if to_index > from_index:
            qt_destination = to_index + 1
        else:
            qt_destination = to_index

        self.beginMoveRows(
            QModelIndex(), from_index, from_index,
            QModelIndex(), qt_destination,
        )
        rec = self._records.pop(from_index)
        self._records.insert(to_index, rec)
        self.endMoveRows()
        return True

    def all_records(self) -> List[ActivityRecord]:
        """Snapshot of all records (for serialization)."""
        return list(self._records)

    def record_at(self, index: int) -> Optional[ActivityRecord]:
        """Direct access to a record (for the controller's edit path)."""
        if not (0 <= index < len(self._records)):
            return None
        return self._records[index]