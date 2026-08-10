# Mem0 must load before DSPy installs its lazy NumPy module proxy.
# isort: off
from app.services.memory.memory import Mem0Memory
from app.agent import SkillCatalog, WorkspaceContext
# isort: on

from app.core.config import Settings
from app.services.llm.dspy_backend import DspyResponseGenerator
from app.services.mcp import MCPToolProvider, load_config, resolve_workspace_path
from app.services.speech_engine import SpeechEngineRuntime


def create_dspy_response_generator(settings: Settings) -> DspyResponseGenerator:
    config = load_config()
    workspace = resolve_workspace_path(config)
    skills = SkillCatalog(workspace)
    mem0_config = config.defaults.memory.mem0
    agent_defaults = config.defaults.agent
    memory = Mem0Memory(api_key=mem0_config.api_key)
    return DspyResponseGenerator(
        provider=agent_defaults.llm_provider,
        api_key=config.providers.api_key_for(agent_defaults.llm_provider),
        model=agent_defaults.llm_model,
        temperature=agent_defaults.llm_temperature,
        max_output_tokens=agent_defaults.llm_max_output_tokens,
        mcp_tools=MCPToolProvider(config.tools.mcp_servers),
        skills=skills,
        workspace_context=WorkspaceContext(workspace, skills),
        memory=memory,
    )


def create_speech_engine_runtime(settings: Settings) -> SpeechEngineRuntime:
    config = load_config()
    elevenlabs = config.providers.elevenlabs
    generator = create_dspy_response_generator(settings)
    return SpeechEngineRuntime(
        api_key=elevenlabs.elevenlabs_api_key,
        engine_id=elevenlabs.elevenlabs_speech_engine_id,
        debug=settings.logging_level.upper() == "DEBUG",
        response_timeout_seconds=config.defaults.agent.response_timeout_seconds,
        generator=generator,
    )
