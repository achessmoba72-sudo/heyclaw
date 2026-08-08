from fastapi import APIRouter

from app import __version__
from app.schemas.health import HealthResponse, ReadinessResponse
from app.services.mcp import load_config

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    return HealthResponse(version=__version__)


@router.get("/ready", response_model=ReadinessResponse)
async def readiness() -> ReadinessResponse:
    config = load_config()
    elevenlabs = config.providers.elevenlabs
    speech_engine_configured = bool(
        elevenlabs.elevenlabs_api_key and elevenlabs.elevenlabs_speech_engine_id
    )
    gemini_configured = bool(config.providers.gemini.gemini_api_key)
    mem0_config = config.defaults.memory.mem0
    mem0_configured = bool(mem0_config.api_key)
    return ReadinessResponse(
        status=(
            "ready"
            if speech_engine_configured and gemini_configured and mem0_configured
            else "configuration_required"
        ),
        speech_engine_configured=speech_engine_configured,
        gemini_configured=gemini_configured,
        mem0_configured=mem0_configured,
        llm_model=config.defaults.agent.llm_model,
    )
