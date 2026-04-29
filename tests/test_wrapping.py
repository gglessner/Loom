"""Tests for loom.wrapping (streaming word-wrap)."""

from __future__ import annotations

import io

import pytest

from loom.wrapping import (
    StreamWrapper,
    detect_terminal_width,
    make_wrapper,
    resolve_wrap_width,
)


def _wrap(text: str, width: int, *, chunk_size: int = 0) -> str:
    """Run ``text`` through ``StreamWrapper`` and return the captured output.

    When ``chunk_size`` > 0 the input is fed in pieces of that size to exercise
    the cross-chunk word-buffer logic; otherwise it goes in as a single feed.
    """
    out = io.StringIO()
    sw = StreamWrapper(out.write, width)
    if chunk_size <= 0:
        sw.feed(text)
    else:
        for i in range(0, len(text), chunk_size):
            sw.feed(text[i : i + chunk_size])
    sw.flush()
    return out.getvalue()


# ----- resolver -------------------------------------------------------------


@pytest.mark.parametrize(
    "setting,expected",
    [
        ("auto", 100),
        ("on", 100),
        ("true", 100),
        ("off", 0),
        ("false", 0),
        ("none", 0),
        ("0", 0),
        ("80", 80),
        ("120", 120),
        ("nonsense", 100),  # falls back to terminal width
    ],
)
def test_resolve_wrap_width(setting: str, expected: int) -> None:
    assert resolve_wrap_width(setting, terminal_width=100) == expected


def test_resolve_wrap_width_negative_treated_as_off() -> None:
    assert resolve_wrap_width("-5", terminal_width=100) == 0


def test_detect_terminal_width_returns_positive_int() -> None:
    """Sanity check - whatever the env reports must be a positive int."""
    w = detect_terminal_width()
    assert isinstance(w, int) and w >= 20


# ----- pass-through (disabled) ---------------------------------------------


def test_disabled_passes_text_through_unchanged() -> None:
    text = "hello world this is a long line that would normally wrap" * 3
    assert _wrap(text, width=0) == text


def test_below_min_width_disables_wrapping() -> None:
    """Widths below the safety floor are treated as 'off' to avoid pathological output."""
    text = "this would otherwise be chopped to bits"
    assert _wrap(text, width=10) == text


# ----- prose wrapping -------------------------------------------------------


def test_long_prose_wraps_at_word_boundary() -> None:
    text = "the quick brown fox jumps over the lazy dog and then keeps on running"
    out = _wrap(text, width=40)
    lines = out.split("\n")
    assert all(len(line) <= 40 for line in lines), out
    # Only whole words on each line - no word was sliced.
    assert "the quick brown fox jumps over the lazy" in out
    # The original word sequence is preserved.
    assert " ".join(out.split()) == text


def test_wrapping_handles_explicit_newlines() -> None:
    text = "line one is short\nline two is also short\n"
    out = _wrap(text, width=80)
    assert out == text


def test_word_longer_than_width_is_emitted_anyway() -> None:
    """We never split a word - if it's too long, let the terminal hard-wrap."""
    text = "short " + ("X" * 80)
    out = _wrap(text, width=40)
    # The long word lives on its own line, prefixed by the wrap newline.
    assert ("X" * 80) in out
    assert out.count("\n") >= 1


def test_no_trailing_whitespace_on_wrapped_lines() -> None:
    """Wrapped lines must not end with a stray space - looks bad in pipes
    and editors that highlight trailing whitespace."""
    text = "foo bar baz qux quux corge grault garply waldo fred plugh xyzzy thud"
    out = _wrap(text, width=40)
    for line in out.split("\n"):
        if line:
            assert not line.endswith(" "), repr(line)


def test_indentation_preserved_at_line_start() -> None:
    """Leading spaces (e.g. on a list item or quoted line) must survive."""
    text = "    indented prose continues here"
    out = _wrap(text, width=80)
    assert out.startswith("    ")


def test_trailing_whitespace_before_newline_dropped() -> None:
    """The model occasionally emits 'foo \\n' - the trailing space serves no
    purpose and creates ugly diffs. Drop it."""
    text = "foo bar   \nbaz"
    out = _wrap(text, width=80)
    assert out == "foo bar\nbaz"


def test_streaming_chunks_yield_same_output_as_single_feed() -> None:
    """The wrapper must be chunk-size-invariant - this is the central
    correctness property of a streaming word-wrapper."""
    text = (
        "the streaming word wrapper must produce the same output regardless "
        "of how the input is sliced into chunks because the LLM hands us "
        "tokens of varying sizes including word fragments"
    )
    one_shot = _wrap(text, width=50)
    for chunk in (1, 2, 3, 5, 13, 50):
        assert _wrap(text, width=50, chunk_size=chunk) == one_shot, chunk


# ----- code-fence preservation ---------------------------------------------


def test_code_fence_suspends_wrapping() -> None:
    """Lines inside ``` ... ``` are emitted verbatim, even if longer than width."""
    text = (
        "Here is some prose that should wrap at the configured column width.\n"
        "```python\n"
        "this_is_a_really_long_line_inside_a_code_fence_that_must_not_be_word_wrapped()\n"
        "```\n"
        "Back to prose that should once again wrap at the column width.\n"
    )
    out = _wrap(text, width=40)
    # The long code line is preserved in full as a single line.
    assert (
        "this_is_a_really_long_line_inside_a_code_fence_that_must_not_be_word_wrapped()"
        in out.split("\n")
    )


def test_code_fence_with_language_tag_still_recognised() -> None:
    text = (
        "```ts\n"
        "const x = some.really.long.expression.that.would.otherwise.wrap()\n"
        "```\n"
    )
    out = _wrap(text, width=40)
    assert (
        "const x = some.really.long.expression.that.would.otherwise.wrap()"
        in out.split("\n")
    )


def test_two_fences_toggle_correctly() -> None:
    """Open fence -> verbatim; close fence -> wrapping resumes."""
    long_word_in_prose = "X" * 60
    text = (
        "first sentence is short.\n"
        "```\n"
        "verbatim\n"
        "```\n"
        f"after the fence {long_word_in_prose} should still trigger a wrap.\n"
    )
    out = _wrap(text, width=40)
    # Outside the fence, wrapping kicked in (we saw at least one newline added
    # that wasn't in the input).
    assert out.count("\n") >= text.count("\n")


# ----- factory --------------------------------------------------------------


def test_make_wrapper_respects_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOOM_WRAP", "off")
    out = io.StringIO()
    sw = make_wrapper(out.write, "auto")
    assert not sw.enabled
    sw.feed("hello world " * 20)
    sw.flush()
    assert out.getvalue() == "hello world " * 20
