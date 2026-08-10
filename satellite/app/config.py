from pathlib import Path
from typing import Literal

from heyclaw_shared.config import ElevenLabsConfig, load_json_config
from pydantic import BaseModel, ConfigDict, Field


class AgentConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    first_message: str = Field(alias="firstMessage", min_length=1)
    audio_input_device_index: int | None = Field(
        default=None, alias="audioInputDeviceIndex"
    )
    audio_output_device_index: int | None = Field(
        default=None, alias="audioOutputDeviceIndex"
    )
    echo_suppression_mode: Literal["off", "gate", "aec"] = Field(
        default="off", alias="echoSuppressionMode"
    )
    echo_guard_ms: int = Field(default=350, ge=0, le=2000, alias="echoGuardMs")
    wake_word_enabled: bool = Field(default=True, alias="wakeWordEnabled")
    wake_word_model: str = Field(default="andromeda", alias="wakeWordModel")
    wake_word_threshold: float = Field(
        default=0.5, ge=0, le=1, alias="wakeWordThreshold"
    )


class DefaultsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent: AgentConfig


class ProvidersConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    elevenlabs: ElevenLabsConfig = Field(default_factory=ElevenLabsConfig)


class SatelliteConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    defaults: DefaultsConfig
    providers: ProvidersConfig = Field(default_factory=ProvidersConfig)


DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[1] / "config.json"


def load_config(path: Path = DEFAULT_CONFIG_PATH) -> SatelliteConfig:
    return load_json_config(SatelliteConfig, path)
