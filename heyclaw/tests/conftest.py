from dataclasses import dataclass


@dataclass
class WireMessage:
    """Speech Engine transcript entry, structurally compatible with SpeechEngineMessage."""

    role: str
    content: str
