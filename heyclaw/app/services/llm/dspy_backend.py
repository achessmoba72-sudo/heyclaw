import asyncio
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Any

import dspy
import orjson
from heyclaw_shared.performance import measure_performance

from app.agent import SkillCatalog, WorkspaceContext
from app.domain.conversation import ConversationMessage, latest_user_content
from app.services.mcp.client import (
    MCPToolEvent,
    MCPToolProvider,
    observe_mcp_tool_events,
)
from app.services.mcp.config import LLMProvider
from app.services.memory.base import AgentMemory

_MODEL_PROVIDER_ALIASES: dict[str, LLMProvider] = {
    "google": "gemini",
    "gemini": "gemini",
    "openai": "openai",
    "anthropic": "anthropic",
}


def _normalize_model(provider: LLMProvider, model: str) -> str:
    model_name = model.strip()
    if not model_name:
        raise ValueError("defaults.agent.llmModel must not be empty")

    prefix, separator, unprefixed_model = model_name.partition("/")
    if separator:
        model_provider = _MODEL_PROVIDER_ALIASES.get(prefix)
        if model_provider is None:
            raise ValueError(
                f"Unsupported LLM provider prefix in defaults.agent.llmModel: {prefix}"
            )
        if model_provider != provider:
            raise ValueError(
                "defaults.agent.llmModel provider prefix does not match "
                "defaults.agent.llmProvider"
            )
        model_name = unprefixed_model

    if not model_name:
        raise ValueError("defaults.agent.llmModel must include a model name")

    return f"{provider}/{model_name}"


def _supports_configurable_temperature(model: str) -> bool:
    default_temperature_only = (
        "openai/gpt-5",
        "anthropic/claude-sonnet-5",
        "anthropic/claude-opus-5",
        "anthropic/claude-fable-5",
    )
    return not model.startswith(default_temperature_only)


class VoiceAssistant(dspy.Signature):
    """Generate the voice assistant's final answer using the configured workspace.

    When a request matches an available skill, read it with read_skill before using the external
    tools it describes. Do not use an external tool without an applicable skill. Do not invent
    data or claim that a tool succeeded before receiving its result. Use semantic memories only
    when they are relevant to the current request. If a skill or tool is unavailable or fails,
    say so briefly. Return only the words to speak to the user, without reasoning, Markdown,
    technical names, arguments, payloads, skills, or internal calls.
    """

    workspace_context: str = dspy.InputField(
        desc="Instructions, identity, context, and skill catalog configured in the workspace"
    )
    memory_context: str = dspy.InputField(
        desc="Relevant semantic memories about the user, retrieved automatically"
    )
    conversation: str = dspy.InputField(
        desc="Chronological transcript of User and Assistant turns"
    )
    answer: str = dspy.OutputField(
        desc="Only the final speakable answer in the user's language"
    )


class ToolSpeechStatus(dspy.Signature):
    """Generate two very short, natural spoken updates for a tool call.

    Answer in the user's language, in the first person, and without Markdown. The sentences must
    describe the concrete action from the user's perspective without speaking technical names,
    servers, tools, payloads, or JSON arguments. The initial acknowledgement immediately says what
    you are about to do; the second asks for a little more patience without repeating the first
    verbatim. Each sentence must be suitable for speech and contain no more than twelve words.
    """

    memory_context: str = dspy.InputField(
        desc="Relevant semantic memories about the user, including language preferences"
    )
    user_request: str = dspy.InputField(desc="User's current request")
    tool_description: str = dspy.InputField(desc="Purpose of the selected tool")
    tool_arguments: str = dspy.InputField(desc="Call arguments, used only as context")
    acknowledgement: str = dspy.OutputField(
        desc="Immediate acknowledgement before the operation"
    )
    still_waiting: str = dspy.OutputField(
        desc="Update to speak only if the wait becomes prolonged"
    )


class DspyResponseGenerator:
    """DSPy voice agent whose internal tool trajectory never reaches speech output."""

    def __init__(
        self,
        *,
        provider: LLMProvider,
        api_key: str,
        model: str,
        temperature: float,
        max_output_tokens: int,
        mcp_tools: MCPToolProvider,
        skills: SkillCatalog,
        workspace_context: WorkspaceContext,
        memory: AgentMemory,
    ) -> None:
        if not api_key:
            raise ValueError(f"providers.{provider}.{provider}ApiKey is not configured")
        normalized_model = _normalize_model(provider, model)
        lm_options: dict[str, Any] = {
            "api_key": api_key,
            "max_tokens": max_output_tokens,
            "cache": False,
            "num_retries": 0,
        }
        if _supports_configurable_temperature(normalized_model):
            lm_options["temperature"] = temperature
        self._lm = dspy.LM(normalized_model, **lm_options)
        self._mcp_tools = mcp_tools
        self._skills = skills
        self._workspace_context = workspace_context
        self._workspace_prompt: str | None = None
        self._memory = memory
        self._program: Any | None = None
        self._tool_status_program: Any = dspy.Predict(ToolSpeechStatus)
        self._program_lock = asyncio.Lock()

    async def start(self) -> None:
        with measure_performance("dspy.generator.start"):
            # The workspace summary needs the tool list, so it waits for the program;
            # memory initialization is independent and runs alongside it.
            await asyncio.gather(self._memory.start(), self._build_workspace_prompt())

    async def _build_workspace_prompt(self) -> None:
        await self._get_program()
        self._workspace_prompt = await self._workspace_context.build()

    async def stream(self, messages: list[ConversationMessage]) -> AsyncIterator[str]:
        if self._workspace_prompt is None:
            raise RuntimeError("DSPy generator is not initialized")
        latest_user = latest_user_content(messages)
        memory_context = await self._memory.search(latest_user)
        conversation = (
            f"GET DATE TODAY: {datetime.now().astimezone().isoformat(timespec='seconds')}\n"
            + "\n".join(
                f"{'Assistant' if item.role == 'assistant' else 'User'}: {item.content}"
                for item in messages
            )
        )
        events: asyncio.Queue[MCPToolEvent] = asyncio.Queue()
        with observe_mcp_tool_events(events.put_nowait):
            answer_task = asyncio.create_task(
                self._answer(conversation, self._workspace_prompt, memory_context),
                name="heyclaw-dspy-answer",
            )

        waiting_messages: dict[int, str] = {}
        try:
            while not answer_task.done() or not events.empty():
                event_task = asyncio.create_task(events.get())
                done, _ = await asyncio.wait(
                    {answer_task, event_task},
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if event_task in done:
                    phrase = await self._tool_status(
                        event_task.result(),
                        latest_user,
                        memory_context,
                        waiting_messages,
                    )
                    if phrase:
                        yield f"{phrase} "
                else:
                    event_task.cancel()
                    await asyncio.gather(event_task, return_exceptions=True)

            answer = await answer_task
        finally:
            if not answer_task.done():
                answer_task.cancel()
                await asyncio.gather(answer_task, return_exceptions=True)

        yield answer

    async def _answer(
        self,
        conversation: str,
        workspace_context: str,
        memory_context: str,
    ) -> str:
        program = await self._get_program()
        with dspy.context(lm=self._lm), measure_performance("dspy.agent.generate"):
            prediction = await program.acall(
                workspace_context=workspace_context,
                memory_context=memory_context or "No relevant memories.",
                conversation=conversation,
            )
        answer = str(prediction.answer).strip()
        if not answer:
            raise ValueError("DSPy returned an empty answer")
        return answer

    async def _tool_status(
        self,
        event: MCPToolEvent,
        latest_user: str,
        memory_context: str,
        waiting_messages: dict[int, str],
    ) -> str:
        if event.phase == "waiting":
            return waiting_messages.pop(event.call_id)

        with (
            dspy.context(lm=self._lm),
            measure_performance("dspy.tool_status.generate"),
        ):
            prediction = await self._tool_status_program.acall(
                memory_context=memory_context or "No relevant memories.",
                user_request=latest_user,
                tool_description=event.description,
                tool_arguments=orjson.dumps(event.arguments).decode(),
            )

        acknowledgement = str(prediction.acknowledgement).strip()
        still_waiting = str(prediction.still_waiting).strip()
        if not acknowledgement or not still_waiting:
            raise ValueError("DSPy returned an empty tool acknowledgement")
        waiting_messages[event.call_id] = still_waiting
        return acknowledgement

    async def close(self) -> None:
        with measure_performance("dspy.generator.close"):
            await asyncio.gather(
                self._mcp_tools.close(),
                self._memory.close(),
            )

    async def remember(self, user_message: str, assistant_message: str) -> None:
        await self._memory.add(user_message, assistant_message)

    async def _get_program(self) -> Any:
        if self._program is not None:
            return self._program
        async with self._program_lock:
            if self._program is None:
                mcp_tools = await self._mcp_tools.start()
                self._skills.set_available_tools(
                    self._mcp_tools.available_tool_sources()
                )
                tools = [self._skills.as_dspy_tool(), *mcp_tools]
                self._program = dspy.ReAct(VoiceAssistant, tools=tools, max_iters=4)
        return self._program
