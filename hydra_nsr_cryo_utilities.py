# This Python file uses the following encoding: utf-8
"""
Hydra NSR Cryo Utilities
.
A PySide6/QML desktop UI for the Thermo Scientific Hydra NSR microscope
(AutoScript >= 4.13). Run this file to launch; pass ``--simulation`` to
start without a microscope connection. Application logic lives in the
``hydra_nsr_cu`` package -- this module only wires up the Qt application,
the QML engine, and the controllers.
.
Authors: Chris Thompson (GitHub: ChrisLeeThompson) and Anthropic's Claude
.
Copyright (c) 2026 Christopher Thompson.
Released under the MIT License.
.
Permission is hereby granted, free of charge, to any person obtaining a
copy of this software and associated documentation files (the "Software"),
to deal in the Software without restriction, including without limitation
the rights to use, copy, modify, merge, publish, distribute, sublicense,
and/or sell copies of the Software, and to permit persons to whom the
Software is furnished to do so, subject to the following conditions:
.
The above copyright notice and this permission notice shall be included in
all copies or substantial portions of the Software.
.
THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING
FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS
IN THE SOFTWARE.
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtGui import QGuiApplication, QIcon
from PySide6.QtQml import QQmlApplicationEngine, qmlRegisterSingletonInstance
from PySide6.QtQuickControls2 import QQuickStyle

from hydra_nsr_cu import __version__, defaults
from hydra_nsr_cu.app_controller import AppController
from hydra_nsr_cu.logging_setup import setup_logging
from hydra_nsr_cu.microscope.bounds import MicroscopeBoundsController

logger = logging.getLogger(__name__)


def parse_args(argv: list[str]) -> argparse.Namespace:
    """Parse CLI args, ignoring any that Qt wants to consume."""
    parser = argparse.ArgumentParser(
        description=f"Hydra NSR Cryo Utilities v{__version__}",
    )
    parser.add_argument(
        "--simulation",
        action="store_true",
        help="Skip the microscope connection and run in simulation mode.",
    )
    # parse_known_args lets Qt's own flags (e.g. -style, -platform) pass through.
    args, _ = parser.parse_known_args(argv[1:])
    return args


def main(argv: list[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv

    args = parse_args(argv)
    setup_logging()
    logger.info("Starting Hydra NSR Cryo Utilities v%s", __version__)

    base_dir = Path(__file__).resolve().parent

    app = QGuiApplication(argv)
    app.setOrganizationName("TFS_AutoScript")
    app.setApplicationName("TFS_AutoScript_HydraNSRUtilities_v3")

    QQuickStyle.setStyle("Universal")
    app.setWindowIcon(
        QIcon(str(base_dir / "qml_resources" / "assets" / "catbug_waiting_color.png"))
    )

    # Build the controller before the engine so the QML binding target exists
    # the moment QML loads. initialize() is deferred (below) so we don't block
    # window rendering on the connect attempt.
    # The DEV_FORCE_SIMULATION dev toggle lives in defaults.py's
    # "Developer / testing overrides" section (alongside the other
    # DEV_FORCE_* flags); OR it with the --simulation CLI flag here.
    force_simulation = args.simulation or defaults.DEV_FORCE_SIMULATION
    if defaults.DEV_FORCE_SIMULATION:
        logger.warning(
            "defaults.DEV_FORCE_SIMULATION is set; running in simulation mode."
        )
    app_controller = AppController(force_simulation=force_simulation)
    app.aboutToQuit.connect(app_controller.shutdown)

    # Hardware-domain bounds and catalogues exposed read-only to QML
    # as the ``MicroscopeBounds`` singleton. Eagerly constructed (no
    # microscope-client dependency) so QML bindings resolve from the
    # moment the engine loads.
    #
    # Registered as a QML singleton (not a context property) because
    # the values are read-only static data — the singleton's lifecycle
    # is aligned with the QML engine's full lifetime, including the
    # teardown phase when consumer objects re-evaluate their bindings.
    # A context property would be nulled earlier in teardown and
    # produce spurious "cannot read property of null" errors on the
    # consumers' final binding evaluation.
    bounds_controller = MicroscopeBoundsController()
    qmlRegisterSingletonInstance(
        MicroscopeBoundsController,
        "HydraNSR.Microscope",
        1, 0,
        "MicroscopeBounds",
        bounds_controller,
    )

    engine = QQmlApplicationEngine()
    engine.quit.connect(app.quit)
    engine.addImportPath(str(base_dir / "qml_resources"))
    engine.rootContext().setContextProperty("appController", app_controller)
    # Single source of truth for the version shown in the window title:
    # the package __version__ (also used by the CLI help and logs).
    engine.rootContext().setContextProperty("appVersion", __version__)

    engine.load(str(base_dir / "qml_resources" / "main.qml"))
    if not engine.rootObjects():
        logger.error("Failed to load QML file")
        return -1

    # Defer the microscope connection until after the event loop starts so
    # the window is already visible while we (potentially) block on connect.
    QTimer.singleShot(0, app_controller.initialize)

    return app.exec()


if __name__ == "__main__":
    sys.exit(main())