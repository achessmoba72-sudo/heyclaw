from typing import Literal

from pydantic import BaseModel


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"
    service: str = "heyclaw"
    version: str


class ReadinessResponse(BaseModel):
    status: Literal["ready", "configuration_required"]
    speech_engine_configured: bool
    gemini_configured: bool
    mem0_configured: bool
    llm_model: str
