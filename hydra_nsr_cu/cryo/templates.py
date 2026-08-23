"""Template-file (JSON) <-> activity-record translation.

This module is the cross-machine portability boundary for Cryo Prep
templates. Records hold a ``position_id`` that is local to one
machine's QSettings; templates store ``position_name`` because names
are unique (enforced by the Add/Edit dialogs in
:mod:`stage_positions`) and meaningful across machines.

``instance_id`` is stripped on save (templates are blueprints, not
instances; ids are minted fresh on load). All functions here are pure
— file I/O lives in :class:`CryoActivitiesController` — and the
positions dependency is the :class:`PositionsLookup` Protocol rather
than the controller itself, so the module is testable without Qt.

Validation policy
-----------------
Two-tier:

* **Structural failures** (not a JSON object, schema version
  mismatch, ``cryo_prep_page_activities`` missing or not a list)
  produce a :class:`LoadResult` with ``records=None`` and an
  ``error_message``; the controller leaves the existing list
  untouched.
* **Data failures** are repaired and recorded in the ``issues`` list
  the controller surfaces in the load summary: missing field →
  silent default (normal forward-compat); wrong-type field → default
  + issue; out-of-range numeric → clamp + issue; unmatched
  ``position_name`` → empty ``position_id`` + issue (an empty or
  missing name is a normal "no selection", not an issue); unknown
  ``activity_type`` → entry dropped + issue.

A missing ``schema_version`` is treated as v1 (lenient); a
present-but-mismatched one is rejected (strict).
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field as dc_field
from datetime import datetime, timezone
from typing import (
    Any,
    Dict,
    Iterable,
    List,
    Optional,
    Protocol,
    Tuple,
    runtime_checkable,
)

from .. import defaults
from ..stage_positions.positions_model import SavedStagePosition
from .activity_records import (
    ACTIVITY_TYPE_GIS_DEPOSITION,
    ACTIVITY_TYPE_SPUTTER_COAT,
    ActivityRecord,
    GISDepositionRecord,
    SputterCoatRecord,
)

logger = logging.getLogger(__name__)


# --- Schema constants ---------------------------------------------------

SCHEMA_VERSION = 1
"""Current template schema version. Bump on any breaking format change.

Loaders accept files where this field is present and equal, and also
accept files where the field is absent (treated as v1, for tolerance
of hand-edited or pre-versioned files). Files with a present-but-
different value are rejected.
"""

ACTIVITIES_FIELD = "cryo_prep_page_activities"
"""Top-level JSON key under which the activity list lives.

The name is specific so that if RT Prep templates ever ship, they can
live in their own files alongside without ambiguity.
"""

STAGE_POSITIONS_FIELD = "stage_positions"
"""Optional top-level JSON key under which bundled stage positions live.

A list of ``{name, x, y, z, r, t}`` dicts — coordinates in AutoScript
SI units, no ``id`` (ids are machine-local and minted fresh on import,
mirroring how activity ``instance_id`` is stripped on save). Absent on
templates saved without positions, and on every template predating this
feature; the loader treats a missing key as "no bundled positions", so
old and new readers agree on what an activities-only template looks
like.
"""

POSITION_SCOPE_REFERENCED = "referenced"
"""Save only the positions the activities point at — the minimal,
self-contained set that makes the workflow runnable on another machine."""

POSITION_SCOPE_ALL = "all"
"""Save the entire saved-positions library, referenced or not."""


# --- PositionsLookup Protocol -------------------------------------------

@runtime_checkable
class PositionsLookup(Protocol):
    """The slice of :class:`StagePositionsController` templates need.

    The real controller satisfies this naturally. The two ``get_*``
    methods are the read side used by the pure functions in this module
    (save translates id->name; load resolves name->id). The two
    list-oriented methods are used by the controller layer:
    :meth:`all_saved_positions` enumerates the library for an
    "all positions" save, and :meth:`import_positions` non-destructively
    merges bundled positions on load. Keeping all four on one Protocol
    means a single object — or a single in-memory test fake — satisfies
    every template need.
    """

    def get_record_by_id(
        self, position_id: str,
    ) -> Optional[SavedStagePosition]:
        ...

    def get_record_by_name(
        self, name: str,
    ) -> Optional[SavedStagePosition]:
        ...

    def all_saved_positions(self) -> List[SavedStagePosition]:
        ...

    def import_positions(
        self, positions: List[SavedStagePosition],
    ) -> Tuple[Dict[str, str], List[str]]:
        ...


# --- LoadResult ---------------------------------------------------------

@dataclass
class LoadResult:
    """Outcome of parsing a template payload.

    Three possible shapes:

    * **Clean success** — ``records`` is a non-None list, ``issues``
      is empty, ``error_message`` is None.
    * **Adjusted success** — ``records`` is a non-None list,
      ``issues`` is non-empty, ``error_message`` is None.
    * **Structural failure** — ``records`` is None, ``issues`` is
      empty, ``error_message`` describes the problem.

    ``template_name`` is filled in whenever it can be extracted from
    the payload (even on failure, when the top-level ``name`` field
    was present), so the UI can refer to "the file you tried to load"
    by its declared name rather than by file path.
    """
    records: Optional[List[ActivityRecord]] = None
    issues: List[str] = dc_field(default_factory=list)
    error_message: Optional[str] = None
    template_name: str = ""

    @property
    def succeeded(self) -> bool:
        """True if structural checks passed and ``records`` is non-None."""
        return self.records is not None


# --- Payload build (save direction) ------------------------------------

def build_template_payload(
    records: Iterable[ActivityRecord],
    positions: PositionsLookup,
    *,
    name: str,
    description: str = "",
    stage_positions: Optional[Iterable[SavedStagePosition]] = None,
) -> Dict[str, Any]:
    """Build the full template-file payload ready for JSON serialization.

    The returned dict is what callers pass to ``json.dump``. The
    schema version, ISO8601 UTC timestamp, and field-key naming are
    set here so callers don't need to know the schema details.

    The ``date_created`` value is generated at call time from
    ``datetime.now(timezone.utc)`` with second precision — sub-second
    precision is not useful for template metadata, and trimming it
    keeps the rendered JSON clean.

    When ``stage_positions`` is provided (the "Include stage positions"
    save option), each is serialized under :data:`STAGE_POSITIONS_FIELD`
    via :func:`template_dict_from_position` — name + coordinates, with
    the machine-local ``id`` stripped, the same blueprint philosophy
    that strips ``instance_id`` from activities. When it is ``None`` the
    key is omitted entirely, which is exactly how a pre-feature
    activities-only template looks, so old and new readers agree.
    """
    payload: Dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "name": name,
        "description": description,
        "date_created": datetime.now(timezone.utc).isoformat(
            timespec="seconds",
        ),
        ACTIVITIES_FIELD: [
            template_dict_from_record(rec, positions) for rec in records
        ],
    }
    if stage_positions is not None:
        payload[STAGE_POSITIONS_FIELD] = [
            template_dict_from_position(pos) for pos in stage_positions
        ]
    return payload


# --- Payload parse (load direction) ------------------------------------

def parse_template_payload(
    data: Any,
    positions: PositionsLookup,
    *,
    position_name_remap: Optional[Dict[str, str]] = None,
) -> LoadResult:
    """Validate and parse a parsed-JSON template payload.

    Performs the structural-validation tier (top-level shape, schema
    version) and dispatches each activity entry to
    :func:`record_from_template_dict` for the data-validation tier.

    An empty ``cryo_prep_page_activities`` list is a clean success
    with zero records — an empty workflow is a valid (if useless)
    state. Likewise, all-entries-rejected is an adjusted success with
    zero records and issues populated; only top-level shape problems
    fail.

    ``position_name_remap`` maps a template position *name* to a local
    position *id*. The controller builds it from
    :meth:`PositionsLookup.import_positions` after merging bundled
    positions, so a GIS activity binds to the freshly-imported copy
    (possibly renamed on a name conflict) rather than to a same-named
    position that already happened to exist on this machine. Names not
    in the map fall back to the library lookup — exactly the behavior
    when no positions were bundled.
    """
    if not isinstance(data, dict):
        return LoadResult(
            error_message="Template file is not a JSON object.",
        )

    template_name = str(data.get("name", ""))

    schema_version = data.get("schema_version")
    # A missing version is treated as the current schema (lenient — see
    # module docstring). A present version must be an int that equals
    # SCHEMA_VERSION. The explicit type/bool check is load-bearing: Python
    # equality would otherwise accept `1.0 == 1` and `True == 1` as valid
    # and reject the numeric string `"1"` with a confusing message.
    if schema_version is not None and (
        not isinstance(schema_version, int)
        or isinstance(schema_version, bool)
        or schema_version != SCHEMA_VERSION
    ):
        return LoadResult(
            error_message=(
                f"Template schema version {schema_version!r} is not "
                f"supported (expected {SCHEMA_VERSION})."
            ),
            template_name=template_name,
        )

    activities = data.get(ACTIVITIES_FIELD)
    if not isinstance(activities, list):
        return LoadResult(
            error_message=(
                f"Template is missing the {ACTIVITIES_FIELD!r} field, "
                f"or it is not a list."
            ),
            template_name=template_name,
        )

    issues: List[str] = []
    records: List[ActivityRecord] = []
    for entry in activities:
        rec = record_from_template_dict(
            entry, positions, issues, position_name_remap,
        )
        if rec is not None:
            records.append(rec)

    return LoadResult(
        records=records,
        issues=issues,
        template_name=template_name,
    )


def parse_stage_positions(
    data: Any,
) -> Tuple[List[SavedStagePosition], List[str]]:
    """Parse the optional bundled stage positions from a template payload.

    Pure and side-effect-free — it only *reads* the payload into
    :class:`SavedStagePosition` records (with fresh ids); the actual
    merge into the live model is the controller's job via
    :meth:`PositionsLookup.import_positions`. Returns ``([], [])`` when
    the key is absent (a normal activities-only template), which the
    controller treats as "nothing to import".

    Tolerant, mirroring the activity loader: a non-list value, a
    non-object entry, or an entry with no usable name is skipped with
    an issue rather than failing the load. Any ``id`` present in the
    file is discarded so an imported position can never collide with a
    local id.
    """
    issues: List[str] = []
    if not isinstance(data, dict):
        return [], issues
    raw = data.get(STAGE_POSITIONS_FIELD)
    if raw is None:
        return [], issues
    if not isinstance(raw, list):
        issues.append(
            f"{STAGE_POSITIONS_FIELD!r} is not a list; no positions "
            f"imported."
        )
        return [], issues

    positions: List[SavedStagePosition] = []
    for entry in raw:
        if not isinstance(entry, dict):
            issues.append(f"Skipped non-object stage position: {entry!r}")
            continue
        name = str(entry.get("name", "")).strip()
        if not name:
            issues.append("Skipped a bundled stage position with no name.")
            continue
        # Strip any id from the file so from_dict mints a fresh one —
        # ids are machine-local and must not survive the transfer.
        clean = {k: v for k, v in entry.items() if k != "id"}
        clean["name"] = name
        positions.append(SavedStagePosition.from_dict(clean))
    return positions, issues


# --- Per-entry save (template_dict_from_record) -------------------------

def template_dict_from_record(
    record: ActivityRecord,
    positions: PositionsLookup,
) -> Dict[str, Any]:
    """Convert a record into a template-file dict.

    Differs from ``record.to_dict()`` in two ways:

    1. ``instance_id`` is omitted. Templates are blueprints; loading
       mints a fresh instance id for each entry.
    2. For position-based records (GIS Deposition and Sputter Coat),
       ``position_id`` is replaced by ``position_name`` resolved through
       :meth:`PositionsLookup.get_record_by_id`. If the id no longer
       resolves (the position was deleted after the activity
       referenced it) or is empty (no selection), ``position_name``
       is the empty string.

    All other fields pass through unchanged from ``to_dict()`` — same
    keys, same value types, same units.
    """
    d = record.to_dict()
    d.pop("instance_id", None)
    if isinstance(record, (GISDepositionRecord, SputterCoatRecord)):
        position_id = d.pop("position_id", "")
        position_name = ""
        if position_id:
            saved = positions.get_record_by_id(position_id)
            if saved is not None:
                position_name = saved.name
        d["position_name"] = position_name
    return d


def template_dict_from_position(
    position: SavedStagePosition,
) -> Dict[str, Any]:
    """Convert a saved position into a template-file dict.

    Stores name + coordinates only; the machine-local ``id`` is
    dropped because ids mean nothing on another machine and are minted
    fresh on import. Coordinates stay in AutoScript SI units (meters,
    radians) — the same units the runtime and QSettings use, so a
    round-trip through a template is lossless.
    """
    return {
        "name": position.name,
        "x": position.x,
        "y": position.y,
        "z": position.z,
        "r": position.r,
        "t": position.t,
    }


# --- Per-entry load (record_from_template_dict) ------------------------

def record_from_template_dict(
    d: Any,
    positions: PositionsLookup,
    issues: List[str],
    position_name_remap: Optional[Dict[str, str]] = None,
) -> Optional[ActivityRecord]:
    """Construct a fresh activity record from a template-file entry.

    The ``issues`` list is appended to with human-readable messages
    for each repair the loader applies. The caller surfaces the
    accumulated messages via the load-summary modal.

    ``position_name_remap`` (position-based activities — GIS Deposition
    and Sputter Coat) maps a template position name to a local id for
    the just-imported positions — see :func:`parse_template_payload`.

    Returns ``None`` when the entry cannot be salvaged: when it isn't
    a JSON object, or when its ``activity_type`` is missing or
    unrecognized. Defensive parsing means almost all other malformed
    cases produce a record with default values rather than ``None``.
    """
    if not isinstance(d, dict):
        issues.append(f"Skipped non-object entry: {d!r}")
        return None

    activity_type = d.get("activity_type")
    if activity_type == ACTIVITY_TYPE_SPUTTER_COAT:
        return _sputter_record_from_template_dict(
            d, positions, issues, position_name_remap,
        )
    if activity_type == ACTIVITY_TYPE_GIS_DEPOSITION:
        return _gis_record_from_template_dict(
            d, positions, issues, position_name_remap,
        )
    issues.append(
        f"Skipped activity with unknown type {activity_type!r}."
    )
    return None


# --- Per-type loaders ---------------------------------------------------

def _sputter_record_from_template_dict(
    d: Dict[str, Any],
    positions: PositionsLookup,
    issues: List[str],
    position_name_remap: Optional[Dict[str, str]] = None,
) -> SputterCoatRecord:
    """Build a fresh :class:`SputterCoatRecord` from a template entry."""
    position_id = _resolve_position_name(
        d, positions, issues, position_name_remap,
    )
    ion_species_index = _coerce_int(
        d, "ion_species_index",
        defaults.SPUTTER_ION_SPECIES_DEFAULT_INDEX, issues,
    )
    ion_species_index = _clamp(
        ion_species_index,
        0, len(defaults.PLASMA_GAS_SPECIES_NAMES) - 1,
        issues, "Sputter Coat ion_species_index",
    )
    ion_current_a = _coerce_float(
        d, "ion_current_a",
        defaults.SPUTTER_ION_CURRENT_DEFAULT_A, issues,
    )
    duration_s = _coerce_int(
        d, "duration_s",
        defaults.SPUTTER_COAT_DURATION_DEFAULT_S, issues,
    )
    duration_s = _clamp(
        duration_s,
        defaults.SPUTTER_DURATION_MIN_S,
        defaults.SPUTTER_DURATION_MAX_S,
        issues, "Sputter Coat duration_s",
    )
    chamber_recovery_s = _coerce_int(
        d, "chamber_recovery_s",
        defaults.SPUTTER_COAT_CHAMBER_RECOVERY_DEFAULT_S, issues,
    )
    chamber_recovery_s = _clamp(
        chamber_recovery_s,
        defaults.CHAMBER_RECOVERY_MIN_S,
        defaults.CHAMBER_RECOVERY_MAX_S,
        issues, "Sputter Coat chamber_recovery_s",
    )
    # instance_id intentionally omitted — the dataclass field's
    # default_factory mints a fresh id, which is the intended
    # behavior for template loads.
    return SputterCoatRecord(
        position_id=position_id,
        ion_species_index=ion_species_index,
        ion_current_a=ion_current_a,
        duration_s=duration_s,
        chamber_recovery_s=chamber_recovery_s,
    )


def _gis_record_from_template_dict(
    d: Dict[str, Any],
    positions: PositionsLookup,
    issues: List[str],
    position_name_remap: Optional[Dict[str, str]] = None,
) -> GISDepositionRecord:
    """Build a fresh :class:`GISDepositionRecord` from a template entry."""
    position_id = _resolve_position_name(
        d, positions, issues, position_name_remap,
    )
    duration_s = _coerce_int(
        d, "duration_s",
        defaults.GIS_DEPOSITION_DURATION_DEFAULT_S, issues,
    )
    duration_s = _clamp(
        duration_s,
        defaults.GIS_DEPOSITION_DURATION_MIN_S,
        defaults.GIS_DEPOSITION_DURATION_MAX_S,
        issues, "GIS Deposition duration_s",
    )
    chamber_recovery_s = _coerce_int(
        d, "chamber_recovery_s",
        defaults.GIS_DEPOSITION_CHAMBER_RECOVERY_DEFAULT_S, issues,
    )
    chamber_recovery_s = _clamp(
        chamber_recovery_s,
        defaults.CHAMBER_RECOVERY_MIN_S,
        defaults.CHAMBER_RECOVERY_MAX_S,
        issues, "GIS Deposition chamber_recovery_s",
    )
    # instance_id minted fresh by default_factory — see Sputter for
    # rationale.
    return GISDepositionRecord(
        position_id=position_id,
        duration_s=duration_s,
        chamber_recovery_s=chamber_recovery_s,
    )


# --- Coercion / clamping helpers ----------------------------------------

def _coerce_int(
    d: Dict[str, Any], key: str, default: int, issues: List[str],
) -> int:
    """Return ``d[key]`` as int; on type failure, default plus issue.

    Missing key is silent — uses ``default`` with no issue, which is
    the normal forward-compat case where the template predates a
    parameter added in a later version.
    """
    if key not in d:
        return default
    raw = d[key]
    # Reject bool explicitly: bool is a subclass of int, so int(True) == 1
    # would otherwise pass silently.
    if isinstance(raw, bool):
        issues.append(
            f"Could not parse {key!r} value {raw!r} (boolean not allowed); "
            f"using default {default}."
        )
        return default
    # Reject non-integral floats: int(30.9) truncates to 30 with no signal.
    if isinstance(raw, float) and not raw.is_integer():
        issues.append(
            f"Could not parse {key!r} value {raw!r} (non-integral); "
            f"using default {default}."
        )
        return default
    try:
        return int(raw)
    except (TypeError, ValueError):
        issues.append(
            f"Could not parse {key!r} value {raw!r}; "
            f"using default {default}."
        )
        return default


def _coerce_float(
    d: Dict[str, Any], key: str, default: float, issues: List[str],
) -> float:
    """Float counterpart to :func:`_coerce_int`."""
    if key not in d:
        return default
    raw = d[key]
    try:
        result = float(raw)
    except (TypeError, ValueError):
        issues.append(
            f"Could not parse {key!r} value {raw!r}; "
            f"using default {default}."
        )
        return default
    # Reject NaN / +-Inf: "nan"/"inf" parse cleanly through float() but a
    # non-finite value must never reach a hardware write (e.g. PFIB plasma
    # beam current). Fall back to the default with a recorded issue.
    if not math.isfinite(result):
        issues.append(
            f"{key!r} value {raw!r} is not finite; "
            f"using default {default}."
        )
        return default
    return result


def _clamp(
    value: int, lo: int, hi: int, issues: List[str], label: str,
) -> int:
    """Clamp ``value`` to ``[lo, hi]``; if clamped, append an issue."""
    if value < lo:
        issues.append(
            f"{label} value {value} clamped to minimum {lo}."
        )
        return lo
    if value > hi:
        issues.append(
            f"{label} value {value} clamped to maximum {hi}."
        )
        return hi
    return value


def _resolve_position_name(
    d: Dict[str, Any],
    positions: PositionsLookup,
    issues: List[str],
    position_name_remap: Optional[Dict[str, str]] = None,
) -> str:
    """Resolve the entry's ``position_name`` to a local ``position_id``.

    Empty or missing ``position_name`` is a normal "no position
    selected" state and produces no issue. A name that doesn't
    resolve to any saved position becomes an empty ``position_id``
    *and* produces an issue — the user needs to know that an
    activity arrived without its target position.

    When ``position_name_remap`` carries the name (the positions were
    bundled and imported this load), it wins over the library lookup so
    the activity binds to the template's own copy — even if a
    different, same-named position already exists locally.
    """
    raw = d.get("position_name", "")
    name = str(raw) if raw is not None else ""
    if not name:
        return ""
    if position_name_remap and name in position_name_remap:
        return position_name_remap[name]
    saved = positions.get_record_by_name(name)
    if saved is None:
        issues.append(
            f"GIS Deposition references position {name!r} not saved "
            f"on this machine; left unselected."
        )
        return ""
    return saved.id