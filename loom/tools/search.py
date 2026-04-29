"""grep-style search across a directory tree (pure stdlib)."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .registry import BuiltinTool


_BINARY_HINT = b"\x00"
_MAX_FILE_BYTES = 5_000_000  # skip files larger than this


def _is_probably_binary(p: Path) -> bool:
    try:
        with p.open("rb") as f:
            head = f.read(4096)
    except OSError:
        return True
    return _BINARY_HINT in head


def _grep(args: dict[str, Any]) -> str:
    pattern = args.get("pattern", "")
    if not pattern:
        return "[error] 'pattern' is required"
    directory = Path(args.get("directory", "."))
    glob = args.get("glob") or "*"
    case_sensitive = bool(args.get("case_sensitive", False))
    max_results = int(args.get("max_results", 200))
    flags = 0 if case_sensitive else re.IGNORECASE
    try:
        regex = re.compile(pattern, flags)
    except re.error as e:
        return f"[error] invalid regex: {e}"

    if not directory.exists() or not directory.is_dir():
        return f"Directory not found: {directory}"

    hits: list[str] = []
    for fp in sorted(directory.rglob(glob)):
        if not fp.is_file():
            continue
        try:
            if fp.stat().st_size > _MAX_FILE_BYTES:
                continue
        except OSError:
            continue
        if _is_probably_binary(fp):
            continue
        try:
            with fp.open("r", encoding="utf-8", errors="ignore") as f:
                for n, line in enumerate(f, 1):
                    if regex.search(line):
                        hits.append(f"{fp}:{n}: {line.rstrip()}")
                        if len(hits) >= max_results:
                            hits.append(f"[... truncated at {max_results} matches ...]")
                            return "\n".join(hits)
        except OSError:
            continue

    return "\n".join(hits) if hits else f"No matches for {pattern!r}"


def _find_files(args: dict[str, Any]) -> str:
    directory = Path(args.get("directory", "."))
    glob = args.get("glob") or "*"
    max_results = int(args.get("max_results", 500))
    if not directory.exists() or not directory.is_dir():
        return f"Directory not found: {directory}"
    hits: list[str] = []
    for fp in sorted(directory.rglob(glob)):
        hits.append(str(fp))
        if len(hits) >= max_results:
            hits.append(f"[... truncated at {max_results} entries ...]")
            break
    return "\n".join(hits) if hits else "(no matches)"


TOOLS: list[BuiltinTool] = [
    BuiltinTool(
        name="grep",
        description="Recursively search files for a regex. Skips binary files.",
        input_schema={
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "Python regex."},
                "directory": {"type": "string", "default": "."},
                "glob": {
                    "type": "string",
                    "default": "*",
                    "description": "Filename glob, e.g. '*.py'.",
                },
                "case_sensitive": {"type": "boolean", "default": False},
                "max_results": {"type": "integer", "default": 200},
            },
            "required": ["pattern"],
        },
        handler=_grep,
    ),
    BuiltinTool(
        name="find_files",
        description="Recursively list files matching a glob. Cross-platform.",
        input_schema={
            "type": "object",
            "properties": {
                "directory": {"type": "string", "default": "."},
                "glob": {"type": "string", "default": "*"},
                "max_results": {"type": "integer", "default": 500},
            },
        },
        handler=_find_files,
    ),
]
