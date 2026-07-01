"""Ion beam hardware ops.

Wraps AutoScript's ``microscope.beams.ion_beam`` with a narrow,
intention-revealing surface. Two implementations live side by side:

* :class:`IonBeamOps` — real, backed by ``SdbMicroscopeClient.beams.ion_beam``.
* :class:`SimulatedIonBeamOps` — in-memory simulation for offline UI development.

The exposed surface covers what Sputter Coat (Session 5B) and the
Stage / Scan page (Session 6) need: beam on/off, plasma gas
selection, high voltage, beam current, and scan rotation.

Plasma-gas opacity
------------------
On the real client, ``plasma_gas`` values are AutoScript's
``PlasmaGasType`` enum members; on the simulated client they're
strings. Both are accepted as setter inputs and returned by the
getter — activities should treat the value as opaque and compare
only via ``==``.

Plasma-gas name ↔ opaque value translation
------------------------------------------
The module-level :func:`resolve_plasma_gas_enum` helper translates an
activity-domain species name (``"Xenon"``, ``"Argon"``, …) into the
opaque value the setter accepts. Two callers use it today:

* :mod:`hydra_nsr_cu.activities.sputter_coat` — to set the gas on the
  real beam (``ion_beam.plasma_gas = resolve_plasma_gas_enum(name)``)
  during a sputter run.
* :class:`hydra_nsr_cu.workflows.cp_workflow.CPWorkflow` — to map the
  microscope's live ``plasma_gas`` reading *back* to a catalog index
  for the SputterCoat toggle-on auto-sync UX. The reverse lookup walks
  the catalog names, resolves each to its opaque value, and compares
  to the microscope reading via ``==`` (per the contract above).

The helper lives here rather than next to either consumer so the
name→enum mapping has one source of truth — same boundary as the
rest of this module.
"""
from __future__ import annotations

import logging
from typing import Any, List

logger = logging.getLogger(__name__)


# Plasma gas type — opaque to activity code. See module docstring.
PlasmaGasValue = Any


def resolve_plasma_gas_enum(species_name: str) -> PlasmaGasValue:
    """Translate a species name string to an AutoScript ``PlasmaGasType``.

    Done as a lazy import so this module can be loaded in a
    simulation-only environment where AutoScript isn't installed. The
    returned value is opaque to callers — they just hand it to the
    ``ion_beam.plasma_gas`` setter, or compare it to the getter's
    return value via ``==``.

    For simulation environments (where importing ``PlasmaGasType`` would
    fail), we fall back to returning the species name as a string. The
    simulated ion beam ops accept strings, so this works transparently.
    The real ion beam ops would reject a string with a clear AutoScript
    error, but that path is never taken with the real client.

    Unknown names (not in the four-element catalog) also fall back to
    the string, with a warning log — caller can still use the result
    as an opaque value, but a real-client setter call will fail
    informatively.
    """
    try:
        from autoscript_sdb_microscope_client.enumerations import PlasmaGasType
    except ImportError:
        logger.debug(
            "AutoScript not available; passing plasma gas as string %r",
            species_name,
        )
        return species_name

    enum_lookup = {
        "Xenon":    PlasmaGasType.XENON,
        "Argon":    PlasmaGasType.ARGON,
        "Oxygen":   PlasmaGasType.OXYGEN,
        "Nitrogen": PlasmaGasType.NITROGEN,
    }
    if species_name not in enum_lookup:
        logger.warning(
            "Unknown plasma gas species %r; passing through as string",
            species_name,
        )
        return species_name
    return enum_lookup[species_name]


class IonBeamOps:
    """Real ion beam ops backed by ``SdbMicroscopeClient.beams.ion_beam``."""

    def __init__(self, sdb: Any) -> None:
        self._sdb = sdb

    @property
    def is_on(self) -> bool:
        return bool(self._sdb.beams.ion_beam.is_on)

    def turn_on(self) -> None:
        logger.info("Ion beam: turn_on")
        self._sdb.beams.ion_beam.turn_on()

    def turn_off(self) -> None:
        logger.info("Ion beam: turn_off")
        self._sdb.beams.ion_beam.turn_off()

    @property
    def plasma_gas(self) -> PlasmaGasValue:
        return self._sdb.beams.ion_beam.source.plasma_gas.value

    @plasma_gas.setter
    def plasma_gas(self, value: PlasmaGasValue) -> None:
        logger.info("Ion beam: setting plasma gas to %r", value)
        self._sdb.beams.ion_beam.source.plasma_gas.value = value

    @property
    def plasma_gas_available(self) -> List[PlasmaGasValue]:
        return list(self._sdb.beams.ion_beam.source.plasma_gas.available_values)

    @property
    def high_voltage(self) -> float:
        return float(self._sdb.beams.ion_beam.high_voltage.value)

    @high_voltage.setter
    def high_voltage(self, value: float) -> None:
        logger.info("Ion beam: setting high voltage to %.0f V", value)
        self._sdb.beams.ion_beam.high_voltage.value = float(value)

    @property
    def beam_current(self) -> float:
        return float(self._sdb.beams.ion_beam.beam_current.value)

    @beam_current.setter
    def beam_current(self, value: float) -> None:
        logger.info("Ion beam: setting beam current to %.3e A", value)
        self._sdb.beams.ion_beam.beam_current.value = float(value)

    @property
    def horizontal_field_width(self) -> float:
        """Ion-beam horizontal field width (HFW), in metres."""
        return float(self._sdb.beams.ion_beam.horizontal_field_width.value)

    @horizontal_field_width.setter
    def horizontal_field_width(self, value: float) -> None:
        logger.info("Ion beam: setting HFW to %.3e m", value)
        self._sdb.beams.ion_beam.horizontal_field_width.value = float(value)

    @property
    def working_distance(self) -> float:
        """Ion-beam working distance, in metres."""
        return float(self._sdb.beams.ion_beam.working_distance.value)

    @working_distance.setter
    def working_distance(self, value: float) -> None:
        logger.info("Ion beam: setting working distance to %.3e m", value)
        self._sdb.beams.ion_beam.working_distance.value = float(value)

    @property
    def scan_rotation_rad(self) -> float:
        """Current scan rotation, in radians.

        Used by the Stage / Scan page's "Scan Rotate SEM and FIB
        180°" operation, which canonically toggles between 0 and π.
        """
        return float(self._sdb.beams.ion_beam.scanning.rotation.value)

    @scan_rotation_rad.setter
    def scan_rotation_rad(self, value: float) -> None:
        logger.info("Ion beam: setting scan rotation to %.4f rad", value)
        self._sdb.beams.ion_beam.scanning.rotation.value = float(value)


# --- Simulated implementation -------------------------------------------


# Plasma gas values for the simulated client. Strings — comparable via
# == in activity code without depending on AutoScript's enum. Names
# match the real PlasmaGasType members so log output looks identical.
_SIM_PLASMA_GASES: List[str] = ["Xenon", "Argon", "Oxygen", "Nitrogen"]


class SimulatedIonBeamOps:
    """Simulated ion beam ops for offline development.

    Tracks state in memory so subsequent reads reflect prior writes.
    Useful for verifying recorder save/restore without hardware.
    """

    def __init__(self) -> None:
        self._is_on: bool = False
        self._plasma_gas: str = _SIM_PLASMA_GASES[0]
        # 12 kV — a plausible Hydra NSR sputter HV starting value.
        self._high_voltage: float = 12000.0
        # 0.12 µA — matches the Xenon sputter default.
        self._beam_current: float = 0.12e-6
        # HFW seeded near the sputter default (1998 µm) and working
        # distance at 0 — both written by the Sputter Coat activity.
        self._horizontal_field_width: float = 1998e-6
        self._working_distance: float = 0.0
        # Scan rotation starts at 0 rad — the canonical "untoggled"
        # state expected by the Stage / Scan page's flip operation.
        self._scan_rotation_rad: float = 0.0

    @property
    def is_on(self) -> bool:
        return self._is_on

    def turn_on(self) -> None:
        logger.info("Simulated ion beam: turn_on")
        self._is_on = True

    def turn_off(self) -> None:
        logger.info("Simulated ion beam: turn_off")
        self._is_on = False

    @property
    def plasma_gas(self) -> str:
        return self._plasma_gas

    @plasma_gas.setter
    def plasma_gas(self, value: str) -> None:
        canonical = str(value)
        logger.info("Simulated ion beam: setting plasma gas to %r", canonical)
        self._plasma_gas = canonical

    @property
    def plasma_gas_available(self) -> List[str]:
        return list(_SIM_PLASMA_GASES)

    @property
    def high_voltage(self) -> float:
        return self._high_voltage

    @high_voltage.setter
    def high_voltage(self, value: float) -> None:
        logger.info("Simulated ion beam: setting high voltage to %.0f V", value)
        self._high_voltage = float(value)

    @property
    def beam_current(self) -> float:
        return self._beam_current

    @beam_current.setter
    def beam_current(self, value: float) -> None:
        logger.info("Simulated ion beam: setting beam current to %.3e A", value)
        self._beam_current = float(value)

    @property
    def horizontal_field_width(self) -> float:
        return self._horizontal_field_width

    @horizontal_field_width.setter
    def horizontal_field_width(self, value: float) -> None:
        logger.info("Simulated ion beam: setting HFW to %.3e m", value)
        self._horizontal_field_width = float(value)

    @property
    def working_distance(self) -> float:
        return self._working_distance

    @working_distance.setter
    def working_distance(self, value: float) -> None:
        logger.info(
            "Simulated ion beam: setting working distance to %.3e m", value
        )
        self._working_distance = float(value)

    @property
    def scan_rotation_rad(self) -> float:
        return self._scan_rotation_rad

    @scan_rotation_rad.setter
    def scan_rotation_rad(self, value: float) -> None:
        logger.info(
            "Simulated ion beam: setting scan rotation to %.4f rad", value,
        )
        self._scan_rotation_rad = float(value)


# Type alias for callers that accept either implementation.
IonBeamOpsLike = IonBeamOps | SimulatedIonBeamOps