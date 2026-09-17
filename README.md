# Hydra NSR Cryo Utilities

> [!NOTE]
> **Full documentation:** https://chrisleethompson.github.io/scripts/hydra_nsr_cryo_utilities/

A PySide6/QML desktop utility for the Thermo Scientific Hydra NSR (Non-standard Request) cryo plasma FIB-SEM. It supports common cryo workflows on the microscope with automated preparation activities, stage movement controls, and angle calculators, and it runs through the Thermo Scientific AutoScript SDK. This script supports the Hydra NSR variant equipped with a gas injection system (GIS) Pt microsputter coater target; its Sputter Coat activity differs from the one in Hydra Bio Cryo Utilities, and the rest of the UI is nearly identical.

## Documentation

Full documentation: https://chrisleethompson.github.io/scripts/hydra_nsr_cryo_utilities/

## Features

- **RT Prep** activities for a room-temperature stage: GIS purge and home stage.
- **Cryo Prep** activities for a cold stage: stage positions, sputter coat (GIS microsputter), GIS deposition, and home stage, with an activity list to run them in sequence.
- **Stage/Scan** controls for driving the stage to regions of interest.
- **Lift-out Angle Calc** with three calculators for cryo lift-out stage tilt angles.
- **Milling Angle Calc** with interactive graphics showing stage tilt relative to the SEM, FIB, GIS, and milling positions, including an editable GIS position.
- **Session Log** that records RT Prep and Cryo Prep activities to a `.jsonl` file, plus a **Settings** page and a **Simulation Mode** for running without a microscope.

## Requirements

- Python 3.11+
- PySide6 6.7.1+
- Thermo Scientific AutoScript 4.14+ (required to control a microscope; not needed for simulation mode)

PySide6 6.7.1 is the version in the AutoScript 4.14 Python environment, where the script is developed and tested.

## Installation

1. Download the latest release ZIP from the [Releases page](https://github.com/ChrisLeeThompson/Hydra_NSR_Cryo_Utilities/releases).
2. Extract it and copy the script folder to your desired location. Scripts that control a microscope are best installed on the Support PC (SPC) or Microscope PC (MPC).
3. If you run the script with the AutoScript Python environment, no packages need to be installed. Otherwise, install them with:

   ```
   pip install -r requirements.txt
   ```

## Running

Run the main module from the script folder:

```
python hydra_nsr_cryo_utilities.py
```

The script also runs from the AutoScript Python interpreter or AutoScript Runner.

### Simulation Mode

To open the UI without a microscope connection, pass `--simulation`:

```
python hydra_nsr_cryo_utilities.py --simulation
```

In simulation mode the UI is fully navigable but cannot control a microscope. The script also falls back to simulation mode on its own when it cannot connect to a microscope, and shows "Microscope not available" in the status bar. To force simulation mode persistently, set `DEV_FORCE_SIMULATION` to `True` in `hydra_nsr_cu/defaults.py`.

## Updating

1. Download the latest release ZIP from the [Releases page](https://github.com/ChrisLeeThompson/Hydra_NSR_Cryo_Utilities/releases).
2. Copy the new files over the existing script folder, overwriting when prompted. Do not delete the old folder first.

An update preserves your data:

- Saved templates in `templates/` are never overwritten. The shipped default template lives inside the app (`hydra_nsr_cu/cryo/factory_templates/`) and is copied into `templates/` at startup only when a file with that name is missing. If you delete `templates/default.json`, it reappears on the next launch; to customize the default, edit it and save over it.
- The session log (`session_logs/session_log.jsonl`) is not part of the download, so it is never touched.
- Settings, stage positions, and the activity list are stored in the Windows registry, outside the folder entirely.

What is replaced: the application code, including the factory templates inside `hydra_nsr_cu/`.

## License

MIT, see [LICENSE](LICENSE). Copyright (c) 2026 Christopher Thompson.

The Catbug artwork in `qml_resources/assets/` is not covered by the MIT license; see [LICENSE](LICENSE). PySide6 (Qt for Python) is licensed under the LGPLv3 and is used as an unmodified runtime dependency installed from PyPI; it is not distributed with this source.

## Contact

Developed by Chris Thompson with assistance from Anthropic's Claude. Questions and suggestions are welcome: [@ChrisLeeThompson](https://github.com/ChrisLeeThompson) on GitHub.
