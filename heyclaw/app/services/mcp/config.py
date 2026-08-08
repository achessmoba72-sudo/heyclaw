from pathlib import Path

import orjson
from pydantic import BaseModel, ConfigDict, Field, model_validator


class MCPServerConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    command: str = ""
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    url: str = ""

    @model_validator(mode="after")
    def validate_transport(self) -> "MCPServerConfig":
        if bool(self.command) == bool(self.url):
            raise ValueError(
                "An MCP server must configure exactly one of command or url"
            )
        return self


class ToolsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mcp_servers: dict[str, MCPServerConfig] = Field(
        default_factory=dict, alias="mcpServers"
    )


class Mem0Config(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    api_key: str = Field(alias="apiKey", min_length=1)
    enabled: bool = True


class MemoryConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mem0: Mem0Config


class AgentConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    first_message: str = Field(alias="firstMessage", min_length=1)
    llm_model: str = Field(default="gemini-3.1-flash-lite", alias="llmModel")
    llm_temperature: float = Field(default=0.2, ge=0, le=2, alias="llmTemperature")
    llm_max_output_tokens: int = Field(
        default=320, ge=32, le=8192, alias="llmMaxOutputTokens"
    )
    response_timeout_seconds: float = Field(
        default=30, gt=0, le=120, alias="responseTimeoutSeconds"
    )


class GeminiProviderConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    gemini_api_key: str = Field(default="", alias="geminiApiKey")


class ElevenLabsProviderConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    elevenlabs_api_key: str = Field(default="", alias="elevenlabsApiKey")
    elevenlabs_speech_engine_id: str = Field(
        default="", alias="elevenlabsSpeechEngineId"
    )


class ProvidersConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    gemini: GeminiProviderConfig = Field(default_factory=GeminiProviderConfig)
    elevenlabs: ElevenLabsProviderConfig = Field(
        default_factory=ElevenLabsProviderConfig
    )


class GatewayConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    api_host: str = Field(default="127.0.0.1", alias="apiHost")
    api_port: int = Field(default=8000, ge=1, le=65535, alias="apiPort")
    public_ws_url: str = Field(default="", alias="publicWsUrl")


class DefaultsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace: str = "./workspace"
    agent: AgentConfig
    memory: MemoryConfig


class HeyClawConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    defaults: DefaultsConfig
    gateway: GatewayConfig = Field(default_factory=GatewayConfig)
    providers: ProvidersConfig = Field(default_factory=ProvidersConfig)
    tools: ToolsConfig = Field(default_factory=ToolsConfig)


DEFAULT_MCP_CONFIG_PATH = Path(__file__).resolve().parents[3] / "config.json"


def load_config(path: Path = DEFAULT_MCP_CONFIG_PATH) -> HeyClawConfig:
    return HeyClawConfig.model_validate(orjson.loads(path.read_bytes()))


def resolve_workspace_path(
    config: HeyClawConfig,
    config_path: Path = DEFAULT_MCP_CONFIG_PATH,
) -> Path:
    workspace = Path(config.defaults.workspace).expanduser()
    if not workspace.is_absolute():
        workspace = config_path.resolve().parent / workspace
    return workspace.resolve()
