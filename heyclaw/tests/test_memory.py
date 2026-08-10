from types import SimpleNamespace
from typing import Any

import app.services.memory.memory as memory_module
from app.services.memory.memory import Mem0Memory


class FakeResponse:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, Any]:
        return self._payload


class FakeMem0Client:
    def __init__(self) -> None:
        self.get_all_calls: list[dict[str, Any]] = []
        self.search_calls: list[tuple[str, dict[str, Any]]] = []
        self.event_calls = 0
        self.profile_results = [
            {"memory": "User prefers Italian"},
            {"memory": "User lives in Rome"},
        ]
        self.async_client = SimpleNamespace(
            get=self.get_event,
            aclose=self.aclose,
        )

    async def get_all(self, **kwargs: Any) -> dict[str, Any]:
        self.get_all_calls.append(kwargs)
        return {"results": self.profile_results}

    async def search(self, query: str, **kwargs: Any) -> dict[str, Any]:
        self.search_calls.append((query, kwargs))
        return {"results": [{"memory": "Published GeoSonar articles"}]}

    async def add(self, *_args: Any, **_kwargs: Any) -> dict[str, Any]:
        self.profile_results.append({"memory": "User addresses assistant as Andromeda"})
        return {"event_id": "event-test", "status": "PENDING"}

    async def get_event(self, path: str) -> FakeResponse:
        assert path == "/v1/event/event-test/"
        self.event_calls += 1
        status = "PENDING" if self.event_calls == 1 else "SUCCEEDED"
        return FakeResponse({"status": status})

    async def aclose(self) -> None:
        return None


async def make_memory(monkeypatch: Any) -> tuple[Mem0Memory, FakeMem0Client]:
    client = FakeMem0Client()
    monkeypatch.setattr(memory_module, "AsyncMemoryClient", lambda **_kwargs: client)
    monkeypatch.setattr(memory_module, "_EVENT_POLL_SECONDS", 0)
    memory = Mem0Memory(api_key="test")
    await memory.start()
    return memory, client


async def test_profile_is_loaded_once_and_semantic_search_is_on_demand(
    monkeypatch: Any,
) -> None:
    memory, client = await make_memory(monkeypatch)

    assert memory.profile_context() == "- User prefers Italian\n- User lives in Rome"
    assert len(client.get_all_calls) == 1
    assert client.search_calls == []

    assert await memory.search("What did I publish?") == "- Published GeoSonar articles"
    assert len(client.search_calls) == 1
    filters = client.search_calls[0][1]["filters"]
    assert filters["AND"][1]["NOT"]["categories"]["in"] == [
        "user_preferences",
        "personal_details",
    ]


async def test_completed_add_refreshes_the_profile_cache(monkeypatch: Any) -> None:
    memory, client = await make_memory(monkeypatch)

    await memory.add("Call yourself Andromeda", "I'll remember that.")

    assert client.event_calls == 2
    assert len(client.get_all_calls) == 2
    assert "Andromeda" in memory.profile_context()
