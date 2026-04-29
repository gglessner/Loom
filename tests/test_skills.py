"""Tests for SkillManager and the multi-layer discovery behavior wired up
in cli.py."""

from __future__ import annotations

from pathlib import Path

import pytest

from loom.skills import SkillManager


def test_discovers_markdown_files(tmp_path: Path) -> None:
    d = tmp_path / "skills"
    d.mkdir()
    (d / "alpha.md").write_text("alpha body", encoding="utf-8")
    (d / "beta.md").write_text("beta body", encoding="utf-8")
    sm = SkillManager([d])
    names = sm.discover()
    assert sorted(names) == ["alpha", "beta"]
    assert sm.skills["alpha"] == "alpha body"


def test_visible_skills_dir_works_alongside_hidden(tmp_path: Path) -> None:
    """Regression: a top-level `./skills/` must be discoverable, not just
    the hidden `./.loom/skills/`."""
    visible = tmp_path / "skills"
    hidden = tmp_path / ".loom" / "skills"
    visible.mkdir()
    hidden.mkdir(parents=True)
    (visible / "shared.md").write_text("from visible", encoding="utf-8")
    (hidden / "private.md").write_text("from hidden", encoding="utf-8")

    sm = SkillManager([visible, hidden])
    names = sm.discover()
    assert sorted(names) == ["private", "shared"]


def test_later_dirs_override_earlier_on_filename_collision(tmp_path: Path) -> None:
    earlier = tmp_path / "earlier"
    later = tmp_path / "later"
    earlier.mkdir()
    later.mkdir()
    (earlier / "coding.md").write_text("user-global version", encoding="utf-8")
    (later / "coding.md").write_text("project version", encoding="utf-8")

    sm = SkillManager([earlier, later])
    sm.discover()
    assert sm.skills["coding"] == "project version"


def test_duplicate_directory_paths_are_deduped(tmp_path: Path) -> None:
    """When skills_dir happens to equal .loom/skills the same directory
    shouldn't be scanned twice."""
    d = tmp_path / ".loom" / "skills"
    d.mkdir(parents=True)
    (d / "x.md").write_text("hi", encoding="utf-8")
    sm = SkillManager([d, d, d])
    assert len(sm.directories) == 1
    sm.discover()
    assert sm.skills == {"x": "hi"}


def test_missing_directories_are_silently_skipped(tmp_path: Path) -> None:
    """SkillManager must tolerate non-existent search paths so a user can
    have a config that points at ~/.loom/skills even before they create it."""
    nonexistent = tmp_path / "no-such-dir"
    real = tmp_path / "real"
    real.mkdir()
    (real / "y.md").write_text("body", encoding="utf-8")
    sm = SkillManager([nonexistent, real])
    sm.discover()
    assert sm.skills == {"y": "body"}


def test_system_block_includes_all_skill_bodies(tmp_path: Path) -> None:
    d = tmp_path / "skills"
    d.mkdir()
    (d / "one.md").write_text("alpha content", encoding="utf-8")
    (d / "two.md").write_text("beta content", encoding="utf-8")
    sm = SkillManager([d])
    sm.discover()
    block = sm.system_block()
    assert "## Skill: one" in block
    assert "## Skill: two" in block
    assert "alpha content" in block
    assert "beta content" in block


def test_empty_skill_set_emits_empty_system_block() -> None:
    sm = SkillManager([])
    sm.discover()
    assert sm.system_block() == ""


def test_cli_skill_search_paths_include_visible_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End-to-end check: the path list LoomCLI gives to SkillManager must
    contain ./skills/ so a top-level coding.md is discovered automatically."""
    from loom.cli import LoomCLI
    from loom.config import LoomConfig

    # Drop us into a fresh project dir with a top-level skills/coding.md.
    project = tmp_path / "project"
    user_home = tmp_path / "home" / ".loom"
    (project / "skills").mkdir(parents=True)
    (project / "skills" / "coding.md").write_text("# coding\nbe careful", encoding="utf-8")
    user_home.mkdir(parents=True)
    monkeypatch.setattr("loom.cli.USER_HOME", user_home)
    monkeypatch.chdir(project)

    cfg = LoomConfig(provider="openrouter")
    cfg.openrouter.api_key = "x"  # so build_provider doesn't blow up

    cli = LoomCLI(cfg)
    names = cli._skills.discover()
    assert "coding" in names
    src = cli._skills.sources["coding"].resolve()
    assert src == (project / "skills" / "coding.md").resolve()