from functools import lru_cache
from pathlib import Path
from typing import cast

import orjson
from pydantic import BaseModel, ConfigDict, Field


class ElevenLabsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    elevenlabs_api_key: str = Field(default="", alias="elevenlabsApiKey")
    elevenlabs_speech_engine_id: str = Field(
        default="", alias="elevenlabsSpeechEngineId"
    )

    def require_api_key(self) -> str:
        if not self.elevenlabs_api_key:
            raise RuntimeError(
                "providers.elevenlabs.elevenlabsApiKey is not configured"
            )
        return self.elevenlabs_api_key

    def require_speech_engine_id(self) -> str:
        if not self.elevenlabs_speech_engine_id:
            raise RuntimeError(
                "providers.elevenlabs.elevenlabsSpeechEngineId is not configured"
            )
        return self.elevenlabs_speech_engine_id


@lru_cache
def _parse_json_config(model: type[BaseModel], path: Path) -> BaseModel:
    return model.model_validate(orjson.loads(path.read_bytes()))


def load_json_config[ConfigT: BaseModel](model: type[ConfigT], path: Path) -> ConfigT:
    """Parse and validate a component's `config.json`, caching it per path."""
    return cast(ConfigT, _parse_json_config(model, path))
