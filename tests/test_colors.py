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


def test_dark_theme_uses_claude_brand_orange(monkeypatch: pytest.MonkeyPatch) -> None:
    """The dark-theme brand color must be Claude's rgb(215,119,87) when the
    terminal supports 24-bit. (Apple Terminal gets the 256-color fallback,
    covered by separate tests below.)"""
    monkeypatch.setenv("COLORTERM", "truecolor")
    monkeypatch.delenv("TERM_PROGRAM", raising=False)
    c = Colors()
    c.configure("dark")
    assert c.theme_name == "dark"
    s = c.brand("X")
    assert "215;119;87" in s


def test_light_theme_uses_light_palette(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COLORTERM", "truecolor")
    monkeypatch.delenv("TERM_PROGRAM", raising=False)
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


# ----- truecolor capability + 256-color fallback ---------------------------


def _no_terminal_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Strip every env var that influences truecolor detection."""
    for var in ("LOOM_TRUECOLOR", "COLORTERM", "TERM_PROGRAM"):
        monkeypatch.delenv(var, raising=False)


def test_apple_terminal_falls_back_to_256_color(monkeypatch: pytest.MonkeyPatch) -> None:
    """Apple Terminal.app does not render 24-bit RGB faithfully - we MUST
    emit ``38;5;N`` (256-color) sequences for it instead, otherwise our
    orange brand reads as a green blob."""
    _no_terminal_env(monkeypatch)
    monkeypatch.setenv("TERM_PROGRAM", "Apple_Terminal")
    c = Colors()
    c.configure("on")
    assert c.theme_name == "dark-256"
    s = c.brand("X")
    assert "38;5;173" in s   # the 256-color slot for our orange
    assert "38;2;" not in s  # no 24-bit codes


def test_colorterm_truecolor_unlocks_24bit(monkeypatch: pytest.MonkeyPatch) -> None:
    _no_terminal_env(monkeypatch)
    monkeypatch.setenv("COLORTERM", "truecolor")
    c = Colors()
    c.configure("on")
    assert c.theme_name == "dark"
    assert "38;2;215;119;87" in c.brand("X")


def test_colorterm_24bit_is_also_recognised(monkeypatch: pytest.MonkeyPatch) -> None:
    _no_terminal_env(monkeypatch)
    monkeypatch.setenv("COLORTERM", "24bit")
    c = Colors()
    c.configure("on")
    assert c.theme_name == "dark"


def test_loom_truecolor_env_forces_24bit(monkeypatch: pytest.MonkeyPatch) -> None:
    """User can force 24-bit even on Apple Terminal if they want to test."""
    _no_terminal_env(monkeypatch)
    monkeypatch.setenv("TERM_PROGRAM", "Apple_Terminal")
    monkeypatch.setenv("LOOM_TRUECOLOR", "1")
    c = Colors()
    c.configure("on")
    assert c.theme_name == "dark"
    assert "38;2;215;119;87" in c.brand("X")


def test_loom_truecolor_env_forces_256_color(monkeypatch: pytest.MonkeyPatch) -> None:
    """User can force 256-color even on iTerm2 if they're scripting screenshots."""
    _no_terminal_env(monkeypatch)
    monkeypatch.setenv("COLORTERM", "truecolor")
    monkeypatch.setenv("LOOM_TRUECOLOR", "0")
    c = Colors()
    c.configure("on")
    assert c.theme_name == "dark-256"


def test_light_theme_also_has_256_color_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    _no_terminal_env(monkeypatch)
    monkeypatch.setenv("TERM_PROGRAM", "Apple_Terminal")
    c = Colors()
    c.configure("light")
    assert c.theme_name == "light-256"
    # Verify a non-trivial token came through correctly.
    assert "38;5;" in c.error("E")
    assert "38;2;" not in c.error("E")


def test_unknown_terminal_assumes_truecolor(monkeypatch: pytest.MonkeyPatch) -> None:
    """If we can't detect anything, assume the terminal is modern - the long
    tail of 24-bit-incapable terminals is small and shrinking."""
    _no_terminal_env(monkeypatch)
    c = Colors()
    c.configure("on")
    assert c.theme_name == "dark"
