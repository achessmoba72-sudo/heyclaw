from typing import TYPE_CHECKING, Any

from app.services.mcp.config import (
    HeyClawConfig,
    MCPServerConfig,
    load_config,
    resolve_workspace_path,
)

if TYPE_CHECKING:
    from app.services.mcp.client import (
        MCPToolEvent,
        MCPToolProvider,
        observe_mcp_tool_events,
    )

_CLIENT_EXPORTS = {
    "MCPToolEvent",
    "MCPToolProvider",
    "observe_mcp_tool_events",
}


def __getattr__(name: str) -> Any:
    if name not in _CLIENT_EXPORTS:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

    from app.services.mcp import client

    return getattr(client, name)


__all__ = [
    "HeyClawConfig",
    "MCPServerConfig",
    "MCPToolEvent",
    "MCPToolProvider",
    "load_config",
    "observe_mcp_tool_events",
    "resolve_workspace_path",
]
