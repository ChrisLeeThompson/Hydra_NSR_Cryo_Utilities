"""Seed the user templates directory from packaged factory templates.

The ``templates/`` directory at the project root is *user-owned*: the
files in it are created and edited by the user and are never tracked in
git, so a paste-over update (copying a fresh GitHub download onto an
existing install) can never overwrite them. The shipped defaults live
here in the package, under ``factory_templates/``, and are copied into
``templates/`` at startup **only when a file with the same name is
missing** — an existing user file always wins, even if it started life
as a factory copy the user later edited.

Consequences of the copy-if-missing rule:

* Deleting ``templates/default.json`` resurrects the factory copy on
  the next launch.
* A release that changes an existing factory template never reaches a
  user who already has that filename. If a future release must ship a
  changed default, ship it under a *new* filename (e.g.
  ``default_v2.json``) so copy-if-missing delivers it.

Seeding is best-effort and never raises: an unwritable directory just
means the Save/Load dialogs behave as they did before this module
existed (first save into a missing directory fails with an actionable
error). File I/O lives here rather than in :mod:`templates`, which is
deliberately pure (see its module docstring).
"""
from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger(__name__)


def seed_factory_templates(
    templates_dir: Optional[Path] = None,
    factory_dir: Optional[Path] = None,
) -> List[Path]:
    """Copy factory templates into the user templates dir if missing.

    Copy-if-missing: never overwrites an existing file. Never raises —
    all I/O failures are logged as warnings and skipped. Returns the
    list of destination paths actually seeded.

    Both directories are injectable for tests; production callers use
    the defaults (``factory_templates/`` next to this file, and
    ``templates/`` at the project root — the same three-parent
    derivation as ``templatesDirUrl`` in :mod:`controller`).
    """
    if factory_dir is None:
        factory_dir = Path(__file__).resolve().parent / "factory_templates"
    if templates_dir is None:
        project_root = Path(__file__).resolve().parent.parent.parent
        templates_dir = project_root / "templates"

    if not factory_dir.is_dir():
        logger.warning(
            "Cryo templates: factory dir %s missing; nothing to seed.",
            factory_dir,
        )
        return []

    try:
        templates_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        logger.warning(
            "Cryo templates: could not create %s; seeding skipped.",
            templates_dir, exc_info=True,
        )
        return []

    seeded: List[Path] = []
    for src in sorted(factory_dir.glob("*.json")):
        dest = templates_dir / src.name
        if dest.exists():
            logger.debug(
                "Cryo templates: %s already exists; not overwritten.", dest,
            )
            continue
        # Atomic copy (sibling ``.tmp`` + ``os.replace``), mirroring
        # save_template in controller.py: a crash or full disk mid-copy
        # must not leave a truncated template behind.
        tmp_path = dest.with_suffix(dest.suffix + ".tmp")
        try:
            try:
                shutil.copyfile(src, tmp_path)
                os.replace(tmp_path, dest)
            except BaseException:
                # Best-effort cleanup of the partial tmp file; never
                # mask the original error.
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass
                raise
        except OSError:
            logger.warning(
                "Cryo templates: could not seed %s.", dest, exc_info=True,
            )
            continue
        seeded.append(dest)

    if seeded:
        logger.info(
            "Cryo templates: seeded %d factory template(s) into %s",
            len(seeded), templates_dir,
        )
    return seeded
