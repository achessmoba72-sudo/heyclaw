from conftest import WireMessage

from app.domain.conversation import normalize_transcript


def test_normalize_transcript_maps_elevenlabs_roles() -> None:
    result = normalize_transcript(
        [
            WireMessage(role="user", content=" Hello "),
            WireMessage(role="agent", content="Hello to you"),
            WireMessage(role="user", content=" "),
            WireMessage(role="user", content="..."),
            WireMessage(role="user", content=" … "),
        ]
    )
    assert [item.model_dump() for item in result] == [
        {"role": "user", "content": "Hello"},
        {"role": "assistant", "content": "Hello to you"},
    ]
