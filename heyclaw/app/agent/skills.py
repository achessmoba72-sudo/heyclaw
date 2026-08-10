from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import dspy
import orjson
from heyclaw_shared.performance import measure_performance

_FRONTMATTER = re.compile(r"^---\s*\r?\n(.*?)\r?\n---\s*\r?\n?", re.DOTALL)
_SKILL_NAME = re.compile(r"^[a-z0-9][a-z0-9-]*$")


@dataclass(frozen=True)
class Skill:
    name: str
    description: str
    path: Path
    required_tools: tuple[str, ...] = ()


class SkillCatalog:
    """Discover workspace skills and expose their instructions without general file access."""

    def __init__(self, workspace: Path) -> None:
        self._workspace = workspace.resolve()
        self._skills_root = (self._workspace / "skills").resolve()
        self._available_tools: set[str] = set()

    def set_available_tools(self, tools: set[str]) -> None:
        self._available_tools = tools

    def list_skills(self) -> list[Skill]:
        skills: list[Skill] = []
        for directory in sorted(
            self._skills_root.iterdir(), key=lambda item: item.name
        ):
            skill_file = (directory / "SKILL.md").resolve()
            if not directory.is_dir():
                continue
            if not skill_file.is_relative_to(self._skills_root):
                raise ValueError(f"Skill outside the workspace: {directory.name}")
            skills.append(self._parse_skill(skill_file))
        return skills

    def build_summary(self) -> str:
        lines: list[str] = []
        for skill in self.list_skills():
            missing = self._missing_tools(skill)
            availability = (
                f" Unavailable: missing {', '.join(missing)}." if missing else ""
            )
            lines.append(f"- {skill.name}: {skill.description}{availability}")
        return "\n".join(lines)

    async def read_skill(self, name: str) -> str:
        with measure_performance(f"workspace.skill.read.{name}"):
            return await asyncio.to_thread(self._read_skill, name)

    def _read_skill(self, name: str) -> str:
        skill = next((item for item in self.list_skills() if item.name == name), None)
        if skill is None:
            raise ValueError(f"Skill unavailable: {name}")
        missing = self._missing_tools(skill)
        if missing:
            raise RuntimeError(
                f"Skill {name} unavailable. Missing tools: {', '.join(missing)}"
            )
        content = skill.path.read_text(encoding="utf-8")
        return _FRONTMATTER.sub("", content, count=1).strip()

    def as_dspy_tool(self) -> Any:
        return dspy.Tool(
            func=self.read_skill,
            name="read_skill",
            desc=(
                "Reads the complete instructions for a skill listed in the context. "
                "Use it before the external tools described by the skill."
            ),
            args={
                "name": {
                    "type": "string",
                    "description": "Exact name of the available skill to read",
                }
            },
            arg_types={"name": str},
        )

    def _missing_tools(self, skill: Skill) -> list[str]:
        return [
            name for name in skill.required_tools if name not in self._available_tools
        ]

    @staticmethod
    def _parse_skill(path: Path) -> Skill:
        content = path.read_text(encoding="utf-8")
        match = _FRONTMATTER.match(content)
        if match is None:
            raise ValueError(f"Missing frontmatter in skill {path.parent.name}")

        fields: dict[str, str] = {}
        for line in match.group(1).splitlines():
            key, separator, value = line.partition(":")
            if separator and key.strip() in {"name", "description", "metadata"}:
                fields[key.strip()] = value.strip().strip("\"'")

        name = fields.get("name", path.parent.name)
        description = fields.get("description", "")
        if not _SKILL_NAME.fullmatch(name):
            raise ValueError(f"Invalid name in skill {path.parent.name}")
        if not description:
            raise ValueError(f"Missing description in skill {name}")

        required_tools: tuple[str, ...] = ()
        metadata = fields.get("metadata")
        if metadata:
            payload = orjson.loads(metadata)
            tools = payload.get("heyclaw", {}).get("requires", {}).get("tools", [])
            if not isinstance(tools, list) or not all(
                isinstance(item, str) for item in tools
            ):
                raise ValueError(f"Invalid required tools in skill {name}")
            required_tools = tuple(tools)

        return Skill(
            name=name,
            description=description,
            path=path,
            required_tools=required_tools,
        )
