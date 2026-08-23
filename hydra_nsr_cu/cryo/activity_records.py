"""JSON-serializable parameter records for Cryo activities.

Each activity type has a dedicated dataclass holding its parameters.
Records carry a stable :attr:`instance_id` (assigned at creation) so
status signals from the workflow runner can be routed to the matching
QML delegate row regardless of reordering.

Records are used by:

* :class:`CryoActivitiesModel` — held as the underlying row data, with
  per-field roles exposed to QML.
* :class:`CryoActivitiesController` — JSON-serialized for QSettings
  persistence and the Templates feature.
* :class:`CPWorkflow._build_activities` — to construct concrete
  :class:`ActivityService` instances at workflow start.

The two record types share an :attr:`activity_type` discriminator
(matching ``ActivityService.activity_id``) so the controller can
dispatch JSON-loaded dicts to the right ``from_dict`` constructor.
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import asdict, dataclass, field, replace
from typing import Any, Dict, Union

from .. import defaults

logger = logging.getLogger(__name__)


# Activity-type discriminators. Match the corresponding
# ActivityService.activity_id on the execution side. Re-declared here as
# constants rather than imported to keep this module independent of the
# activities subpackage — the records are pure data and don't need to
# import the services they describe.
ACTIVITY_TYPE_SPUTTER_COAT = "sputter_coat"
ACTIVITY_TYPE_GIS_DEPOSITION = "gis_deposition"


def _new_instance_id() -> str:
    """Stable, opaque, collision-resistant id for an activity instance.

    Matches the format used by :func:`stage_positions.positions_model._new_id` —
    8 hex chars from a UUID4. Long enough to be visually distinct in
    logs, short enough to be unobtrusive in serialized form.
    """
    return uuid.uuid4().hex[:8]


@dataclass
class SputterCoatRecord:
    """Parameters for a single Sputter Coat activity instance."""

    instance_id: str = field(default_factory=_new_instance_id)
    # Position id from the StagePositionsController. Empty string means
    # "no position selected" — the workflow refuses to start an activity
    # with an unselected position. Mirrors GISDepositionRecord — the NSR
    # sputter coater applies its pattern at a saved stage position.
    position_id: str = defaults.SPUTTER_COAT_POSITION_ID_DEFAULT
    ion_species_index: int = defaults.SPUTTER_ION_SPECIES_DEFAULT_INDEX
    # Beam current in amperes. Stored as the actual current value
    # rather than a UI-list index — the index would only be valid
    # against a specific species' UI list, and switching species
    # would invalidate it. Storing the value lets the activity send
    # the value directly to AutoScript, which snaps to the nearest
    # available current for the active species.
    ion_current_a: float = defaults.SPUTTER_ION_CURRENT_DEFAULT_A
    duration_s: int = defaults.SPUTTER_COAT_DURATION_DEFAULT_S
    chamber_recovery_s: int = defaults.SPUTTER_COAT_CHAMBER_RECOVERY_DEFAULT_S

    @property
    def activity_type(self) -> str:
        return ACTIVITY_TYPE_SPUTTER_COAT

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        # Discriminator goes in the serialized form so from_dict-on-a-dict
        # can dispatch to the right record type.
        d["activity_type"] = ACTIVITY_TYPE_SPUTTER_COAT
        return d

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "SputterCoatRecord":
        # Defensive — supply defaults for any missing fields so older
        # persisted data without a future field doesn't crash on load.
        # An old Hydra NSR record carrying ``grid_index`` is simply
        # ignored (not a constructor field), and a missing ``position_id``
        # falls back to its default.
        return cls(
            instance_id=str(d.get("instance_id") or _new_instance_id()),
            position_id=str(d.get(
                "position_id", defaults.SPUTTER_COAT_POSITION_ID_DEFAULT
            )),
            ion_species_index=int(d.get(
                "ion_species_index", defaults.SPUTTER_ION_SPECIES_DEFAULT_INDEX
            )),
            ion_current_a=float(d.get(
                "ion_current_a", defaults.SPUTTER_ION_CURRENT_DEFAULT_A
            )),
            duration_s=int(d.get(
                "duration_s", defaults.SPUTTER_COAT_DURATION_DEFAULT_S
            )),
            chamber_recovery_s=int(d.get(
                "chamber_recovery_s",
                defaults.SPUTTER_COAT_CHAMBER_RECOVERY_DEFAULT_S,
            )),
        )

    def with_changes(self, **kwargs: Any) -> "SputterCoatRecord":
        """Return a new record with the given fields changed.

        Records are nominally immutable — the model swaps in a new
        record on edit rather than mutating in place. ``instance_id``
        is preserved unless explicitly overridden.
        """
        return replace(self, **kwargs)


@dataclass
class GISDepositionRecord:
    """Parameters for a single GIS Deposition activity instance."""

    instance_id: str = field(default_factory=_new_instance_id)
    # Position id from the StagePositionsController. Empty string means
    # "no position selected" — the workflow refuses to start an
    # activity with an unselected position. Stale ids (referring to
    # deleted positions) are detected at workflow Start, not eagerly.
    position_id: str = defaults.GIS_DEPOSITION_POSITION_ID_DEFAULT
    duration_s: int = defaults.GIS_DEPOSITION_DURATION_DEFAULT_S
    chamber_recovery_s: int = defaults.GIS_DEPOSITION_CHAMBER_RECOVERY_DEFAULT_S

    @property
    def activity_type(self) -> str:
        return ACTIVITY_TYPE_GIS_DEPOSITION

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["activity_type"] = ACTIVITY_TYPE_GIS_DEPOSITION
        return d

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "GISDepositionRecord":
        return cls(
            instance_id=str(d.get("instance_id") or _new_instance_id()),
            position_id=str(d.get(
                "position_id", defaults.GIS_DEPOSITION_POSITION_ID_DEFAULT
            )),
            duration_s=int(d.get(
                "duration_s", defaults.GIS_DEPOSITION_DURATION_DEFAULT_S
            )),
            chamber_recovery_s=int(d.get(
                "chamber_recovery_s",
                defaults.GIS_DEPOSITION_CHAMBER_RECOVERY_DEFAULT_S,
            )),
        )

    def with_changes(self, **kwargs: Any) -> "GISDepositionRecord":
        return replace(self, **kwargs)


# --- Type alias and dispatch ----------------------------------------------

# A row in the activities model is one of these two record types.
ActivityRecord = Union[SputterCoatRecord, GISDepositionRecord]


def record_from_dict(d: Dict[str, Any]) -> ActivityRecord:
    """Construct the appropriate record subtype from a serialized dict.

    Dispatches on the ``activity_type`` discriminator. Raises
    :class:`ValueError` for unknown types — defensive against
    partially-corrupted or future-version persisted state.
    """
    activity_type = d.get("activity_type")
    if activity_type == ACTIVITY_TYPE_SPUTTER_COAT:
        return SputterCoatRecord.from_dict(d)
    if activity_type == ACTIVITY_TYPE_GIS_DEPOSITION:
        return GISDepositionRecord.from_dict(d)
    raise ValueError(
        f"Unknown activity_type {activity_type!r} in record dict; "
        f"cannot deserialize"
    )


def make_default_record(activity_type: str) -> ActivityRecord:
    """Construct a fresh record of the given type with all defaults.

    Used when the user clicks Add Sputter / Add GIS — the new record
    gets a fresh instance_id and all parameters at factory-default
    values.
    """
    if activity_type == ACTIVITY_TYPE_SPUTTER_COAT:
        return SputterCoatRecord()
    if activity_type == ACTIVITY_TYPE_GIS_DEPOSITION:
        return GISDepositionRecord()
    raise ValueError(
        f"Unknown activity_type {activity_type!r}; cannot make default record"
    )