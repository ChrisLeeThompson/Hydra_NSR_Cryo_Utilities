"""Application-wide default values and bounds.

Single source of truth for behavioral constants: the values the app
falls back to when no user-saved override exists, and the bounds used
both for clamping setters and for QML SpinBox ``from`` / ``to``
properties (exposed via controller Q_INVOKABLE getters).

Sections are organized by feature area. Settings and workflow modules
import from this module rather than holding their own ``defaults.py``
files — there are few enough values that one file is easier to edit
and to grep.

What's *not* here: pure UI / visual constants (margins, colors,
spacing, animation durations) live in ``qml_resources/Config/AppConfig.qml``.
"""
from __future__ import annotations

from typing import Any, Dict, List, Tuple


# =========================================================================
# Persistence schema namespace
# =========================================================================

# Prefix for every QSettings key the app persists (activity lists, saved
# stage positions, RT workflow params, user settings). Bump this on a
# breaking change to any persisted format to start from a clean namespace.
# Every module composes its keys from this constant rather than hardcoding
# the literal, so a single edit re-namespaces all persisted state at once.
SCHEMA_PREFIX: str = "v3/"


# =========================================================================
# Settings (user preferences — application-wide)
# =========================================================================

# --- Always On Top ---
ALWAYS_ON_TOP: bool = False

# --- GIS gas port name (used by GIS Purge activity) ---
GIS_GAS_PORT_NAME: str = "Pt dep Cryo"

# --- Sputter Coat settings (NSR pattern-based sputtering) ---
# The NSR sputter coater applies a saved FEI pattern application file
# (.ptf, located in the pattern_files/ directory at the repo root) via
# the PFIB + patterning subsystem, inserting the coater GIS port. These
# three values are user-editable on the Settings page and captured into
# the workflow settings snapshot at Start (see WorkflowSettingsSnapshot).
#   * SPUTTER_COAT_PATTERN_FILE  — file name (not path) of the .ptf to apply.
#   * SPUTTER_COAT_HFW_UM_DEFAULT — ion-beam horizontal field width (microns).
#   * SPUTTER_COAT_PORT_NAME      — the GIS port that acts as the sputter coater.
SPUTTER_COAT_PATTERN_FILE: str = "pattern_Sputtering.ptf"
SPUTTER_COAT_HFW_UM_DEFAULT: int = 1998
SPUTTER_COAT_HFW_UM_MIN: int = 1
SPUTTER_COAT_HFW_UM_MAX: int = 10000
SPUTTER_COAT_PORT_NAME: str = "µCoater"

# --- Sputter coat preset durations (seconds) ---
# Used by the Bulk and Lamella preset buttons in the SputterCoat activity.
# These are the user's last-saved choices; SettingsController exposes
# them as user-editable values in the Settings page. The constants here
# are the factory defaults that SettingsController initializes from.
BULK_SPUTTER_DURATION_S: int = 120
LAMELLA_SPUTTER_DURATION_S: int = 12

# --- Sputter duration bounds ---
# Shared between the Settings spin boxes and any workflow that uses
# sputter durations.
SPUTTER_DURATION_MIN_S: int = 1
SPUTTER_DURATION_MAX_S: int = 999

# --- Workflow completion behavior ---
# Used by the workflow runners for end-of-run state restoration.
#
# MOVE_STAGE_TO_ORIGINAL_POSITION drives the stage-position recorder:
# return the stage to where the session began, on a successful run.
#
# The two PFIB-restore toggles are independent axes (see
# PFIBConditionsRecorder): one restores the beam's electrical state
# (high voltage, beam current, and on/off), the other restores the
# ion species (plasma gas). Splitting lets the user, e.g., revert
# voltage/current for imaging while keeping the species that was
# switched in for sputter coating. Like the stage restore, both
# apply only after a fully successful run (see CPWorkflow._after_run).
MOVE_STAGE_TO_ORIGINAL_POSITION: bool = True
RESTORE_ORIGINAL_PFIB_VOLTAGE_CURRENT: bool = False
RESTORE_ORIGINAL_PFIB_ION_SPECIES: bool = False

# --- Stage move safety ---
# When True, every move the app makes to a saved or captured position
# (GIS Deposition's and Sputter Coat's targets, Move To, and Move Stage
# To Original Position) is a staged move: tilt to 0°, move X/Y/Z/R
# together, then tilt to the position's tilt. Rotation therefore
# happens flat and the tilt is applied only once the stage is at the
# target rotation; a single five-axis move gives no control over the
# order in which xT applies rotation and tilt. When the move also
# changes the holder (GIS Deposition, Sputter Coat, Move To), the stage
# tilts to 0° under the current holder and the position's holder is
# selected while the stage is flat. Costs up to two extra tilt moves
# per position, so it's a setting: workflows that hop between nearby
# positions (e.g. grid 1 -> grid 2) can leave it off. See
# microscope/stage_moves.py.
ZERO_TILT_BEFORE_STAGE_MOVES: bool = True

# Arrival check. xT clamps a move that leaves the selected holder's
# limits without raising an error (absolute_move returns and the stage
# stops short), so every stage of a move is read back. The axes listed
# in STAGE_MOVE_ARRIVAL_CHECK_AXES are compared with their targets; a
# miss (a shortfall) fails the move: GIS Deposition and Sputter Coat
# stop before the needle moves, Move To reports it, and the stage
# restore is reported as not restored. Rotation and tilt are the
# default: those are the axes the holder limits clamp, and their
# encoders read back exactly. X/Y/Z deviations are logged with every
# check but not enforced: on the instrument a Y miss of about 170 µm
# appeared after a tilt with nothing clamped, so a hard X/Y/Z check
# would stop good runs. Add an axis letter (x, y, z, r, t) once the
# logged deviations show it is stable. STAGE_MOVE_ARRIVAL_CHECK off:
# log only, never fail. The angle tolerance is in degrees because the
# UI steps tilt in 0.1°.
STAGE_MOVE_ARRIVAL_CHECK: bool = True
STAGE_MOVE_ARRIVAL_CHECK_AXES: str = "rt"
STAGE_MOVE_TOLERANCE_XYZ_M: float = 20e-6
STAGE_MOVE_TOLERANCE_ANGLE_DEG: float = 0.05

# --- Holders ---
# The xT holder (microscope.specimen.stage.holder) is the selected
# specimen holder, chosen by the operator in Sample Exchange. It governs
# which stage positions Microscope Control permits: with the AutoGrid
# shuttle holder selected, xT refuses moves to some positions that the
# standard shuttle holder allows, and the reverse.
#
# A saved stage position records the holder selected when it was saved
# (or last updated). GIS Deposition, Sputter Coat and Move To select
# that holder before moving there; a position with no recorded holder
# is reached under the holder already selected. Home Stage selects a
# fixed holder (HOME_STAGE_HOLDER). A Cryo Prep run remembers the
# holder selected at Start (the Start holder): GIS Deposition and
# Sputter Coat reach an untagged position under it, and the run selects
# it again when it ends — on every exit path, unlike the stage / PFIB
# restores (see CPWorkflow._after_run). Move To leaves the position's
# holder selected.
#
# Names are exactly as xT lists them in ``stage.holder.available_values``.

# Holders a position can be saved with, and the tag the Stage Positions
# list shows for each. Saving under any other holder is refused.
SUPPORTED_HOLDERS: Dict[str, str] = {
    "AutoGridShuttle35": "AG",
    "CryoTEMStubShuttle": "ST",
    "HPFCarrierShuttle3mm35": "HPF",
    "HPFCarrierShuttle6mm35": "HPF",
    "HPFSapphireShuttle3mm35": "HPF",
    "HPFSapphireShuttle6mm35": "HPF",
    "LeicaShuttle35": "LS",
    "SpinMillHolder": "SM",
}

# Holders whose positions ask for confirmation: the Start dialog lists
# the GIS Deposition and Sputter Coat positions saved with them, and
# the Move To dialog says the holder changes. The other supported
# holders are switched without a dialog.
HOLDERS_TO_CONFIRM: Tuple[str, ...] = (
    "CryoTEMStubShuttle",
    "SpinMillHolder",
)

# The holder Home Stage runs under. The activity tilts the stage to 0°
# under the holder selected at that moment, selects this holder, then
# homes, whatever holder a preceding GIS Deposition or Sputter Coat
# left selected. Start is refused when Microscope Control does not
# list a holder an enabled activity selects.
HOME_STAGE_HOLDER: str = "AutoGridShuttle35"

# Holder-switch verification. Microscope Control may silently override
# a holder selection it does not permit (the command is accepted, the
# holder is then changed back), so every switch reads the holder back:
# poll until it matches (up to HOLDER_SWITCH_TIMEOUT_S, every
# HOLDER_SWITCH_POLL_S), then wait HOLDER_SWITCH_SETTLE_S and re-read
# to catch a delayed override. See microscope/holder.py.
HOLDER_SWITCH_TIMEOUT_S: float = 10.0
HOLDER_SWITCH_POLL_S: float = 0.5
HOLDER_SWITCH_SETTLE_S: float = 2.0


# =========================================================================
# RT Prep workflow
# =========================================================================

# --- GIS Purge ---
GIS_PURGE_DURATION_S: int = 120
GIS_PURGE_CHAMBER_RECOVERY_S: int = 30

GIS_PURGE_DURATION_MIN_S: int = 1
GIS_PURGE_DURATION_MAX_S: int = 999

# Home Stage has no per-activity parameters — its only configuration
# is MOVE_STAGE_TO_ORIGINAL_POSITION above.


# =========================================================================
# Cryo Prep workflow
# =========================================================================

# --- Sputter Coat ---

# Stage position for the sputter coat. The NSR sputter coater applies
# its pattern at a saved stage position (like GIS Deposition), so each
# Sputter Coat activity references a position by stable id. Empty string
# = no position selected; the workflow refuses to start an activity with
# an unselected position (stale ids are detected at Start, not eagerly).
SPUTTER_COAT_POSITION_ID_DEFAULT: str = ""

# Sputter ion species catalogue. Each entry exposes the species name
# (which doubles as the AutoScript ``plasma_gas`` enum string) and its
# available beam currents in amperes, plus the default current index
# for that species.
#
# Catalogue shape (preserve on edits — both QML and Python consumers
# depend on this):
#   "name":                str  — AutoScript plasma_gas value
#   "beamCurrents":        list of:
#       "display":         str  — UI label, matches xT formatting
#       "value":           float — beam current in amperes
#   "defaultCurrentIndex": int  — index into beamCurrents
#
# Exposed to QML via microscope/bounds.py's MicroscopeBoundsController.
# The QML SputterCoat component reads species via ``textRole: "name"``
# and currents via ``textRole: "display"`` / value field "value".
#
# SPUTTER_ION_SPECIES_DEFAULT_INDEX below is an index into this list,
# stored in records as ``ion_species_index``. PLASMA_GAS_SPECIES_NAMES
# below is derived from this catalogue's "name" fields, so the two
# representations cannot drift.
SPUTTER_ION_SPECIES: List[Dict[str, Any]] = [
    {
        "name": "Xenon",
        "beamCurrents": [
            {"display": "1.0 nA",  "value": 1.0e-9},
            {"display": "2.5 nA",  "value": 2.5e-9},
            {"display": "8.0 nA",  "value": 8.0e-9},
            {"display": "20 nA",   "value": 20e-9},
            {"display": "70 nA",   "value": 70e-9},
            {"display": "0.12 µA", "value": 120e-9},
            {"display": "0.36 µA", "value": 360e-9},
        ],
        "defaultCurrentIndex": 5,
    },
    {
        "name": "Argon",
        "beamCurrents": [
            {"display": "1.2 nA",  "value": 1.2e-9},
            {"display": "3.2 nA",  "value": 3.2e-9},
            {"display": "5.8 nA",  "value": 5.8e-9},
            {"display": "14 nA",   "value": 14e-9},
            {"display": "50 nA",   "value": 50e-9},
            {"display": "0.15 µA", "value": 150e-9},
            {"display": "0.25 µA", "value": 250e-9},
            {"display": "0.70 µA", "value": 700e-9},
        ],
        "defaultCurrentIndex": 5,
    },
    {
        "name": "Oxygen",
        "beamCurrents": [
            {"display": "1.5 nA",  "value": 1.5e-9},
            {"display": "2.8 nA",  "value": 2.8e-9},
            {"display": "13 nA",   "value": 13e-9},
            {"display": "64 nA",   "value": 64e-9},
            {"display": "0.20 µA", "value": 200e-9},
            {"display": "0.71 µA", "value": 710e-9},
        ],
        "defaultCurrentIndex": 4,
    },
    {
        "name": "Nitrogen",
        "beamCurrents": [
            {"display": "1.2 nA",  "value": 1.2e-9},
            {"display": "6.9 nA",  "value": 6.9e-9},
            {"display": "38 nA",   "value": 38e-9},
            {"display": "0.12 µA", "value": 120e-9},
            {"display": "0.45 µA", "value": 450e-9},
        ],
        "defaultCurrentIndex": 3,
    },
]

# Ion species: index into SPUTTER_ION_SPECIES above.
# 0 = Xenon, 1 = Argon, 2 = Oxygen, 3 = Nitrogen.
SPUTTER_ION_SPECIES_DEFAULT_INDEX: int = 0

# Plasma gas species names, indexed by sputter activities'
# ion_species_index. Derived from SPUTTER_ION_SPECIES so the two
# lists cannot drift — sputter_coat.py looks up the species name
# here before passing it to the AutoScript ion-beam plasma_gas setter,
# while the QML SputterCoat ComboBox reads species from the catalogue
# directly via microscope/bounds.py. Both consumers ultimately resolve
# through SPUTTER_ION_SPECIES, so an integer ``ion_species_index``
# means the same species on both sides by construction.
PLASMA_GAS_SPECIES_NAMES: List[str] = [s["name"] for s in SPUTTER_ION_SPECIES]

# Beam current default (amperes). Defaults to 0.12 µA — the
# Xenon-species default. When the user changes species in the UI, the
# QML re-binds the current ComboBox to that species' default current
# (which may differ from this app-wide default — the QML's per-species
# defaults are the canonical choice for each species). This default
# is only used when adding a fresh Sputter Coat activity, before the
# user has picked a species or current.
SPUTTER_ION_CURRENT_DEFAULT_A: float = 120e-9

# Sputter high voltage (volts). Fixed at 12 kV — not user-configurable.
# The sputter ion-species → beam-current catalogue (above) is curated
# for this HV; making HV adjustable would require the available currents
# to vary with HV too, so it is held constant (matching Hydra NSR). The
# Sputter Coat form's read-only HV label displays this in kilovolts,
# exposed via microscope/bounds.py as ``sputterHighVoltageKv`` (which
# divides this value by 1000). sputter_coat.py writes this defensively
# at the start of each run in case earlier session activity changed it.
SPUTTER_HIGH_VOLTAGE_V: int = 12000

SPUTTER_COAT_DURATION_DEFAULT_S: int = 120
SPUTTER_COAT_CHAMBER_RECOVERY_DEFAULT_S: int = 0

# --- GIS Deposition ---
# Empty string = no position selected; the workflow refuses to start
# an activity with an unselected position. Stale ids (referring to
# deleted positions) are detected at workflow Start, not eagerly.
GIS_DEPOSITION_POSITION_ID_DEFAULT: str = ""

GIS_DEPOSITION_DURATION_DEFAULT_S: int = 90
GIS_DEPOSITION_DURATION_MIN_S: int = 1
GIS_DEPOSITION_DURATION_MAX_S: int = 999

GIS_DEPOSITION_CHAMBER_RECOVERY_DEFAULT_S: int = 30


# --- Chamber recovery bounds (shared) ---
# Both sputter and GIS deposition use the same recovery range.
CHAMBER_RECOVERY_MIN_S: int = 0
CHAMBER_RECOVERY_MAX_S: int = 999


# --- First-launch / Restore-Defaults activity list ----------------------

# Default Cryo workflow shipped with the app: two pairs of
# (Sputter, GIS) activities. Each entry is
# a tuple of (activity_type, params_dict). Empty params dicts mean
# "use all defaults from the constants above" — passed to the matching
# Record dataclass's ``from_dict``, which fills missing fields.
DEFAULT_CRYO_ACTIVITY_LIST: List[Tuple[str, dict]] = [
    ("sputter_coat", {}),
    ("gis_deposition", {}),
    ("gis_deposition", {}),
    ("sputter_coat", {}),
]


# =========================================================================
# Stage Positions page
# =========================================================================

# Longest saved-position name accepted by add_from_current / rename.
# Names are interpolated into status-bar text ("Moving stage to ..."),
# so the cap keeps those lines readable. The name dialog enforces the
# same limit at input time via AppConfig.qml's positionNameMaxLength —
# keep the two in step.
STAGE_POSITION_NAME_MAX_CHARS: int = 40


# =========================================================================
# Stage / Scan page
# =========================================================================
#
# The Stage / Scan page is a manual-assist surface (not a workflow):
# the user clicks Rotate / Scan-Rotate or drags the Z slider, and
# operations execute as direct gestures with no per-activity records.
# The constants below configure those gestures' bounds and defaults.

# --- Tilt-after-rotation angle (degrees) ---
# Bounds are exposed to QML via microscope/bounds.py for the SpinBox's
# ``from`` / ``to``, and used by SettingsController to clamp the
# persisted ``tiltAfterRotationAngleDeg`` setting on write. The default
# is the SpinBox's initial value when no user-saved override exists.
TILT_AFTER_ROTATION_ANGLE_DEG_MIN: int = -10
TILT_AFTER_ROTATION_ANGLE_DEG_MAX: int = 60
TILT_AFTER_ROTATION_ANGLE_DEG_DEFAULT: int = 17

# --- Stage rotation checkbox factory defaults ---
# Initial values for the three Stage / Scan page checkboxes when no
# user-saved override exists. SettingsController persists the user's
# edits across sessions; these are the values that ship.
#
#   * Scan rotate after rotation: on by default — the typical
#     workflow rotates the stage and flips the scan rotation in
#     one gesture.
#   * Zero tilt before rotation: off — users tilt manually if they
#     need it.
#   * Tilt after rotation: off — paired with the angle SpinBox; the
#     user opts in explicitly.
SCAN_ROTATE_AFTER_ROTATION_DEFAULT: bool = True
ZERO_TILT_BEFORE_ROTATION_DEFAULT: bool = False
TILT_AFTER_ROTATION_DEFAULT: bool = False

# --- Stage Z slider worker tunables ---
# The Z slider's worker thread issues a ``relative_move`` every tick
# while the slider value is non-zero. The dz argument (in metres) is
# computed as ``slider_value × STAGE_Z_STEP_SIZE_M``; the wait between
# ticks is ``STAGE_Z_TICK_INTERVAL_S`` (seconds).
#
# Step size is 2.5 µm/step. Tick interval (50 ms) keeps
# the slider responsive without spamming the AutoScript input queue.
# At slider = ±25 (the AppConfig.qml limits) and these defaults, the
# worker produces ~1.25 mm/s of effective Z velocity at the extremes.
#
# Reduce STAGE_Z_STEP_SIZE_M for finer manual control; increase
# STAGE_Z_TICK_INTERVAL_S to slow the slider down. Both are kept as
# constants here rather than user settings — promote either to
# SettingsController if users ask for fine-control / fast-travel
# modes.
STAGE_Z_STEP_SIZE_M: float = 2.5e-6
STAGE_Z_TICK_INTERVAL_S: float = 0.05

# How often the Z worker re-reads the stage position to refresh the
# Z-slider safety gate, while the page is active and the slider is idle.
# Much slower than the tick above — the safe/blocked decision doesn't
# move fast. The worker polls every
# ``round(STAGE_SAFE_RANGE_POLL_INTERVAL_S / STAGE_Z_TICK_INTERVAL_S)``
# idle ticks, and skips the read entirely while the slider is driving Z
# (radial distance is invariant under Z motion, and it keeps all stage
# client I/O on the one worker thread).
STAGE_SAFE_RANGE_POLL_INTERVAL_S: float = 0.5

# --- Stage safety threshold for StagePositionWithinSafeRangeCheck ---
#
# A stage position whose radial distance from chamber center
# exceeds this threshold triggers an ASK_CONFIRM dialog at
# workflow Start (and at the start of direct Stage Rotation
# and Move To gestures). The threshold is intentionally
# conservative — it catches operationally unusual positions,
# not physically dangerous ones — the user can still confirm
# and proceed. Bump this as real hardware experience accrues.
#
# Tilt is deliberately not part of the check: legitimate operating
# positions (e.g., the GIS deposition default at 60°) routinely
# exceed any conservative tilt threshold, and the radial XY check
# already catches the operationally unusual positions. The snapshot
# still carries ``stage_t_rad`` for checks that may need it.

STAGE_SAFE_RADIAL_RANGE_M: float = 8e-3   # 8 mm from chamber center


# =========================================================================
# Developer / testing overrides
# =========================================================================
#
# Local development toggles, consolidated here so a single glance before
# distributing catches any flag left on. Keep them all at their committed
# defaults in shipped code; flip one locally to exercise a path, then flip
# it back.
#
# Reader contexts differ — these are not all read in the same place:
#   * DEV_FORCE_SIMULATION is read by the entry point
#     (hydra_nsr_cryo_utilities.py); it has no effect once a client
#     (real or simulated) exists.
#   * DEV_FORCE_UNLINKED / DEV_FORCE_STAGE_OUT_OF_RANGE /
#     DEV_FORCE_HOLDER_OVERRIDE / DEV_FORCE_STAGE_SHORTFALL /
#     DEV_SIMULATED_HOLDERS / DEV_SIMULATED_INITIAL_HOLDER are read only
#     by SimulatedStageOps (microscope/stage_ops.py) and have no effect
#     on the real StageOps facade.
#
# DEV_FORCE_SIMULATION:
#   Skip the microscope connection attempt entirely and start in
#   simulation mode (equivalent to passing --simulation on the command
#   line). The entry point ORs this with the --simulation flag and logs a
#   warning when it is set. Set back to False before distributing.
#
# The two stage-state seeds below let the pre-start check dialogs be
# exercised in simulation without editing the simulated initial position
# or the is_linked default by hand.
#
# DEV_FORCE_UNLINKED:
#   Start the simulated stage unlinked, so GIS Deposition's
#   ZLinkedToFreeWorkingDistanceCheck returns REFUSE on Start.
#
# DEV_FORCE_STAGE_OUT_OF_RANGE:
#   Start the simulated stage at DEV_FORCED_OUT_OF_RANGE_RADIAL_M from
#   chamber center, so StagePositionWithinSafeRangeCheck returns
#   ASK_CONFIRM for any gated gesture (GIS Deposition, Sputter Coat,
#   Home Stage, direct Stage Rotation). home() also resets to this
#   position while the flag is set, so the state persists across Homes
#   for repeated triggering.
#
# DEV_FORCE_HOLDER_OVERRIDE:
#   Make the simulated stage behave like Microscope Control overriding
#   a holder selection: set_holder() is accepted and the first readback
#   shows the new holder, but the next readback reverts to the previous
#   one. Exercises the "reverted" failure path of
#   microscope/holder.py's select_holder() (GIS Deposition and Sputter
#   Coat then fail with "holder selection failed"; the end-of-run holder
#   restore reports "holder not restored"; Move To reports "Holder
#   selection failed").
#
# DEV_FORCE_STAGE_SHORTFALL:
#   Make every simulated absolute_move land short of its command (X by
#   50 µm, tilt by 1°), the way xT clamps a move that leaves the
#   selected holder's limits. Exercises the arrival check in
#   microscope/stage_moves.py: GIS Deposition and Sputter Coat end with
#   "stage did not reach position", Move To with "Stage stopped short
#   of ...", and the stage restore reports "stage not restored". The X
#   miss shows up in the deviation log line; the tilt miss trips the
#   check.
#
# DEV_SIMULATED_HOLDERS:
#   The simulated stage's ``available_holders`` list: every supported
#   holder plus an unsupported generic entry, so the "Holder Not
#   Supported" path can be exercised by selecting it.
#
# DEV_SIMULATED_INITIAL_HOLDER:
#   The holder the simulated stage starts with.
DEV_FORCE_SIMULATION: bool = False
DEV_FORCE_UNLINKED: bool = False
DEV_FORCE_STAGE_OUT_OF_RANGE: bool = False
DEV_FORCE_HOLDER_OVERRIDE: bool = False
DEV_FORCE_STAGE_SHORTFALL: bool = False
DEV_SIMULATED_HOLDERS: List[str] = [*SUPPORTED_HOLDERS, "GenericHolder"]
DEV_SIMULATED_INITIAL_HOLDER: str = "AutoGridShuttle35"

# Radial distance (metres) used to seed the stage position when
# DEV_FORCE_STAGE_OUT_OF_RANGE is set. Chosen comfortably past
# STAGE_SAFE_RADIAL_RANGE_M so the check trips unambiguously; the value
# also shows up in the dialog text ("9.0 mm from center (0, 0)"). Keep
# this at least ~1 mm beyond STAGE_SAFE_RADIAL_RANGE_M whenever that
# limit changes, or DEV_FORCE_STAGE_OUT_OF_RANGE stops seeding an
# out-of-range position and the ASK_CONFIRM path can no longer be
# exercised in simulation.
DEV_FORCED_OUT_OF_RANGE_RADIAL_M: float = 9e-3