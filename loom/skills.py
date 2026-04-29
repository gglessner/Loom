"""Skill .md loader. Skills are plain markdown files that get appended to
the system prompt for additional context.

Skills are merged from multiple locations so a user can keep a personal
library while a project can layer extras on top:

  1. ~/.loom/skills/           (user-global)
  2. <cwd>/skills/             (project-shared, visible - check this into git!)
  3. <cwd>/.loom/skills/       (project-private, hidden - overrides shared)
  4. <cwd>/<skills_dir>/       (configurable; defaults to .loom/skills, dedup'd)

Later directories override earlier ones if they contain a skill of the same
filename, so a project-local skill can shadow a user-global one.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable, Sequence


class SkillManager:
    def __init__(self, directories: Sequence[Path]) -> None:
        # Preserve insertion order; deduplicate while keeping later wins.
        seen: set[Path] = set()
        ordered: list[Path] = []
        for d in directories:
            r = d.resolve() if d.exists() else d
            if r in seen:
                continue
            seen.add(r)
            ordered.append(d)
        self.directories: list[Path] = ordered
        self.skills: dict[str, str] = {}
        self.sources: dict[str, Path] = {}

    def discover(self) -> list[str]:
        self.skills.clear()
        self.sources.clear()
        for directory in self.directories:
            if not directory.exists():
                continue
            for md in sorted(directory.rglob("*.md")):
                try:
                    self.skills[md.stem] = md.read_text(encoding="utf-8")
                    self.sources[md.stem] = md
                except OSError:
                    continue
        return list(self.skills)

    def system_block(self) -> str:
        if not self.skills:
            return ""
        chunks: Iterable[str] = (
            f"## Skill: {name}\n{content.strip()}" for name, content in self.skills.items()
        )
        return "# Skills\n" + "\n\n".join(chunks)

    def short_summary(self) -> str:
        if not self.skills:
            return "(no skills loaded)"
        rows = []
        for name, content in self.skills.items():
            head = (content.strip().split("\n", 1)[0]) if content.strip() else ""
            src = self.sources.get(name)
            src_str = f"  [{src.parent}]" if src else ""
            rows.append(f"- {name}: {head[:80]}{src_str}")
        return "\n".join(rows)
