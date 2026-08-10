import asyncio
from pathlib import Path

from heyclaw_shared.performance import measure_performance

from app.agent.skills import SkillCatalog


class WorkspaceContext:
    """Build the configurable portion of the agent context from the local workspace."""

    BOOTSTRAP_FILES = ("AGENTS.md", "SOUL.md", "USER.md", "TOOLS.md")

    def __init__(self, workspace: Path, skills: SkillCatalog) -> None:
        self._workspace = workspace.resolve()
        self._skills = skills

    async def build(self) -> str:
        with measure_performance("workspace.context.load"):
            return await asyncio.to_thread(self._build)

    def _build(self) -> str:
        sections: list[str] = []
        for filename in self.BOOTSTRAP_FILES:
            path = self._workspace / filename
            content = path.read_text(encoding="utf-8").strip()
            if not content:
                raise ValueError(f"Empty workspace file: {filename}")
            sections.append(content)

        summary = self._skills.build_summary()
        if summary:
            sections.append(
                "# Available skills\n\n"
                "When a request matches an available skill, use `read_skill` to read its "
                "complete instructions before invoking external tools.\n\n"
                f"{summary}"
            )
        return "\n\n---\n\n".join(sections)
