from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterator
from contextlib import AsyncExitStack, contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from itertools import count
from typing import Any, Literal

import dspy
import orjson
from heyclaw_shared.performance import measure_performance
from loguru import logger
from mcp import Client, StdioServerParameters, types
from mcp.client.stdio import stdio_client

from app.services.mcp.config import MCPServerConfig


@dataclass(frozen=True)
class MCPToolEvent:
    call_id: int
    phase: Literal["started", "waiting"]
    server_name: str
    tool_name: str
    description: str
    arguments: dict[str, Any]


ToolEventListener = Callable[[MCPToolEvent], None]
_TOOL_EVENT_LISTENER: ContextVar[ToolEventListener | None] = ContextVar(
    "mcp_tool_event_listener", default=None
)
_CALL_IDS = count(1)
# Web searches routinely take ~7s, so a lower threshold makes the "still waiting" line
# fire on almost every call and land glued to the answer it was meant to precede.
_SLOW_TOOL_SECONDS = 12.0
_LOGGED_RESPONSE_CHARS = 300


@contextmanager
def observe_mcp_tool_events(listener: ToolEventListener) -> Iterator[None]:
    token = _TOOL_EVENT_LISTENER.set(listener)
    try:
        yield
    finally:
        _TOOL_EVENT_LISTENER.reset(token)


class MCPToolProvider:
    """Connect configured MCP v2 clients and expose their tools to DSPy."""

    def __init__(self, servers: dict[str, MCPServerConfig]) -> None:
        self._servers = servers
        self._stack = AsyncExitStack()
        self._tools: list[Any] = []
        self._tool_sources: set[str] = set()
        self._started = False

    async def start(self) -> list[Any]:
        if self._started:
            return self._tools
        self._started = True
        await self._stack.__aenter__()

        try:
            for server_name, config in self._servers.items():
                with measure_performance(f"mcp.server.connect.{server_name}"):
                    transport = (
                        stdio_client(
                            StdioServerParameters(
                                command=config.command,
                                args=config.args,
                                env=config.env or None,
                            )
                        )
                        if config.command
                        else config.url
                    )
                    client = await self._stack.enter_async_context(
                        Client(transport, read_timeout_seconds=300)
                    )
                    result = await client.list_tools()
                for definition in result.tools:
                    tool = _to_dspy_tool(client, server_name, definition)
                    self._tools.append(tool)
                    self._tool_sources.add(f"{server_name}/{definition.name}")
                logger.info(
                    "MCP server connected: {} ({} tools)",
                    server_name,
                    len(result.tools),
                )
        except BaseException:
            await self.close()
            raise

        return self._tools

    def available_tool_sources(self) -> set[str]:
        return self._tool_sources.copy()

    async def close(self) -> None:
        if self._started:
            with measure_performance("mcp.clients.close"):
                await self._stack.aclose()
            self._started = False
            self._tools.clear()
            self._tool_sources.clear()


def _to_dspy_tool(client: Client, server_name: str, tool: types.Tool) -> Any:
    properties = tool.input_schema.get("properties", {})
    required = set(tool.input_schema.get("required", []))
    arguments = {
        name: schema if name in required else {**schema, "default": None}
        for name, schema in properties.items()
    }

    async def call_mcp_tool(**kwargs: Any) -> Any:
        call_id = next(_CALL_IDS)
        listener = _TOOL_EVENT_LISTENER.get()
        description = tool.description or tool.name
        if listener is not None:
            listener(
                MCPToolEvent(
                    call_id=call_id,
                    phase="started",
                    server_name=server_name,
                    tool_name=tool.name,
                    description=description,
                    arguments=kwargs,
                )
            )

        logger.info("MCP → {}/{}", server_name, tool.name)
        logger.opt(lazy=True).debug(
            "{}",
            lambda: orjson.dumps({"name": tool.name, "arguments": kwargs}).decode(),
        )

        async def on_progress(
            progress: float,
            total: float | None,
            message: str | None,
        ) -> None:
            logger.opt(lazy=True).debug(
                "{}",
                lambda: orjson.dumps(
                    {"progress": progress, "total": total, "message": message}
                ).decode(),
            )

        async def report_slow_tool() -> None:
            await asyncio.sleep(_SLOW_TOOL_SECONDS)
            if listener is not None:
                listener(
                    MCPToolEvent(
                        call_id=call_id,
                        phase="waiting",
                        server_name=server_name,
                        tool_name=tool.name,
                        description=description,
                        arguments=kwargs,
                    )
                )

        slow_tool_task = (
            asyncio.create_task(report_slow_tool()) if listener is not None else None
        )
        try:
            with measure_performance(f"mcp.tool.call.{server_name}.{tool.name}"):
                result = await client.call_tool(
                    tool.name,
                    arguments=kwargs,
                    progress_callback=on_progress,
                )
        finally:
            if slow_tool_task is not None:
                slow_tool_task.cancel()
                await asyncio.gather(slow_tool_task, return_exceptions=True)

        logger.opt(lazy=True).debug(
            "{}",
            lambda: orjson.dumps(
                result.model_dump(by_alias=True, exclude_none=True)
            ).decode(),
        )
        response = _mcp_response_text(result)
        if result.is_error:
            detail = response or "error without details"
            logger.warning("MCP ← {}/{}: {}", server_name, tool.name, detail)
            raise RuntimeError(f"MCP tool {tool.name} failed: {detail}")
        # The full payload is already in the DEBUG record above; a search result is
        # kilobytes of prose and citations that would otherwise land in every INFO log.
        logger.info("MCP ← {}/{}: {}", server_name, tool.name, _abbreviate(response))
        return (
            result.structured_content
            if result.structured_content is not None
            else response
        )

    return dspy.Tool(
        func=call_mcp_tool,
        name=f"mcp_{server_name}_{tool.name}",
        desc=tool.description or f"Tool {tool.name} from MCP server {server_name}",
        args=arguments,
        arg_types={name: Any for name in arguments},
    )


def _abbreviate(response: str) -> str:
    collapsed = " ".join(response.split())
    if len(collapsed) <= _LOGGED_RESPONSE_CHARS:
        return collapsed
    return f"{collapsed[:_LOGGED_RESPONSE_CHARS]}… (+{len(collapsed) - _LOGGED_RESPONSE_CHARS} chars)"


def _content_as_text(content: list[types.ContentBlock]) -> str:
    parts: list[str] = []
    for block in content:
        if isinstance(block, types.TextContent):
            parts.append(block.text)
        else:
            parts.append(orjson.dumps(block.model_dump(by_alias=True)).decode())
    return "\n".join(parts)


def _mcp_response_text(result: types.CallToolResult) -> str:
    if isinstance(result.structured_content, dict):
        response = result.structured_content.get("response")
        if isinstance(response, str):
            return response
    return _content_as_text(result.content)
