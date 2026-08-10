import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from mcp import Tool, types

import app.services.mcp.client as mcp_client
from app.domain.conversation import ConversationMessage
from app.services.llm.dspy_backend import DspyResponseGenerator
from app.services.mcp import MCPToolProvider, load_config
from app.services.mcp.client import _to_dspy_tool


class FakeClient:
    def __init__(self, delay: float = 0) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.delay = delay

    async def call_tool(
        self,
        name: str,
        arguments: dict[str, Any],
        progress_callback: Any = None,
    ) -> types.CallToolResult:
        if self.delay:
            await asyncio.sleep(self.delay)
        self.calls.append((name, arguments))
        return types.CallToolResult(
            content=[types.TextContent(type="text", text="updated result")]
        )


class FakeSkills:
    def as_dspy_tool(self) -> Any:
        return None

    def set_available_tools(self, tools: set[str]) -> None:
        return None


class FakeWorkspace:
    async def build(self) -> str:
        return "Workspace context"


class FakeMemory:
    async def start(self) -> None:
        return None

    async def search(self, query: str) -> str:
        return ""

    async def add(self, user_message: str, assistant_message: str) -> None:
        return None

    async def close(self) -> None:
        return None


def make_generator() -> DspyResponseGenerator:
    return DspyResponseGenerator(
        provider="gemini",
        api_key="test-key",
        model="gemini-test",
        temperature=0.0,
        max_output_tokens=64,
        mcp_tools=MCPToolProvider({}),
        skills=FakeSkills(),
        workspace_context=FakeWorkspace(),
        memory=FakeMemory(),
    )


def make_tool(client: FakeClient, name: str, description: str) -> Any:
    return _to_dspy_tool(
        client,
        "perplexity",
        Tool(
            name=name,
            description=description,
            inputSchema={
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        ),
    )


async def generate(generator: DspyResponseGenerator, request: str) -> list[str]:
    await generator.start()
    return [
        chunk
        async for chunk in generator.stream(
            [ConversationMessage(role="user", content=request)]
        )
    ]


def test_loads_nanobot_compatible_mcp_config(tmp_path: Path) -> None:
    config = tmp_path / "config.json"
    config.write_text(
        '{"defaults":{"agent":{"firstMessage":"Hello"},'
        '"memory":{"mem0":{"apiKey":"test"}}},'
        '"tools":{"mcpServers":{"perplexity":{"command":"npx",'
        '"args":["server"],"env":{"PERPLEXITY_API_KEY":"secret"}}}}}',
        encoding="utf-8",
    )

    servers = load_config(config).tools.mcp_servers

    assert servers["perplexity"].command == "npx"
    assert servers["perplexity"].env == {"PERPLEXITY_API_KEY": "secret"}


async def test_mcp_v2_tool_is_adapted_to_async_dspy_tool() -> None:
    client = FakeClient()
    tool = make_tool(client, "perplexity_search", "Search the current web")
    result = await tool.acall(query="latest news")

    assert result == "updated result"
    assert client.calls == [("perplexity_search", {"query": "latest news"})]


async def test_dspy_generator_yields_only_final_answer() -> None:
    class FakeProgram:
        async def acall(self, **kwargs: Any) -> SimpleNamespace:
            assert kwargs["conversation"].startswith("GET DATE TODAY: ")
            return SimpleNamespace(
                answer="Speakable answer.",
                trajectory={"tool_name_0": "perplexity_search"},
            )

    generator = make_generator()
    generator._program = FakeProgram()

    assert await generate(generator, "How are you?") == ["Speakable answer."]


async def test_dspy_generator_speaks_during_a_slow_mcp_tool(
    monkeypatch: Any,
) -> None:
    monkeypatch.setattr(mcp_client, "_SLOW_TOOL_SECONDS", 0.001)
    tool = make_tool(
        FakeClient(delay=0.02),
        "perplexity_ask",
        "Search for up-to-date information on the Internet",
    )

    class FakeProgram:
        async def acall(self, **_kwargs: Any) -> SimpleNamespace:
            await tool.acall(query="latest news")
            return SimpleNamespace(answer="Here is the result.")

    class FakeStatusProgram:
        async def acall(self, **_kwargs: Any) -> SimpleNamespace:
            return SimpleNamespace(
                acknowledgement="One moment, I'll search for the latest information.",
                still_waiting="The search needs a little more time.",
            )

    generator = make_generator()
    generator._program = FakeProgram()
    generator._tool_status_program = FakeStatusProgram()

    assert await generate(generator, "Tell me the latest news") == [
        "One moment, I'll search for the latest information. ",
        "The search needs a little more time. ",
        "Here is the result.",
    ]
