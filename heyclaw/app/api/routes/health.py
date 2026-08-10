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
    openai_configured = bool(config.providers.openai.openai_api_key)
    anthropic_configured = bool(config.providers.anthropic.anthropic_api_key)
    llm_provider = config.defaults.agent.llm_provider
    llm_configured = bool(config.providers.api_key_for(llm_provider))
    mem0_config = config.defaults.memory.mem0
    mem0_configured = bool(mem0_config.api_key)
    return ReadinessResponse(
        status=(
            "ready"
            if speech_engine_configured and llm_configured and mem0_configured
            else "configuration_required"
        ),
        speech_engine_configured=speech_engine_configured,
        llm_configured=llm_configured,
        llm_provider=llm_provider,
        gemini_configured=gemini_configured,
        openai_configured=openai_configured,
        anthropic_configured=anthropic_configured,
        mem0_configured=mem0_configured,
        llm_model=config.defaults.agent.llm_model,
    )
