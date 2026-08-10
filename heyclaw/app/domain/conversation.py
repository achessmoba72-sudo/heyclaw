from collections.abc import Sequence
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict


class ConversationMessage(BaseModel):
    model_config = ConfigDict(frozen=True)

    role: Literal["user", "assistant"]
    content: str


class SpeechEngineMessage(Protocol):
    role: str
    content: str


def has_meaningful_text(content: str) -> bool:
    return any(character.isalnum() for character in content)


def latest_user_content(messages: Sequence[SpeechEngineMessage]) -> str:
    """Return the most recent user turn, or an empty string when there is none."""
    return next(
        (
            message.content.strip()
            for message in reversed(messages)
            if message.role == "user"
        ),
        "",
    )


def normalize_transcript(
    messages: list[SpeechEngineMessage],
) -> list[ConversationMessage]:
    normalized: list[ConversationMessage] = []
    for message in messages:
        content = message.content.strip()
        role = "assistant" if message.role == "agent" else "user"
        if not content or (role == "user" and not has_meaningful_text(content)):
            continue
        normalized.append(
            ConversationMessage(
                role=role,
                content=content,
            )
        )
    return normalized
