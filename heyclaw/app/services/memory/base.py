from typing import Protocol


class AgentMemory(Protocol):
    async def start(self) -> None: ...

    async def search(self, query: str) -> str: ...

    async def add(self, user_message: str, assistant_message: str) -> None: ...

    async def close(self) -> None: ...


class NullMemory:
    """No-op memory used when cloud memory is disabled."""

    async def start(self) -> None:
        pass

    async def search(self, query: str) -> str:
        return ""

    async def add(self, user_message: str, assistant_message: str) -> None:
        pass

    async def close(self) -> None:
        pass
