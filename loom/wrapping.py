"""Streaming word-wrapper for LLM text output.

The Anthropic / OpenAI-compatible streaming APIs hand us text in arbitrarily
sized chunks. A naive ``stdout.write(chunk)`` lets the terminal hard-wrap at
the column boundary, often slicing words in half. This module provides a
``StreamWrapper`` that:

  * buffers partial words across chunks,
  * inserts a soft newline before any word that would overflow the configured
    width,
  * suspends wrapping inside fenced code blocks (``` ... ```) so source code
    layout is preserved verbatim,
  * forwards everything else (existing newlines, leading whitespace) untouched.

The wrapper is intentionally synchronous and stateless beyond a single line -
construct one per agent turn so it picks up the current terminal width.
"""

from __future__ import annotations

import os
import shutil
from typing import Callable


# Below this width wrapping does more harm than good (terminals get angry,
# words barely fit). Treat very small widths as "off".
_MIN_WRAP_WIDTH = 40

# Fallback when ``shutil.get_terminal_size`` can't determine the real width
# (e.g. stdout redirected to a file). Comfortable for prose, not so wide that
# diff-style tool output looks weird.
_DEFAULT_WIDTH = 100


def detect_terminal_width(default: int = _DEFAULT_WIDTH) -> int:
    """Return the current terminal column count, or ``default`` if unknown."""
    try:
        cols = shutil.get_terminal_size((default, 24)).columns
    except Exception:
        cols = default
    return max(int(cols), 20)


def resolve_wrap_width(setting: str, *, terminal_width: int) -> int:
    """Translate a user-facing ``wrap`` setting to an integer column count.

    Returns 0 to mean "no wrapping". Accepted values:
      * ``"auto"`` (default) -> the current terminal width
      * ``"off"`` / ``"false"`` / ``"none"`` / ``"0"`` -> 0 (no wrapping)
      * any positive integer string -> that fixed column count
      * unrecognised values fall back to ``"auto"`` so a typo never breaks
        rendering.
    """
    s = (setting or "auto").strip().lower()
    if s in ("off", "false", "none", "no", "0"):
        return 0
    if s in ("auto", "on", "true", "yes"):
        return terminal_width
    try:
        n = int(s)
    except ValueError:
        return terminal_width
    if n <= 0:
        return 0
    return n


class StreamWrapper:
    """Word-wrapping write-through filter for streaming text.

    Construct with a ``write_fn`` that takes a string and a ``width``. Width
    of 0 disables wrapping (the wrapper becomes a transparent pass-through
    so callers don't have to special-case it).
    """

    def __init__(self, write_fn: Callable[[str], None], width: int) -> None:
        self._write = write_fn
        self._width = max(width, 0)
        self._enabled = self._width >= _MIN_WRAP_WIDTH
        self._col = 0  # current cursor column on the in-progress line
        self._word: list[str] = []  # buffer for the current partial word
        # Whitespace that follows the previous word but precedes the next.
        # We hold it back until we know whether the next word fits, so that
        # wrapped lines don't leave a stray trailing space behind. Inside
        # code fences this buffer stays empty - whitespace is written
        # verbatim there.
        self._pending_ws: list[str] = []
        self._line_so_far: list[str] = []  # current line, for ``` detection
        self._in_code_fence = False

    @property
    def enabled(self) -> bool:
        return self._enabled

    # ----- public API --------------------------------------------------------

    def feed(self, text: str) -> None:
        """Consume a streaming chunk."""
        if not text:
            return
        if not self._enabled:
            self._write(text)
            return
        for ch in text:
            self._consume(ch)

    def flush(self) -> None:
        """Emit any buffered partial word. Call once when streaming ends."""
        if self._word:
            self._emit_word()

    # ----- internal ---------------------------------------------------------

    def _consume(self, ch: str) -> None:
        if ch == "\n":
            self._emit_word()
            # Trailing whitespace on a line is invisible anyway and looks
            # untidy when the output is piped to a file - drop it.
            self._pending_ws = []
            self._write("\n")
            self._maybe_toggle_fence()
            self._col = 0
            self._line_so_far = []
            return

        if ch == "\r":
            self._emit_word()
            self._pending_ws = []
            self._write(ch)
            self._col = 0
            return

        if ch == " " or ch == "\t":
            # Word boundary - flush any buffered word first.
            self._emit_word()
            if self._in_code_fence:
                self._write(ch)
                self._col += 4 if ch == "\t" else 1
                self._line_so_far.append(ch)
                return
            # Outside fences, hold spaces until we know whether the next
            # word will share this line. _emit_word decides.
            self._pending_ws.append(ch)
            self._line_so_far.append(ch)
            return

        self._word.append(ch)
        self._line_so_far.append(ch)

    def _ws_width(self, chars: list[str]) -> int:
        return sum(4 if c == "\t" else 1 for c in chars)

    def _emit_word(self) -> None:
        if not self._word:
            return
        word = "".join(self._word)
        self._word = []
        if self._in_code_fence:
            self._write(word)
            self._col += len(word)
            return

        ws = self._pending_ws
        self._pending_ws = []
        ws_width = self._ws_width(ws)

        if self._col > 0 and self._col + ws_width + len(word) > self._width:
            # Wrap before the word; the pending whitespace would otherwise
            # become an invisible trailing run, so we drop it.
            self._write("\n")
            self._col = 0
        elif ws:
            # Either we're at the start of a line (preserve indentation) or
            # the word still fits on this line - in both cases the spaces
            # belong in the output.
            self._write("".join(ws))
            self._col += ws_width

        self._write(word)
        self._col += len(word)

    def _maybe_toggle_fence(self) -> None:
        """At end-of-line, check whether the line we just printed was a
        code-fence delimiter (`` ``` `` possibly with a language tag). The
        delimiter is the *first* non-whitespace token on the line."""
        line = "".join(self._line_so_far).lstrip()
        if line.startswith("```"):
            self._in_code_fence = not self._in_code_fence


def make_wrapper(write_fn: Callable[[str], None], setting: str) -> StreamWrapper:
    """Convenience factory: resolves the user-facing setting and snapshots
    the current terminal width."""
    if os.environ.get("LOOM_WRAP"):
        setting = os.environ["LOOM_WRAP"]
    width = resolve_wrap_width(setting, terminal_width=detect_terminal_width())
    return StreamWrapper(write_fn, width)
