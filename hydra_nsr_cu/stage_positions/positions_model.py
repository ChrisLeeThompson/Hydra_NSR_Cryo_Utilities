"""``QAbstractListModel`` for the saved stage positions.

The model holds a sequence of :class:`SavedStagePosition` records.
Each record has a stable :attr:`SavedStagePosition.id` that survives
reordering — referenced by activity parameters and templates that
need to point at a specific saved position rather than at an index.

Coordinate units are AutoScript SI units (meters for x/y/z, radians
for r/t). Conversion to display units (mm, degrees) happens in QML.

QML interaction
---------------
The model exposes:

* Standard ``QAbstractListModel`` machinery for delegate consumption
  via custom roles.
* ``count`` as a Qt property for the dialog's name-uniqueness check.
* ``get(int)`` returning a JavaScript dict — matches the convention
  the v3 QML expected (``positionsModel.get(i).name``).
* ``find_index_by_id(str)`` for translating ids to indices when the
  controller needs to update or remove a row by id.

Direct mutation methods (``add_record`` / ``update_record`` /
``rename_record`` / ``remove_at``) are intended to be called from
the controller; QML drives the controller, not the model.
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import asdict, dataclass, field, replace
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

logger = logging.getLogger(__name__)


@dataclass
class SavedStagePosition:
    """A saved stage position record.

    Coordinates are in AutoScript SI units (meters for x/y/z, radians
    for r/t). The ``id`` is a stable identifier assigned at creation
    time and persisted across sessions.
    """
    id: str
    name: str
    x: float = 0.0
    y: float = 0.0
    z: float = 0.0
    r: float = 0.0
    t: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "SavedStagePosition":
        # Defensive — supply defaults for any missing fields so older
        # persisted data without a future field doesn't crash on load.
        return cls(
            id=str(d.get("id") or _new_id()),
            name=str(d.get("name", "")),
            x=float(d.get("x", 0.0)),
            y=float(d.get("y", 0.0)),
            z=float(d.get("z", 0.0)),
            r=float(d.get("r", 0.0)),
            t=float(d.get("t", 0.0)),
        )


def _new_id() -> str:
    """Short, opaque, collision-resistant id.

    8 hex chars from a UUID4 gives ~4 billion possible values — vastly
    more than we'll ever generate in this app. Long enough to be
    visually distinct in logs, short enough to be unobtrusive in
    serialized form.
    """
    return uuid.uuid4().hex[:8]


class StagePositionsModel(QAbstractListModel):
    """List model for saved stage positions."""

    # Role enum values. Starting at Qt.UserRole keeps them clear of
    # the standard Qt roles. Role byte-strings (returned by roleNames)
    # are what QML delegates reference as property names.
    NameRole = Qt.UserRole + 1
    IdRole = Qt.UserRole + 2
    XRole = Qt.UserRole + 3
    YRole = Qt.UserRole + 4
    ZRole = Qt.UserRole + 5
    RRole = Qt.UserRole + 6
    TRole = Qt.UserRole + 7

    # Notify signal so QML's "count" binding refreshes when rows are
    # added or removed. QAbstractListModel emits rowsInserted/
    # rowsRemoved which already drives the listview, but a separate
    # countChanged is needed for the @Property notification contract.
    countChanged = Signal()

    def __init__(self, parent: Optional[Any] = None) -> None:
        super().__init__(parent)
        self._records: List[SavedStagePosition] = []

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
        if role == self.NameRole:
            return rec.name
        if role == self.IdRole:
            return rec.id
        if role == self.XRole:
            return rec.x
        if role == self.YRole:
            return rec.y
        if role == self.ZRole:
            return rec.z
        if role == self.RRole:
            return rec.r
        if role == self.TRole:
            return rec.t
        return None

    def roleNames(self) -> Dict[int, QByteArray]:
        # Delegate properties: model.name, model.id, model.posX, ...
        # Naming `posX` matches the v3 QML which used those names
        # against the placeholder ListModel. Keeping consistent names
        # avoids a QML rewrite of the delegate.
        return {
            self.NameRole: QByteArray(b"name"),
            self.IdRole: QByteArray(b"id"),
            self.XRole: QByteArray(b"posX"),
            self.YRole: QByteArray(b"posY"),
            self.ZRole: QByteArray(b"posZ"),
            self.RRole: QByteArray(b"posR"),
            self.TRole: QByteArray(b"posT"),
        }

    # --- QML conveniences --------------------------------------------------

    @Property(int, notify=countChanged)
    def count(self) -> int:
        return len(self._records)

    @Slot(int, result="QVariant")
    def get(self, index: int) -> Dict[str, Any]:
        """Return a dict representation of the row at ``index``.

        Matches the v3 QML's expectation
        (``positionsModel.get(i).name``). Returns an empty dict on
        out-of-range index — caller handles that by falling back.
        """
        if not (0 <= index < len(self._records)):
            return {}
        return self._records[index].to_dict()

    @Slot(str, result=int)
    def find_index_by_id(self, position_id: str) -> int:
        """Return the row index of the position with this id, or -1."""
        for i, rec in enumerate(self._records):
            if rec.id == position_id:
                return i
        return -1
    
    @Slot(str, result=int)
    def find_index_by_name(self, name: str) -> int:
        """Return the row index of the position with this name, or -1.

        Names are constrained unique by the controller (``add_from_current``
        and ``rename`` reject collisions) as well as the Add/Edit dialogs,
        so the first match (if any) is the only match.
        """
        for i, rec in enumerate(self._records):
            if rec.name == name:
                return i
        return -1

    # --- Mutation (called by the controller, not directly by QML) ---------

    def replace_all(self, records: List[SavedStagePosition]) -> None:
        """Replace the entire list. Used at startup when loading from JSON."""
        self.beginResetModel()
        self._records = list(records)
        self.endResetModel()
        self.countChanged.emit()

    def add_record(self, record: SavedStagePosition) -> None:
        """Append a new record."""
        row = len(self._records)
        self.beginInsertRows(QModelIndex(), row, row)
        self._records.append(record)
        self.endInsertRows()
        self.countChanged.emit()

    def update_coordinates(
        self,
        index: int,
        x: float, y: float, z: float, r: float, t: float,
    ) -> bool:
        """Update the coordinates of the row at ``index`` (name unchanged)."""
        if not (0 <= index < len(self._records)):
            return False
        old = self._records[index]
        self._records[index] = replace(old, x=x, y=y, z=z, r=r, t=t)
        # All coordinate roles change at once; emit dataChanged for the
        # full role list so any binding refreshes.
        model_index = self.index(index, 0)
        self.dataChanged.emit(
            model_index,
            model_index,
            [self.XRole, self.YRole, self.ZRole, self.RRole, self.TRole],
        )
        return True

    def rename(self, index: int, new_name: str) -> bool:
        """Rename the row at ``index``."""
        if not (0 <= index < len(self._records)):
            return False
        old = self._records[index]
        if old.name == new_name:
            return False
        self._records[index] = replace(old, name=new_name)
        model_index = self.index(index, 0)
        self.dataChanged.emit(model_index, model_index, [self.NameRole])
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

    def all_records(self) -> List[SavedStagePosition]:
        """Snapshot of all records (for serialization)."""
        return list(self._records)