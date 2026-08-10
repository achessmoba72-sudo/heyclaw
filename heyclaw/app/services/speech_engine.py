import asyncio
from collections.abc import AsyncIterator
from functools import partial
from time import monotonic
from typing import Any

import httpx
import orjson
from elevenlabs import AsyncElevenLabs
from heyclaw_shared.performance import measure_performance
from loguru import logger

from app.domain.conversation import (
    has_meaningful_text,
    latest_user_content,
    normalize_transcript,
)
from app.services.llm.dspy_backend import DspyResponseGenerator

_SPEECH_ENGINE_PORT = 3001
_SPEECH_ENGINE_PATH = "/ws"


class _SharedResponse:
    """One generator run that can survive replacement transcript handlers."""

    def __init__(
        self,
        fingerprint: tuple[tuple[str, str], ...],
        stream: AsyncIterator[str],
        *,
        conversation_id: str,
    ) -> None:
        self.fingerprint = fingerprint
        self.persisted = False
        self.done = False
        self._chunks: list[str] = []
        self._delivered_chunks = 0
        self._condition = asyncio.Condition()
        self._error: BaseException | None = None
        self._task = asyncio.create_task(
            self._consume(stream),
            name=f"generate-response-{conversation_id}",
        )

    async def subscribe(self) -> AsyncIterator[str]:
        index = 0
        while True:
            async with self._condition:
                await self._condition.wait_for(
                    lambda current_index=index: (
                        current_index < len(self._chunks) or self.done
                    )
                )
                # A replacement SDK handler resumes the same response. Chunks already
                # handed to the previous handler must not be spoken a second time.
                start = max(index, self._delivered_chunks)
                index = start
                chunks = self._chunks[start:]
                done = self.done
                error = self._error
            for chunk_index, chunk in enumerate(chunks, start=start):
                try:
                    yield chunk
                finally:
                    # Advance only after this individual chunk was handed to the
                    # subscriber. If it is replaced mid-batch, later chunks remain.
                    index = chunk_index + 1
                    async with self._condition:
                        self._delivered_chunks = max(self._delivered_chunks, index)
            if done:
                if error is not None:
                    raise error
                return

    async def close(self) -> None:
        if not self._task.done():
            self._task.cancel()
        await asyncio.gather(self._task, return_exceptions=True)

    async def _consume(self, stream: AsyncIterator[str]) -> None:
        try:
            async for chunk in stream:
                async with self._condition:
                    self._chunks.append(chunk)
                    self._condition.notify_all()
        except BaseException as exc:
            self._error = exc
        finally:
            async with self._condition:
                self.done = True
                self._condition.notify_all()


class SpeechEngineRuntime:
    def __init__(
        self,
        *,
        api_key: str,
        engine_id: str,
        debug: bool,
        response_timeout_seconds: float,
        generator: DspyResponseGenerator,
    ) -> None:
        self._api_key = api_key
        self._engine_id = engine_id
        self._debug = debug
        self._response_timeout_seconds = response_timeout_seconds
        self._generator = generator
        self._client: AsyncElevenLabs | None = None
        self._http_client: httpx.AsyncClient | None = None
        self._responses: dict[str, _SharedResponse] = {}
        self._seen_transcripts: dict[str, set[tuple[tuple[str, str], ...]]] = {}
        self._memory_writes: set[asyncio.Task[None]] = set()

    async def serve(self) -> None:
        with measure_performance("speech_engine.agent.initialize"):
            await self._generator.start()
        self._http_client = httpx.AsyncClient(timeout=30)
        self._client = AsyncElevenLabs(
            api_key=self._api_key,
            httpx_client=self._http_client,
        )
        with measure_performance("elevenlabs.speech_engine.get"):
            engine = await self._client.speech_engine.get(self._engine_id)
        logger.info(
            "Speech Engine {} listening on port {}{}",
            self._engine_id,
            _SPEECH_ENGINE_PORT,
            _SPEECH_ENGINE_PATH,
        )
        await engine.serve(
            port=_SPEECH_ENGINE_PORT,
            path=_SPEECH_ENGINE_PATH,
            debug=self._debug,
            disable_auth=False,
            on_init=self._on_init,
            on_transcript=self._on_transcript,
            on_close=self._on_close,
            on_disconnect=self._on_disconnect,
            on_error=self._on_error,
        )

    async def close(self) -> None:
        for response in self._responses.values():
            await response.close()
        self._responses.clear()
        self._seen_transcripts.clear()
        if self._memory_writes:
            # Let pending writes finish rather than losing what the user just said.
            await asyncio.gather(*tuple(self._memory_writes), return_exceptions=True)
        await self._generator.close()
        if self._http_client is not None:
            await self._http_client.aclose()

    async def _on_init(self, conversation_id: str, _session: Any) -> None:
        logger.bind(conversation_id=conversation_id).info("Conversation started")

    async def _on_transcript(self, transcript: list[Any], session: Any) -> None:
        conversation_id = session.conversation_id
        if not conversation_id:
            raise RuntimeError("Missing conversation ID")
        bound_logger = logger.bind(conversation_id=conversation_id)
        latest_user = latest_user_content(transcript)
        if not has_meaningful_text(latest_user):
            bound_logger.debug("User turn has no semantic content: ignoring it")
            return

        messages = normalize_transcript(transcript)
        bound_logger.opt(lazy=True).debug(
            "{}",
            lambda: orjson.dumps(
                [
                    {"role": message.role, "content": message.content}
                    for message in messages
                ]
            ).decode(),
        )
        fingerprint = tuple((message.role, message.content) for message in messages)
        response = self._responses.get(conversation_id)
        seen_transcripts = self._seen_transcripts.setdefault(conversation_id, set())
        if fingerprint in seen_transcripts and (
            response is None or response.fingerprint != fingerprint or response.done
        ):
            bound_logger.debug("Previously handled transcript replayed: ignoring it")
            return
        seen_transcripts.add(fingerprint)
        if response is None or response.fingerprint != fingerprint:
            if response is not None:
                await response.close()
            response = _SharedResponse(
                fingerprint,
                self._generator.stream(messages),
                conversation_id=conversation_id,
            )
            self._responses[conversation_id] = response
        else:
            bound_logger.debug("Duplicate transcript: reusing the active response")
        started_at = monotonic()
        response_parts: list[str] = []
        stream = self._instrument_stream(
            response.subscribe(),
            bound_logger=bound_logger,
            started_at=started_at,
            response_parts=response_parts,
        )
        completed = False
        try:
            async with asyncio.timeout(self._response_timeout_seconds):
                with measure_performance("speech_engine.response"):
                    await session.send_response(stream)
                completed = True
        except TimeoutError:
            bound_logger.warning(
                "Generation stopped after {} seconds", self._response_timeout_seconds
            )
            await session.send_response("I'm sorry, the response is taking too long.")
        finally:
            if completed:
                spoken = "".join(response_parts).strip()
                if spoken:
                    bound_logger.info("HeyClaw: {}", spoken)
                bound_logger.debug(
                    "Response completed in {:.0f} ms", (monotonic() - started_at) * 1000
                )
                if response_parts and not response.persisted:
                    response.persisted = True
                    self._remember(latest_user, spoken, conversation_id=conversation_id)

    def _remember(
        self, user_message: str, assistant_message: str, *, conversation_id: str
    ) -> None:
        """Persist the exchange without holding up the next transcript."""
        task = asyncio.create_task(
            self._generator.remember(user_message, assistant_message),
            name=f"remember-{conversation_id}",
        )
        self._memory_writes.add(task)
        task.add_done_callback(partial(self._on_memory_write_done, conversation_id))

    def _on_memory_write_done(
        self, conversation_id: str, task: asyncio.Task[None]
    ) -> None:
        self._memory_writes.discard(task)
        if task.cancelled():
            return
        error = task.exception()
        if error is not None:
            logger.bind(conversation_id=conversation_id).opt(exception=error).warning(
                "Storing the conversation memory failed"
            )

    @staticmethod
    async def _instrument_stream(
        stream: AsyncIterator[str],
        *,
        bound_logger: Any,
        started_at: float,
        response_parts: list[str],
    ) -> AsyncIterator[str]:
        first_chunk = True
        async for chunk in stream:
            if first_chunk:
                first_chunk = False
                bound_logger.debug(
                    "First token in {:.0f} ms", (monotonic() - started_at) * 1000
                )
            response_parts.append(chunk)
            yield chunk

    async def _on_close(self, session: Any) -> None:
        await self._close_response(session.conversation_id)
        logger.bind(conversation_id=session.conversation_id).info("Conversation ended")

    async def _on_disconnect(self, session: Any) -> None:
        await self._close_response(session.conversation_id)
        logger.bind(conversation_id=session.conversation_id).warning(
            "Conversation disconnected"
        )

    async def _on_error(self, error: Exception, session: Any) -> None:
        logger.bind(conversation_id=session.conversation_id).opt(exception=error).error(
            "Speech Engine error"
        )

    async def _close_response(self, conversation_id: str | None) -> None:
        if conversation_id is None:
            return
        self._seen_transcripts.pop(conversation_id, None)
        response = self._responses.pop(conversation_id, None)
        if response is not None:
            await response.close()
