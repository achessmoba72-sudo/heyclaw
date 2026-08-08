from pathlib import Path

import pytest

from app.agent.skills import SkillCatalog


async def test_skill_requires_its_configured_tool(tmp_path: Path) -> None:
    skill_dir = tmp_path / "skills" / "web-search"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        '---\nname: web-search\ndescription: Search the web\nmetadata: '
        '{"heyclaw":{"requires":{"tools":["perplexity/perplexity_ask"]}}}\n'
        '---\n\nUse Perplexity.',
        encoding="utf-8",
    )
    catalog = SkillCatalog(tmp_path)

    with pytest.raises(RuntimeError):
        await catalog.read_skill("web-search")

    catalog.set_available_tools({"perplexity/perplexity_ask"})
    assert await catalog.read_skill("web-search") == "Use Perplexity."
