"""Exercise every builtin tool against a tmp_path. Pure, no network."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from loom.tools.registry import ToolRegistry, builtin_tools


@pytest.fixture()
def registry() -> ToolRegistry:
    r = ToolRegistry()
    r.register_many(builtin_tools())
    return r


def test_filesystem_round_trip(tmp_path: Path, registry: ToolRegistry) -> None:
    target = tmp_path / "sub" / "hello.txt"
    out = registry.execute("write_file", {"path": str(target), "content": "alpha\nbeta"})
    assert "Wrote" in out
    assert target.read_text(encoding="utf-8") == "alpha\nbeta"

    out = registry.execute("read_file", {"path": str(target)})
    assert out == "alpha\nbeta"

    out = registry.execute("file_info", {"path": str(target)})
    info = json.loads(out)
    assert info["is_file"] and info["size_bytes"] == len("alpha\nbeta")

    out = registry.execute(
        "edit_file", {"path": str(target), "old": "beta", "new": "gamma"}
    )
    assert "Edited" in out
    assert target.read_text(encoding="utf-8") == "alpha\ngamma"

    # Edit fails when old isn't unique
    target.write_text("x\nx", encoding="utf-8")
    out = registry.execute("edit_file", {"path": str(target), "old": "x", "new": "y"})
    assert "appears 2 times" in out


def test_listing_and_tree(tmp_path: Path, registry: ToolRegistry) -> None:
    (tmp_path / "a.txt").write_text("a", encoding="utf-8")
    (tmp_path / "b").mkdir()
    (tmp_path / "b" / "c.txt").write_text("c", encoding="utf-8")

    out = registry.execute("list_dir", {"path": str(tmp_path), "detail": True})
    assert "a.txt" in out and "b" in out

    out = registry.execute("tree", {"path": str(tmp_path), "max_depth": 3})
    assert "a.txt" in out and "c.txt" in out


def test_copy_move_delete(tmp_path: Path, registry: ToolRegistry) -> None:
    src = tmp_path / "x.txt"
    src.write_text("hi", encoding="utf-8")
    dst = tmp_path / "nested" / "y.txt"
    registry.execute("copy", {"source": str(src), "destination": str(dst)})
    assert dst.read_text(encoding="utf-8") == "hi"

    moved = tmp_path / "elsewhere" / "z.txt"
    registry.execute("move", {"source": str(dst), "destination": str(moved)})
    assert moved.exists() and not dst.exists()

    registry.execute("delete", {"path": str(moved)})
    assert not moved.exists()


def test_grep_and_find(tmp_path: Path, registry: ToolRegistry) -> None:
    (tmp_path / "foo.py").write_text("def needle():\n    pass\n", encoding="utf-8")
    (tmp_path / "bar.py").write_text("# unrelated\n", encoding="utf-8")
    out = registry.execute("grep", {"pattern": "needle", "directory": str(tmp_path)})
    assert "foo.py" in out and "needle" in out

    out = registry.execute(
        "find_files", {"directory": str(tmp_path), "glob": "*.py"}
    )
    assert "foo.py" in out and "bar.py" in out


def test_run_python_uses_sys_executable(registry: ToolRegistry) -> None:
    out = registry.execute("run_python", {"code": "print(1+2)"})
    assert "3" in out


def test_run_shell_basic(registry: ToolRegistry) -> None:
    # `echo` exists on both cmd.exe and /bin/sh.
    out = registry.execute("run_shell", {"command": "echo loom"})
    assert "loom" in out


def test_platform_info(registry: ToolRegistry) -> None:
    out = registry.execute("platform_info", {})
    assert "system=" in out and "python=" in out


def test_excel_round_trip(tmp_path: Path, registry: ToolRegistry) -> None:
    target = tmp_path / "book.xlsx"
    rows = [["a", "b", "c"], [1, 2, 3], [4, 5, 6]]
    out = registry.execute(
        "excel_write", {"path": str(target), "sheet": "S1", "rows": rows}
    )
    assert "Wrote 3 rows" in out

    out = registry.execute("excel_sheets", {"path": str(target)})
    assert "S1" in out

    out = registry.execute("excel_read", {"path": str(target), "sheet": "S1"})
    parsed = json.loads(out)
    assert parsed["rows_returned"] == 3
    assert parsed["rows"][0] == ["a", "b", "c"]


def test_unknown_tool_returns_error(registry: ToolRegistry) -> None:
    assert "unknown tool" in registry.execute("does_not_exist", {}).lower()


def test_input_schemas_are_valid_json_schema(registry: ToolRegistry) -> None:
    for tool in registry.provider_tools():
        assert tool.input_schema.get("type") == "object"
        assert "properties" in tool.input_schema
