from typing import Literal

from pydantic import BaseModel


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"
    service: str = "heyclaw"
    version: str


class ReadinessResponse(BaseModel):
    status: Literal["ready", "configuration_required"]
    speech_engine_configured: bool
    llm_configured: bool
    llm_provider: Literal["gemini", "openai", "anthropic"]
    gemini_configured: bool
    openai_configured: bool
    anthropic_configured: bool
    mem0_configured: bool
    llm_model: str
