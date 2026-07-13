"""Tests for hydra_nsr_cu.cryo.template_seeding.

Pure filesystem tests — template_seeding has no Qt imports, so no
QApplication is needed. Run with: pytest tests/
"""
from __future__ import annotations

import shutil
from pathlib import Path

from hydra_nsr_cu.cryo import template_seeding


def _make_factory(tmp_path: Path, files: dict[str, str]) -> Path:
    factory = tmp_path / "factory"
    factory.mkdir()
    for name, content in files.items():
        (factory / name).write_text(content, encoding="utf-8")
    return factory


def test_seeds_missing_template(tmp_path):
    factory = _make_factory(tmp_path, {"default.json": '{"a": 1}\n'})
    target = tmp_path / "templates"
    target.mkdir()

    seeded = template_seeding.seed_factory_templates(
        templates_dir=target, factory_dir=factory,
    )

    dest = target / "default.json"
    assert seeded == [dest]
    assert dest.read_text(encoding="utf-8") == '{"a": 1}\n'


def test_skips_existing_file(tmp_path):
    factory = _make_factory(tmp_path, {"default.json": '{"factory": true}'})
    target = tmp_path / "templates"
    target.mkdir()
    dest = target / "default.json"
    dest.write_text('{"user_edited": true}', encoding="utf-8")

    seeded = template_seeding.seed_factory_templates(
        templates_dir=target, factory_dir=factory,
    )

    assert seeded == []
    assert dest.read_text(encoding="utf-8") == '{"user_edited": true}'


def test_partial_seed(tmp_path):
    factory = _make_factory(
        tmp_path,
        {"default.json": '{"d": 1}', "extra.json": '{"e": 2}'},
    )
    target = tmp_path / "templates"
    target.mkdir()
    (target / "default.json").write_text('{"mine": true}', encoding="utf-8")

    seeded = template_seeding.seed_factory_templates(
        templates_dir=target, factory_dir=factory,
    )

    assert seeded == [target / "extra.json"]
    assert (target / "default.json").read_text(encoding="utf-8") == '{"mine": true}'
    assert (target / "extra.json").read_text(encoding="utf-8") == '{"e": 2}'


def test_creates_templates_dir(tmp_path):
    factory = _make_factory(tmp_path, {"default.json": "{}"})
    target = tmp_path / "does" / "not" / "exist"

    seeded = template_seeding.seed_factory_templates(
        templates_dir=target, factory_dir=factory,
    )

    assert target.is_dir()
    assert seeded == [target / "default.json"]


def test_missing_factory_dir_is_noop(tmp_path):
    seeded = template_seeding.seed_factory_templates(
        templates_dir=tmp_path / "templates",
        factory_dir=tmp_path / "no_such_factory",
    )
    assert seeded == []


def test_copy_failure_does_not_raise(tmp_path, monkeypatch):
    factory = _make_factory(tmp_path, {"default.json": "{}"})
    target = tmp_path / "templates"

    def boom(src, dst, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(shutil, "copyfile", boom)

    seeded = template_seeding.seed_factory_templates(
        templates_dir=target, factory_dir=factory,
    )

    assert seeded == []
    assert not (target / "default.json").exists()
    assert list(target.glob("*.tmp")) == []


def test_packaged_factory_dir_contains_default():
    # Guards the shipped data file against being lost in a refactor:
    # the real package must contain factory_templates/default.json.
    factory = Path(template_seeding.__file__).resolve().parent / "factory_templates"
    assert (factory / "default.json").is_file()
