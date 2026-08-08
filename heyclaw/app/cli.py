import asyncio
from time import monotonic

import httpx
import uvicorn
from elevenlabs import AsyncElevenLabs
from elevenlabs.types import (
    BaseTurnConfig,
    SpeechEngineConfig,
    SpeechEngineConversationInitiationClientDataConfig,
)
from loguru import logger

from app.core.config import get_settings
from app.core.logging import configure_logging
from app.core.performance import measure_performance
from app.services.mcp.config import load_config


def _api_server() -> uvicorn.Server:
    settings = get_settings()
    gateway = load_config().gateway
    return uvicorn.Server(
        uvicorn.Config(
            "app.main:app",
            host=gateway.api_host,
            port=gateway.api_port,
            log_level=settings.logging_level.lower(),
        )
    )


def run_api() -> None:
    _api_server().run()


async def _serve(api: uvicorn.Server | None = None) -> None:
    from app.runtime.factory import create_speech_engine_runtime

    settings = get_settings()
    configure_logging(settings)
    runtime = create_speech_engine_runtime(settings)
    try:
        if api is None:
            await runtime.serve()
        else:
            async with asyncio.TaskGroup() as tasks:
                tasks.create_task(api.serve(), name="heyclaw-api")
                tasks.create_task(runtime.serve(), name="heyclaw-speech-engine")
    finally:
        await runtime.close()


def run_speech_engine() -> None:
    asyncio.run(_serve())


def run_all() -> None:
    asyncio.run(_serve(_api_server()))


async def _create_engine() -> None:
    config = load_config()
    elevenlabs = config.providers.elevenlabs
    if not elevenlabs.elevenlabs_api_key:
        raise RuntimeError(
            "providers.elevenlabs.elevenlabsApiKey is not configured"
        )
    if not config.gateway.public_ws_url:
        raise RuntimeError("gateway.publicWsUrl is not configured")
    async with httpx.AsyncClient(timeout=30) as http_client:
        client = AsyncElevenLabs(
            api_key=elevenlabs.elevenlabs_api_key,
            httpx_client=http_client,
        )
        with measure_performance("elevenlabs.speech_engine.create"):
            engine = await client.speech_engine.create(
                name="HeyClaw",
                language="en",
                speech_engine=SpeechEngineConfig(ws_url=config.gateway.public_ws_url),
                # Keep this exact BaseTurnConfig configuration unchanged unless explicitly requested by
                # the user; it prevents silence from triggering unwanted responses.
                turn=BaseTurnConfig(
                    turn_timeout=-1,
                    silence_end_call_timeout=-1,
                    speculative_turn=False,
                ),
                overrides=SpeechEngineConversationInitiationClientDataConfig(
                    first_message=True
                ),
            )
    logger.info("Speech Engine created: {}", engine.engine_id)


def create_engine() -> None:
    settings = get_settings()
    configure_logging(settings)
    asyncio.run(_create_engine())


async def _benchmark_llm() -> None:
    from app.domain.conversation import ConversationMessage
    from app.runtime.factory import create_dspy_response_generator

    settings = get_settings()
    agent_defaults = load_config().defaults.agent
    configure_logging(settings)
    generator = create_dspy_response_generator(settings)
    started_at = monotonic()
    first_token_at: float | None = None
    chunks: list[str] = []
    try:
        await generator.start()
        async for chunk in generator.stream(
            [
                ConversationMessage(
                    role="user", content="Greet me with a very short sentence."
                )
            ]
        ):
            if first_token_at is None:
                first_token_at = monotonic()
            chunks.append(chunk)
    finally:
        await generator.close()
    completed_at = monotonic()
    logger.info("DSPy model: {}", agent_defaults.llm_model)
    if first_token_at is not None:
        logger.info(
            "Time to first token: {:.0f} ms", (first_token_at - started_at) * 1000
        )
    logger.info("Total time: {:.0f} ms", (completed_at - started_at) * 1000)
    logger.info("Answer: {}", "".join(chunks).strip())


def benchmark_llm() -> None:
    asyncio.run(_benchmark_llm())
