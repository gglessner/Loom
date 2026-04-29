"""Shell + Python execution tools.

On Windows we run shell commands via ``cmd.exe``; everywhere else via
``/bin/sh``. Both honour a timeout and capture stdout+stderr.

For Python we always invoke ``sys.executable`` so the model uses the same
interpreter Loom is running under (no surprises with python vs. python3).
"""

from __future__ import annotations

import os
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any

from .registry import BuiltinTool


_OUTPUT_CAP = 50_000  # bytes


def _truncate(text: str) -> str:
    if len(text) <= _OUTPUT_CAP:
        return text
    return text[:_OUTPUT_CAP] + f"\n[... output truncated at {_OUTPUT_CAP} chars ...]"


def _run_shell(args: dict[str, Any]) -> str:
    command = args.get("command", "")
    if not command:
        return "[error] 'command' is required"
    cwd = args.get("cwd") or None
    timeout = int(args.get("timeout", 60))

    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=cwd,
        )
    except subprocess.TimeoutExpired:
        return f"[timeout after {timeout}s] {command}"
    except FileNotFoundError as e:
        return f"[not found] {e}"

    out = result.stdout or ""
    if result.stderr:
        out += ("\n" if out else "") + f"[stderr]\n{result.stderr}"
    if result.returncode != 0:
        out = f"[exit {result.returncode}]\n{out}"
    return _truncate(out) if out else "(no output)"


def _run_python(args: dict[str, Any]) -> str:
    code = args.get("code", "")
    if not code:
        return "[error] 'code' is required"
    cwd = args.get("cwd") or None
    timeout = int(args.get("timeout", 60))

    try:
        result = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=cwd,
        )
    except subprocess.TimeoutExpired:
        return f"[timeout after {timeout}s]"

    out = result.stdout or ""
    if result.stderr:
        out += ("\n" if out else "") + f"[stderr]\n{result.stderr}"
    if result.returncode != 0:
        out = f"[exit {result.returncode}]\n{out}"
    return _truncate(out) if out else "(no output)"


def _platform_info(_args: dict[str, Any]) -> str:
    import platform
    return (
        f"system={platform.system()}\n"
        f"release={platform.release()}\n"
        f"machine={platform.machine()}\n"
        f"python={sys.version.split()[0]}\n"
        f"executable={sys.executable}\n"
        f"cwd={Path.cwd()}\n"
        f"sep={os.sep!r}\n"
    )


TOOLS: list[BuiltinTool] = [
    BuiltinTool(
        name="run_shell",
        description=(
            "Run a shell command (cmd.exe on Windows, /bin/sh elsewhere). "
            "Captures stdout+stderr; output is truncated at 50KB."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "command": {"type": "string"},
                "cwd": {"type": "string", "description": "Working directory."},
                "timeout": {"type": "integer", "default": 60, "minimum": 1},
            },
            "required": ["command"],
        },
        handler=_run_shell,
    ),
    BuiltinTool(
        name="run_python",
        description="Execute a Python snippet using Loom's interpreter and return stdout.",
        input_schema={
            "type": "object",
            "properties": {
                "code": {"type": "string"},
                "cwd": {"type": "string"},
                "timeout": {"type": "integer", "default": 60, "minimum": 1},
            },
            "required": ["code"],
        },
        handler=_run_python,
    ),
    BuiltinTool(
        name="platform_info",
        description="Return basic platform info (OS, Python version, cwd, path separator).",
        input_schema={"type": "object", "properties": {}},
        handler=_platform_info,
    ),
]
