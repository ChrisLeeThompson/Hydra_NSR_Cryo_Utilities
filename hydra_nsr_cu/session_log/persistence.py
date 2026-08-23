"""JSON Lines (JSONL) persistence for the session log.

Three pure functions over file paths and record dicts. No Qt, no
model state, no awareness of the entry dataclasses — this is the
durable layer the SessionLog controller composes on top of. The
dataclass ↔ dict conversion happens above this layer (see
:mod:`records`).

Crash-durability profile
------------------------
* :func:`append_record` opens in append mode and writes a single JSON
  line followed by a newline. Prior lines are physically untouched;
  a crash during this write leaves at most a torn final line, which
  :func:`load_all` tolerates by skipping with a warning. This is the
  common path that runs every time an activity finishes — the
  crash-critical scenario the log was designed to survive.

* :func:`atomic_rewrite` writes the full record list to a sibling
  ``.tmp`` file then ``os.replace()``-es it onto the destination.
  ``os.replace`` is atomic on both POSIX and Windows: readers either
  see the old file or the new file, never a partially-written one.
  Used for the rare note-edit path; a crash before the replace
  leaves the old file fully intact.

* :func:`load_all` is tolerant by design — missing file, torn lines,
  unparseable JSON, and non-dict JSON values all degrade to "skip
  with warning" rather than raising. The log is observational, never
  load-bearing, so a corrupt file must still produce a renderable
  (even if partial) view rather than blanking the page.

Append-creates-file-if-missing is the basic self-heal primitive: a
caller that detects the file vanished can simply continue appending
and the file will be re-created. A higher-level controller that
knows the in-memory model can compose :func:`atomic_rewrite` to
self-heal *back to the full model state* on disappearance — that
policy belongs above this layer because it needs model awareness.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Union

logger = logging.getLogger(__name__)


# Type alias for "anything path-like" — accepts str or Path so callers
# at any layer can hand in whatever shape is natural for them.
PathLike = Union[str, Path]


def load_all(path: PathLike) -> List[Dict[str, Any]]:
    """Read all JSONL records from ``path``, tolerant of corruption.

    Returns the raw record dicts in file order. Higher layers handle
    schema validation (via :func:`records.entry_from_dict`).

    Tolerance policy:

    * Missing file → empty list, no error logged. (Expected on the
      first run, or after the file was deleted out-of-band — the
      caller treats this as the empty-state.)
    * Blank lines → silently skipped (cosmetic file edits that add
      whitespace shouldn't trip the loader).
    * Unparseable JSON on a line → that line dropped with a warning,
      surrounding lines kept.
    * JSON value that is not an object (e.g. an array or a bare
      string) → dropped with a warning. Entries are always dicts.
    * Invalid UTF-8 bytes (e.g. a crash mid-append that split a
      multibyte sequence) → decoded with ``errors="replace"`` so the
      mangled bytes become U+FFFD rather than raising
      ``UnicodeDecodeError`` (a ``ValueError`` subclass, which is
      neither an ``OSError`` nor a ``JSONDecodeError`` and would
      otherwise escape both guards below). The replacement char then
      makes the line fail ``json.loads`` and it is skipped with a
      warning like any other torn line.
    * Other OSError reading the file → warning logged, empty list
      returned. We prefer to render an empty page over crashing the
      app for a transient I/O issue.
    """
    path = Path(path)
    if not path.exists():
        return []

    records: List[Dict[str, Any]] = []
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for lineno, line in enumerate(f, start=1):
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError as exc:
                    logger.warning(
                        "Session log: skipping unparseable line %d of %s "
                        "(%s)",
                        lineno, path, exc,
                    )
                    continue
                if not isinstance(obj, dict):
                    logger.warning(
                        "Session log: skipping non-dict line %d of %s "
                        "(got %s)",
                        lineno, path, type(obj).__name__,
                    )
                    continue
                records.append(obj)
    except OSError:
        logger.exception(
            "Session log: failed to read %s; returning empty list", path,
        )
        return []
    return records


def append_record(path: PathLike, record: Dict[str, Any]) -> None:
    """Append a single JSON line to ``path``, creating the file if missing.

    Creates the parent directory if it doesn't exist (``mkdir`` with
    ``parents=True, exist_ok=True``) so a missing ``session_logs/``
    directory doesn't break the write. Opens in append mode so prior
    lines are not at risk; the worst case from a crash mid-call is a
    torn final line that :func:`load_all` skips on the next read.

    The record dict must be JSON-serializable — callers either feed
    in dataclass ``to_dict()`` output or
    :meth:`ActivityService.parameter_summary` output, both clean by
    contract.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(record, ensure_ascii=False)
    with open(path, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def atomic_rewrite(path: PathLike, records: List[Dict[str, Any]]) -> None:
    """Rewrite ``path`` to contain exactly ``records``, atomically.

    Writes to a sibling ``<path>.tmp`` file, then ``os.replace()``-es
    it onto the destination. ``os.replace`` is atomic on both POSIX
    and Windows. If a crash interrupts the write before the replace,
    the old file is left fully intact; if it interrupts during the
    replace itself, the OS guarantees either the old or new content
    is visible — never a torn file.

    An empty list produces an empty file, which is the legitimate
    "clear all" semantics used by the Settings delete-log control.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Sibling .tmp file. Naming it with the original suffix preserves
    # the file extension for any external tool that filters by it.
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with open(tmp_path, "w", encoding="utf-8") as f:
        for record in records:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    os.replace(tmp_path, path)