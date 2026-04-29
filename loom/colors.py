"""Terminal color/theme support for Loom.

Uses 24-bit ANSI escape codes. On Windows 10+ (and Windows 11) the default
console doesn't interpret ANSI by default, so on first use we flip the
``ENABLE_VIRTUAL_TERMINAL_PROCESSING`` flag via ``SetConsoleMode``. Linux and
macOS terminals interpret these natively.

The default ``dark`` theme mimics Claude Code's accent palette - the brand
``rgb(215,119,87)`` orange for prompts/banners, plus the dark-theme
success/error/warning/diff colors documented at:
https://blog.vincentqiao.com/en/posts/claude-code-theme/

Behavior:
  * ``color = "auto"`` (default): colors on if stdout is a TTY and ``NO_COLOR``
    is not set in the environment (https://no-color.org/ convention).
  * ``color = "on" | "true" | "dark" | "light"``: force-enable, optionally
    selecting a theme variant.
  * ``color = "off" | "false" | "none"``: never emit escape codes.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass


CSI = "\x1b["
RESET = f"{CSI}0m"


def _enable_windows_vt() -> bool:
    """Flip ENABLE_VIRTUAL_TERMINAL_PROCESSING on stdout's console handle.

    Returns True on success or on non-Windows platforms (where it's a no-op).
    Returns False if the call failed (e.g. stdout is redirected to a file).
    """
    if sys.platform != "win32":
        return True
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        STD_OUTPUT_HANDLE = -11
        ENABLE_VIRTUAL_TERMINAL_PROCESSING = 0x0004

        handle = kernel32.GetStdHandle(STD_OUTPUT_HANDLE)
        if handle in (0, -1):
            return False
        mode = wintypes.DWORD()
        if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            return False
        new_mode = mode.value | ENABLE_VIRTUAL_TERMINAL_PROCESSING
        return bool(kernel32.SetConsoleMode(handle, new_mode))
    except Exception:
        return False


def _supports_color(stream) -> bool:
    """Decide whether to emit ANSI codes for ``stream``."""
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("FORCE_COLOR"):
        return True
    isatty = getattr(stream, "isatty", None)
    return bool(isatty and isatty())


@dataclass(frozen=True)
class Theme:
    """An ANSI SGR theme.

    Each token is the *body* of an SGR escape (between ``\\x1b[`` and ``m``)
    or an empty string to disable that token.
    """

    name: str = "dark"
    brand: str = "38;2;215;119;87"      # Claude orange (rgb 215,119,87)
    text: str = ""                       # default fg - leave terminal default
    dim: str = "38;2;130;130;130"       # subtle hints, secondary labels
    success: str = "38;2;78;186;101"
    error: str = "38;2;255;107;128"
    warning: str = "38;2;240;186;0"
    info: str = "38;2;103;162;255"
    tool: str = "38;2;160;180;200"      # bluish-grey for [tool ...] lines
    bold: str = "1"


THEME_DARK = Theme(name="dark")

THEME_LIGHT = Theme(
    name="light",
    brand="38;2;215;119;87",
    text="",
    dim="38;2;100;100;100",
    success="38;2;44;122;57",
    error="38;2;171;43;63",
    warning="38;2;180;120;0",
    info="38;2;30;90;200",
    tool="38;2;90;110;130",
    bold="1",
)

THEME_NONE = Theme(
    name="none",
    brand="", text="", dim="", success="", error="",
    warning="", info="", tool="", bold="",
)


class Colors:
    """Singleton color emitter. Wrap strings with ``COLOR.brand("...")``,
    ``COLOR.error("...")`` etc. When colors are disabled all wrappers are
    no-ops, so callers don't have to branch."""

    def __init__(self) -> None:
        self._theme: Theme = THEME_NONE
        self._enabled: bool = False

    def configure(self, mode: str = "auto", *, stream=None) -> None:
        """Apply a config value. Idempotent.

        Accepted values (case-insensitive): ``auto``, ``on``/``true``,
        ``off``/``false``/``none``, ``dark``, ``light``.
        """
        m = (mode or "auto").strip().lower()
        out_stream = stream if stream is not None else sys.stdout

        if m in ("off", "false", "none", "no", "0"):
            self._enabled = False
            self._theme = THEME_NONE
            return

        if m == "auto":
            self._enabled = _supports_color(out_stream)
            theme = THEME_DARK
        elif m in ("on", "true", "yes", "1", "dark"):
            self._enabled = True
            theme = THEME_DARK
        elif m == "light":
            self._enabled = True
            theme = THEME_LIGHT
        else:
            # Unknown value: behave like auto rather than failing loudly.
            self._enabled = _supports_color(out_stream)
            theme = THEME_DARK

        if self._enabled:
            _enable_windows_vt()
            self._theme = theme
        else:
            self._theme = THEME_NONE

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def theme_name(self) -> str:
        return self._theme.name

    def wrap(self, code: str, s: str) -> str:
        if not self._enabled or not code or not s:
            return s
        return f"{CSI}{code}m{s}{RESET}"

    # --- semantic shortcuts -------------------------------------------------

    def brand(self, s: str) -> str:
        return self.wrap(self._theme.brand, s)

    def text(self, s: str) -> str:
        return self.wrap(self._theme.text, s)

    def dim(self, s: str) -> str:
        return self.wrap(self._theme.dim, s)

    def success(self, s: str) -> str:
        return self.wrap(self._theme.success, s)

    def error(self, s: str) -> str:
        return self.wrap(self._theme.error, s)

    def warning(self, s: str) -> str:
        return self.wrap(self._theme.warning, s)

    def info(self, s: str) -> str:
        return self.wrap(self._theme.info, s)

    def tool(self, s: str) -> str:
        return self.wrap(self._theme.tool, s)

    def bold(self, s: str) -> str:
        return self.wrap(self._theme.bold, s)


COLOR = Colors()
