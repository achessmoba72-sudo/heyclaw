from collections.abc import AsyncIterator

from conftest import WireMessage

from app.domain.conversation import ConversationMessage
from app.services.speech_engine import SpeechEngineRuntime


class FakeGenerator:
    def __init__(self) -> None:
        self.memory: tuple[str, str] | None = None
        self.stream_calls = 0

    async def start(self) -> None:
        return None

    def stream(self, messages: list[ConversationMessage]) -> AsyncIterator[str]:
        self.stream_calls += 1

        async def chunks() -> AsyncIterator[str]:
            if messages[-1].content == "How are you?":
                yield "Very "
                yield "well."
            else:
                yield "Something new."

        return chunks()

    async def remember(self, user_message: str, assistant_message: str) -> None:
        self.memory = (user_message, assistant_message)

    async def close(self) -> None:
        return None


class FakeSession:
    conversation_id = "conversation-test"

    def __init__(self) -> None:
        self.response = ""
        self.send_calls = 0

    async def send_response(self, response) -> None:
        self.send_calls += 1
        if isinstance(response, str):
            self.response += response
            return
        async for chunk in response:
            self.response += chunk


def make_runtime() -> tuple[SpeechEngineRuntime, FakeGenerator, FakeSession]:
    generator = FakeGenerator()
    runtime = SpeechEngineRuntime(
        api_key="test",
        engine_id="seng_test",
        debug=False,
        response_timeout_seconds=2,
        generator=generator,  # type: ignore[arg-type]
    )
    return runtime, generator, FakeSession()


async def test_transcript_is_streamed_and_persisted() -> None:
    runtime, generator, session = make_runtime()

    await runtime._on_transcript(
        [WireMessage(role="user", content="How are you?")], session
    )

    assert session.response == "Very well."
    assert generator.memory == ("How are you?", "Very well.")
    assert generator.stream_calls == 1

    await runtime._on_transcript(
        [WireMessage(role="user", content="How are you?")], session
    )

    assert session.response == "Very well."
    assert session.send_calls == 1
    assert generator.stream_calls == 1


async def test_punctuation_only_user_turn_is_ignored() -> None:
    runtime, generator, session = make_runtime()

    await runtime._on_transcript(
        [
            WireMessage(role="agent", content="How can I help you?"),
            WireMessage(role="user", content="..."),
        ],
        session,
    )

    assert session.response == ""
    assert session.send_calls == 0
    assert generator.memory is None
    assert generator.stream_calls == 0


async def test_superseded_transcript_replay_is_ignored_without_regeneration() -> None:
    runtime, generator, session = make_runtime()
    first = [WireMessage(role="user", content="How are you?")]
    second = [
        WireMessage(role="user", content="How are you?"),
        WireMessage(role="agent", content="Very well."),
        WireMessage(role="user", content="What is new?"),
    ]

    await runtime._on_transcript(first, session)
    await runtime._on_transcript(second, session)
    await runtime._on_transcript(first, session)

    assert generator.stream_calls == 2
    assert session.send_calls == 2
