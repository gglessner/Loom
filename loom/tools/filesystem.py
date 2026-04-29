"""Cross-platform filesystem tools (pure stdlib)."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from .registry import BuiltinTool


# Cap how much content we ever return so a stray ``read_file`` on a binary
# blob doesn't blow up the model's context window.
_MAX_READ_BYTES = 200_000


def _read_file(args: dict[str, Any]) -> str:
    path = args.get("path", "")
    if not path:
        return "[error] 'path' is required"
    p = Path(path)
    if not p.exists():
        return f"File not found: {path}"
    if not p.is_file():
        return f"Path is not a file: {path}"
    try:
        data = p.read_bytes()
    except PermissionError:
        return f"Permission denied: {path}"
    truncated = len(data) > _MAX_READ_BYTES
    if truncated:
        data = data[:_MAX_READ_BYTES]
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        text = data.decode("latin-1")
    if truncated:
        text += f"\n\n[... truncated at {_MAX_READ_BYTES} bytes ...]"
    return text


def _write_file(args: dict[str, Any]) -> str:
    path = args.get("path", "")
    content = args.get("content", "")
    if not path:
        return "[error] 'path' is required"
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    try:
        # Write bytes so we don't get Windows' implicit \n -> \r\n translation.
        p.write_bytes(content.encode("utf-8"))
    except Exception as e:
        return f"Error writing {path}: {e}"
    return f"Wrote {len(content)} chars ({content.count(chr(10)) + 1} lines) to {path}"


def _edit_file(args: dict[str, Any]) -> str:
    """Replace exactly one occurrence of ``old`` with ``new`` in ``path``.

    This intentionally fails if ``old`` is not unique - the model has to give
    enough context to identify the spot, which prevents accidental sweeping
    edits and matches how Cursor/Claude editing tools work.
    """
    path = args.get("path", "")
    old = args.get("old", "")
    new = args.get("new", "")
    if not path or old == "":
        return "[error] 'path' and non-empty 'old' are required"
    p = Path(path)
    if not p.exists():
        return f"File not found: {path}"
    text = p.read_bytes().decode("utf-8")
    count = text.count(old)
    if count == 0:
        return f"'old' string not found in {path}"
    if count > 1:
        return (
            f"'old' string appears {count} times in {path}; provide more "
            "surrounding context so it matches exactly once."
        )
    p.write_bytes(text.replace(old, new, 1).encode("utf-8"))
    return f"Edited {path} (replaced 1 occurrence)"


def _list_dir(args: dict[str, Any]) -> str:
    path = args.get("path", ".")
    detail = bool(args.get("detail", False))
    p = Path(path)
    if not p.exists():
        return f"Path not found: {path}"
    if not p.is_dir():
        return f"Path is not a directory: {path}"
    rows: list[str] = []
    for entry in sorted(p.iterdir()):
        if detail:
            try:
                stat = entry.stat()
                kind = "dir " if entry.is_dir() else "file"
                rows.append(f"{kind}  {stat.st_size:>12,}  {entry.name}")
            except PermissionError:
                rows.append(f"???   {'?':>12}  {entry.name}")
        else:
            suffix = "/" if entry.is_dir() else ""
            rows.append(f"{entry.name}{suffix}")
    return "\n".join(rows) if rows else "(empty)"


def _tree(args: dict[str, Any]) -> str:
    path = args.get("path", ".")
    max_depth = int(args.get("max_depth", 3))
    p = Path(path)
    if not p.exists() or not p.is_dir():
        return f"Path not found or not a directory: {path}"

    lines: list[str] = []

    def walk(current: Path, depth: int) -> None:
        try:
            children = sorted(current.iterdir())
        except PermissionError:
            return
        for child in children:
            indent = "  " * depth
            lines.append(f"{indent}{'D' if child.is_dir() else 'F'} {child.name}")
            if child.is_dir() and depth + 1 < max_depth:
                walk(child, depth + 1)

    walk(p, 0)
    return "\n".join(lines) if lines else "(empty)"


def _file_info(args: dict[str, Any]) -> str:
    path = args.get("path", "")
    if not path:
        return "[error] 'path' is required"
    p = Path(path)
    if not p.exists():
        return f"{path} does not exist"
    stat = p.stat()
    return json.dumps(
        {
            "path": str(p),
            "is_file": p.is_file(),
            "is_dir": p.is_dir(),
            "size_bytes": stat.st_size,
            "modified": stat.st_mtime,
        },
        indent=2,
    )


def _copy(args: dict[str, Any]) -> str:
    src = Path(args.get("source", ""))
    dst = Path(args.get("destination", ""))
    if not src.exists():
        return f"Source not found: {src}"
    dst.parent.mkdir(parents=True, exist_ok=True)
    if src.is_dir():
        shutil.copytree(src, dst, dirs_exist_ok=True)
    else:
        shutil.copy2(src, dst)
    return f"Copied {src} -> {dst}"


def _move(args: dict[str, Any]) -> str:
    src = Path(args.get("source", ""))
    dst = Path(args.get("destination", ""))
    if not src.exists():
        return f"Source not found: {src}"
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(dst))
    return f"Moved {src} -> {dst}"


def _delete(args: dict[str, Any]) -> str:
    path = Path(args.get("path", ""))
    if not path.exists():
        return f"Not found: {path}"
    if path.is_dir():
        shutil.rmtree(path, ignore_errors=False)
        return f"Deleted directory: {path}"
    path.unlink()
    return f"Deleted file: {path}"


def _mkdir(args: dict[str, Any]) -> str:
    path = Path(args.get("path", ""))
    path.mkdir(parents=True, exist_ok=True)
    return f"Directory ensured: {path}"


TOOLS: list[BuiltinTool] = [
    BuiltinTool(
        name="read_file",
        description="Read a UTF-8 text file. Returns its contents (truncated if very large).",
        input_schema={
            "type": "object",
            "properties": {"path": {"type": "string", "description": "File path."}},
            "required": ["path"],
        },
        handler=_read_file,
    ),
    BuiltinTool(
        name="write_file",
        description="Write or overwrite a UTF-8 file. Creates parent directories as needed.",
        input_schema={
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "content": {"type": "string"},
            },
            "required": ["path", "content"],
        },
        handler=_write_file,
    ),
    BuiltinTool(
        name="edit_file",
        description=(
            "Replace exactly one occurrence of 'old' with 'new' in the file. "
            "Fails if 'old' is not unique; include enough surrounding context "
            "to make it unique."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "old": {"type": "string", "description": "Exact text to replace."},
                "new": {"type": "string", "description": "Replacement text."},
            },
            "required": ["path", "old", "new"],
        },
        handler=_edit_file,
    ),
    BuiltinTool(
        name="list_dir",
        description="List entries in a directory. Set detail=true for sizes and types.",
        input_schema={
            "type": "object",
            "properties": {
                "path": {"type": "string", "default": "."},
                "detail": {"type": "boolean", "default": False},
            },
        },
        handler=_list_dir,
    ),
    BuiltinTool(
        name="tree",
        description="Show a directory tree up to max_depth (default 3).",
        input_schema={
            "type": "object",
            "properties": {
                "path": {"type": "string", "default": "."},
                "max_depth": {"type": "integer", "default": 3, "minimum": 1, "maximum": 8},
            },
        },
        handler=_tree,
    ),
    BuiltinTool(
        name="file_info",
        description="Return JSON metadata (size, type, mtime) for a path.",
        input_schema={
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
        handler=_file_info,
    ),
    BuiltinTool(
        name="copy",
        description="Copy a file or directory tree from source to destination.",
        input_schema={
            "type": "object",
            "properties": {
                "source": {"type": "string"},
                "destination": {"type": "string"},
            },
            "required": ["source", "destination"],
        },
        handler=_copy,
    ),
    BuiltinTool(
        name="move",
        description="Move or rename a file or directory.",
        input_schema={
            "type": "object",
            "properties": {
                "source": {"type": "string"},
                "destination": {"type": "string"},
            },
            "required": ["source", "destination"],
        },
        handler=_move,
    ),
    BuiltinTool(
        name="delete",
        description="Delete a file or directory (recursively).",
        input_schema={
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
        handler=_delete,
    ),
    BuiltinTool(
        name="mkdir",
        description="Create a directory (and parents if needed).",
        input_schema={
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
        handler=_mkdir,
    ),
]
