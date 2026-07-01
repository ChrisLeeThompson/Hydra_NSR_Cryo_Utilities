"""Microscope-domain bounds and catalogues, exposed to QML.

This module is the QML-facing surface for hardware-domain values that
live in :mod:`hydra_nsr_cu.defaults`. The pattern is read-only and
inverted from the v2.1 / early-v3 arrangement:

* **Source of truth** is Python (``defaults.py``).
* **QML reads** these values through this controller, registered as
  the ``MicroscopeBounds`` QML singleton in module
  ``HydraNSR.Microscope`` 1.0 (via ``qmlRegisterSingletonInstance``
  in the entry point).

Everything here is a ``@Property(..., constant=True)`` — the values
never change at runtime, so QML caches them after the first read and
no notify signal is needed. This applies to:

* Catalogues (``sputterIonSpecies``) — passed through to QML as a
  ``QVariantList``; nested dicts in the species catalogue surface as
  JS objects with ``.name``, ``.beamCurrents``, etc.
* Default indices (``sputterIonSpeciesDefaultIndex``) — for
  catalogue-backed ComboBox initial selection.
* Unit-converted values (``sputterHighVoltageKv``) — the canonical
  Python value lives in volts; the conversion happens inline so
  there is no second source of truth.
* Numeric bounds (``tiltAfterRotationMin/Max/Default``) — the same
  bounds are also consumed Python-side by ``SettingsController`` for
  clamping; both consumers resolve through the same constant.

Unlike :class:`MicroscopeClient`, this controller has no microscope
client dependency and is constructed eagerly at engine startup,
before any connection attempt. It is safe to read from QML the
moment the engine loads.

There is no simulated counterpart. The values are static constants
identical for both real and simulated paths; one controller serves
both modes.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from PySide6.QtCore import Property, QObject

from .. import defaults


class MicroscopeBoundsController(QObject):
    """Read-only QObject exposing hardware-domain bounds and catalogues.

    Registered as the ``MicroscopeBounds`` QML singleton in module
    ``HydraNSR.Microscope`` 1.0 (via ``qmlRegisterSingletonInstance``
    in the application entry point). QML reads each Property at binding-
    evaluation time; ``constant=True`` tells the engine the value
    never changes and binding evaluation can be cached.
    """

    def __init__(self, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)

    # --- Sputter Coat catalogues and defaults -----------------------------

    @Property("QVariantList", constant=True)
    def sputterIonSpecies(self) -> List[Dict[str, Any]]:
        """The canonical sputter ion-species catalogue.

        Each entry has ``name`` (str — also the AutoScript
        ``plasma_gas`` enum string), ``beamCurrents`` (list of dicts
        with ``display`` and ``value``), and ``defaultCurrentIndex``
        (int into ``beamCurrents``). See
        :data:`defaults.SPUTTER_ION_SPECIES` for the full schema.

        Bound to SputterCoat.qml's species ComboBox as its ``model``,
        with ``textRole: "name"``. Per-species beam currents are
        read from the selected entry's ``beamCurrents`` field at
        species-change time.
        """
        return defaults.SPUTTER_ION_SPECIES

    @Property(int, constant=True)
    def sputterIonSpeciesDefaultIndex(self) -> int:
        """Index into :attr:`sputterIonSpecies` for the initial species."""
        return defaults.SPUTTER_ION_SPECIES_DEFAULT_INDEX

    @Property(int, constant=True)
    def sputterHighVoltageKv(self) -> int:
        """Sputter high voltage in kilovolts (read-only display value).

        The Python-side authoritative value lives in volts as
        :data:`defaults.SPUTTER_HIGH_VOLTAGE_V`; this Property converts
        to kV at read time so the Sputter Coat form's HV label can
        display the conventional unit. Single source of truth: the volts
        constant in ``defaults.py``. The HV is fixed (not user-set) — the
        sputter ion-species → beam-current catalogue is curated for it.
        """
        return defaults.SPUTTER_HIGH_VOLTAGE_V // 1000

    # --- Stage / Scan page bounds -----------------------------------------

    @Property(int, constant=True)
    def tiltAfterRotationMin(self) -> int:
        """Lower bound (degrees) for the tilt-after-rotation SpinBox.

        The same value clamps the persisted
        ``tiltAfterRotationAngleDeg`` setting in
        :class:`SettingsController` — both consumers resolve through
        :data:`defaults.TILT_AFTER_ROTATION_ANGLE_DEG_MIN`.
        """
        return defaults.TILT_AFTER_ROTATION_ANGLE_DEG_MIN

    @Property(int, constant=True)
    def tiltAfterRotationMax(self) -> int:
        """Upper bound (degrees) for the tilt-after-rotation SpinBox."""
        return defaults.TILT_AFTER_ROTATION_ANGLE_DEG_MAX

    @Property(int, constant=True)
    def tiltAfterRotationDefault(self) -> int:
        """Default value (degrees) for the tilt-after-rotation SpinBox."""
        return defaults.TILT_AFTER_ROTATION_ANGLE_DEG_DEFAULT