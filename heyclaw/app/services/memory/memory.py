import asyncio
from time import monotonic
from typing import Any

import orjson
from heyclaw_shared.performance import measure_performance
from loguru import logger
from mem0 import AsyncMemoryClient

_USER_ID = "local-user"
_SEARCH_LIMIT = 5
_PROFILE_PAGE_SIZE = 100
_PROFILE_CATEGORIES = ("user_preferences", "personal_details")
_EVENT_POLL_SECONDS = 0.25
_EVENT_TIMEOUT_SECONDS = 15.0
_CUSTOM_INSTRUCTIONS = """Extract only durable user-specific information useful across future sessions:
- Personal details explicitly stated by the user
- Stable preferences, habits, constraints, languages, and relationships
- Long-term goals, ongoing projects, explicit requests to remember, and corrections

Exclude:
- Questions, commands, one-off requests, and transient intentions
- Web searches, search topics or results, current events, and time-sensitive facts
- Assistant or tool output, praise, greetings, and small talk
- Uncertain inferences based only on what the user asks

If no durable user-specific information is present, create no memory."""


class Mem0Memory:
    """Mandatory per-user semantic memory backed by Mem0 Platform."""

    def __init__(self, *, api_key: str) -> None:
        if not api_key:
            raise RuntimeError(
                "defaults.memory.mem0.apiKey is not configured in config.json"
            )
        self._api_key = api_key
        self._client: AsyncMemoryClient | None = None
        self._profile_memories: tuple[str, ...] = ()
        self._profile_lock = asyncio.Lock()

    async def start(self) -> None:
        if self._client is not None:
            return
        with measure_performance("mem0.client.initialize"):
            # The constructor performs blocking setup; running it inline would stall the
            # event loop and serialize everything started alongside it.
            self._client = await asyncio.to_thread(
                AsyncMemoryClient, api_key=self._api_key
            )
        await self._refresh_profile()
        logger.info("Mem0 connected ({} profile memories)", len(self._profile_memories))

    def profile_context(self) -> str:
        return _format_memories(self._profile_memories)

    async def search(self, query: str) -> str:
        client = self._get_client()
        with measure_performance("mem0.memory.search"):
            response = await client.search(
                query,
                filters={
                    "AND": [
                        {"user_id": _USER_ID},
                        {"NOT": {"categories": {"in": list(_PROFILE_CATEGORIES)}}},
                    ]
                },
                top_k=_SEARCH_LIMIT,
            )

        memories = _memory_results(response)
        logger.debug("Mem0: {} relevant memories", len(memories))
        return _format_memories(memories)

    async def add(self, user_message: str, assistant_message: str) -> None:
        client = self._get_client()
        messages = [
            {"role": "user", "content": user_message},
            {"role": "assistant", "content": assistant_message},
        ]
        with measure_performance("mem0.memory.add"):
            response = await client.add(
                messages,
                user_id=_USER_ID,
                custom_instructions=_CUSTOM_INSTRUCTIONS,
            )
        logger.opt(lazy=True).debug("{}", lambda: orjson.dumps(response).decode())
        event_id = response.get("event_id")
        if isinstance(event_id, str):
            await self._wait_for_event(event_id)
            await self._refresh_profile()

    async def close(self) -> None:
        if self._client is not None:
            with measure_performance("mem0.client.close"):
                await self._client.async_client.aclose()
            self._client = None

    def _get_client(self) -> AsyncMemoryClient:
        if self._client is None:
            raise RuntimeError("Mem0 client is not initialized")
        return self._client

    async def _refresh_profile(self) -> None:
        client = self._get_client()
        async with self._profile_lock:
            with measure_performance("mem0.memory.profile.load"):
                response = await client.get_all(
                    filters={
                        "AND": [
                            {"user_id": _USER_ID},
                            {"categories": {"in": list(_PROFILE_CATEGORIES)}},
                        ]
                    },
                    page=1,
                    page_size=_PROFILE_PAGE_SIZE,
                )
            self._profile_memories = tuple(_memory_results(response))
            logger.debug(
                "Mem0: {} profile memories cached", len(self._profile_memories)
            )

    async def _wait_for_event(self, event_id: str) -> None:
        client = self._get_client()
        deadline = monotonic() + _EVENT_TIMEOUT_SECONDS
        while True:
            response = await client.async_client.get(f"/v1/event/{event_id}/")
            response.raise_for_status()
            event = response.json()
            status = event.get("status")
            if status == "SUCCEEDED":
                return
            if status == "FAILED":
                raise RuntimeError(f"Mem0 memory event failed: {event_id}")
            if monotonic() >= deadline:
                raise TimeoutError(f"Mem0 memory event timed out: {event_id}")
            await asyncio.sleep(_EVENT_POLL_SECONDS)


def _memory_results(response: Any) -> list[str]:
    memories: list[str] = []
    for item in response["results"]:
        memory = item["memory"]
        if not isinstance(memory, str):
            raise TypeError("Invalid Mem0 response")
        memories.append(memory)
    return memories


def _format_memories(memories: Any) -> str:
    return "\n".join(f"- {memory}" for memory in memories)
