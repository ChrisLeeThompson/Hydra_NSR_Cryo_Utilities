"""Sputter pattern application file (.ptf) handling.

The Hydra NSR Sputter Coat activity applies a saved FEI pattern file
(``.ptf``) through the ion-beam patterning subsystem. This module is the
pure file/XML side of that: it locates the configured ``.ptf`` in the
``pattern_files/`` directory, validates it, and parses out the polygon
vertices, spot pitch, and application-file name the activity feeds to
:meth:`PatterningOps.create_polygon`.

Kept in the activities package (not the microscope ops layer) because
it has no hardware dependency — it's pure parsing consumed only by
:class:`hydra_nsr_cu.activities.sputter_coat.SputterCoatService`. Ported
from v2's ``parse_pattern_file`` (see the monolithic v2 reference), with
two improvements: a single :class:`PatternFileError` for every failure
mode (so the activity catches one exception type and surfaces its
message) and explicit existence/extension validation split out into
:func:`validate_pattern_file`.

File format
-----------
A ``.ptf`` is FEI's ``PatternFile 1.2`` XML. The fields this module
reads:

* ``<Application>`` — the application-file name (e.g. ``"Dx_M GISOpen"``)
  assigned to both the polygon pattern and the patterning default.
* ``<PitchX>`` / ``<PitchY>`` — spot spacing in metres.
* ``<Points>`` — the polygon boundary. Its text is itself an *escaped*
  inner XML document (``&lt;Points&gt;&lt;Point&gt;...``), so it is
  re-parsed to read each ``<Point>``'s ``<PositionX>`` / ``<PositionY>``.
"""
from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import List, Tuple

logger = logging.getLogger(__name__)


# The pattern_files/ directory lives at the repo root — a sibling of the
# hydra_nsr_cu package and the main entry module — where the shipped
# pattern_Sputtering.ptf already lives. From this file
# (hydra_nsr_cu/activities/pattern_file.py) the root is parents[2].
PATTERN_FILES_BASE_PATH: Path = Path(__file__).resolve().parents[2] / "pattern_files"


class PatternFileError(Exception):
    """Raised when a sputter pattern file is missing or unparseable.

    Carries a user-facing message (``str(exc)``) that the Sputter Coat
    activity surfaces in the status bar / log on failure.
    """


def resolve_pattern_path(name: str) -> Path:
    """Resolve a pattern file *name* to its path under ``pattern_files/``.

    ``name`` is the bare file name stored in settings
    (``SettingsController.sputterPatternFile``), not a full path.
    """
    return PATTERN_FILES_BASE_PATH / name


def validate_pattern_file(path: Path) -> None:
    """Validate that ``path`` is an existing ``.ptf`` file.

    Raises :class:`PatternFileError` with a user-facing message if the
    file is missing, is not a regular file, or doesn't end in ``.ptf``.
    Mirrors v2's three checks.
    """
    if not path.exists():
        raise PatternFileError(f"Pattern file not found: {path}")
    if not path.is_file():
        raise PatternFileError(f"Pattern path is not a file: {path}")
    if path.suffix.lower() != ".ptf":
        raise PatternFileError(
            f"Pattern file must be a .ptf file, got: {path.suffix}"
        )


def parse_pattern_file(
    path: Path,
) -> Tuple[List[Tuple[float, float]], float, float, str]:
    """Parse a ``.ptf`` into ``(vertices, pitch_x, pitch_y, application_file)``.

    * ``vertices`` — list of ``(x, y)`` polygon points in metres.
    * ``pitch_x`` / ``pitch_y`` — spot spacing in metres.
    * ``application_file`` — the ``<Application>`` name.

    Raises :class:`PatternFileError` on any read/parse failure, or if a
    required field (application file, either pitch, or the points block)
    is missing. The caller treats that as a fatal activity error.
    """
    try:
        content = path.read_text(encoding="utf-8")
        root = ET.fromstring(content)

        application_element = root.find(".//Application")
        pitch_x_element = root.find(".//PitchX")
        pitch_y_element = root.find(".//PitchY")
        points_element = root.find(".//Points")
        if (
            application_element is None
            or application_element.text is None
            or pitch_x_element is None
            or pitch_x_element.text is None
            or pitch_y_element is None
            or pitch_y_element.text is None
            or points_element is None
            or points_element.text is None
        ):
            raise PatternFileError(
                f"Pattern file is missing application/pitch/points data: {path}"
            )

        application_file = application_element.text.strip()
        pitch_x = float(pitch_x_element.text.strip())
        pitch_y = float(pitch_y_element.text.strip())

        # The Points element's text is an escaped inner XML document;
        # re-parse it to read each Point's PositionX / PositionY.
        points_root = ET.fromstring(points_element.text.strip())
        vertices: List[Tuple[float, float]] = []
        for point in points_root.findall("Point"):
            pos_x = point.find("PositionX")
            pos_y = point.find("PositionY")
            if (
                pos_x is None or pos_x.text is None
                or pos_y is None or pos_y.text is None
            ):
                continue
            vertices.append((float(pos_x.text), float(pos_y.text)))

        if not vertices:
            raise PatternFileError(
                f"Pattern file contains no polygon points: {path}"
            )

        logger.info(
            "Parsed pattern file %s: %d vertices, pitch=(%.3e, %.3e), "
            "application=%r",
            path.name, len(vertices), pitch_x, pitch_y, application_file,
        )
        return vertices, pitch_x, pitch_y, application_file
    except PatternFileError:
        raise
    except (OSError, ET.ParseError, ValueError) as exc:
        raise PatternFileError(
            f"Could not parse pattern file {path}: {exc}"
        ) from exc
