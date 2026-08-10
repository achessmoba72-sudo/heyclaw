from dataclasses import dataclass

# Match the runtime import order: Mem0 must initialize NumPy before DSPy installs
# its lazy module proxy. Importing DSPy first makes qdrant_client fail at collection.
from app.services.memory.memory import Mem0Memory as _Mem0Memory  # noqa: F401


@dataclass
class WireMessage:
    """Speech Engine transcript entry, structurally compatible with SpeechEngineMessage."""

    role: str
    content: str
