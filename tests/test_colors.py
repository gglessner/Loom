"""Tests for loom.colors."""

from __future__ import annotations

import io

import pytest

from loom.colors import COLOR, Colors


def _no_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Strip NO_COLOR / FORCE_COLOR so the dev's shell doesn't poison tests."""
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.delenv("FORCE_COLOR", raising=False)


def test_default_state_is_disabled() -> None:
    """A freshly constructed Colors emits no escape codes until configured."""
    c = Colors()
    assert not c.enabled
    assert c.brand("hello") == "hello"
    assert c.error("hello") == "hello"
    assert c.dim("hello") == "hello"


@pytest.mark.parametrize("off", ["off", "false", "none", "no", "0", "OFF", "False"])
def test_off_modes_disable_colors(off: str) -> None:
    c = Colors()
    c.configure(off)
    assert not c.enabled
    assert c.brand("hi") == "hi"
    assert c.error("hi") == "hi"


def test_explicit_on_emits_escape_codes() -> None:
    c = Colors()
    c.configure("on")
    assert c.enabled
    s = c.brand("hi")
    assert s.startswith("\x1b[")
    assert s.endswith("\x1b[0m")
    assert "hi" in s


def test_dark_theme_uses_claude_brand_orange() -> None:
    """The dark-theme brand color must be Claude's rgb(215,119,87)."""
    c = Colors()
    c.configure("dark")
    assert c.theme_name == "dark"
    s = c.brand("X")
    assert "215;119;87" in s


def test_light_theme_uses_light_palette() -> None:
    c = Colors()
    c.configure("light")
    assert c.theme_name == "light"
    err = c.error("E")
    assert "171;43;63" in err


def test_no_color_env_disables_auto(monkeypatch: pytest.MonkeyPatch) -> None:
    """https://no-color.org/ - if NO_COLOR is set, never colorize on auto."""
    monkeypatch.setenv("NO_COLOR", "1")
    monkeypatch.delenv("FORCE_COLOR", raising=False)
    fake = io.StringIO()
    fake.isatty = lambda: True  # type: ignore[attr-defined]
    c = Colors()
    c.configure("auto", stream=fake)
    assert not c.enabled


def test_force_color_env_enables_non_tty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setenv("FORCE_COLOR", "1")
    fake = io.StringIO()
    c = Colors()
    c.configure("auto", stream=fake)
    assert c.enabled


def test_auto_disabled_when_not_a_tty(monkeypatch: pytest.MonkeyPatch) -> None:
    _no_env(monkeypatch)
    fake = io.StringIO()  # StringIO.isatty() returns False
    c = Colors()
    c.configure("auto", stream=fake)
    assert not c.enabled


def test_unknown_value_falls_back_to_auto(monkeypatch: pytest.MonkeyPatch) -> None:
    """A typo in `color =` should not raise - we fall back to auto behavior."""
    _no_env(monkeypatch)
    fake = io.StringIO()
    c = Colors()
    c.configure("magenta-purple", stream=fake)
    assert not c.enabled


def test_wrap_with_empty_string_is_unchanged() -> None:
    c = Colors()
    c.configure("on")
    assert c.brand("") == ""


def test_module_singleton_exists() -> None:
    """`COLOR` is exposed at module scope so callers can import it directly."""
    assert isinstance(COLOR, Colors)
