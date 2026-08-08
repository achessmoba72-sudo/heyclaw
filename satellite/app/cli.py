import signal
from contextlib import suppress

from elevenlabs import ElevenLabs
from elevenlabs.conversational_ai.conversation import (
    Conversation,
    ConversationInitiationData,
)
from loguru import logger

from app.audio.pyaudio_interface import (
    PyAudioInterface,
    audio_devices,
)
from app.audio.wake_word import WakeWordDetector
from app.config import get_settings, load_config
from app.logging import configure_logging
from app.performance import measure_performance


def _log_user_transcript(transcript: str) -> None:
    logger.info("You: {}", transcript)


def list_audio_devices() -> None:
    for device in audio_devices():
        capabilities = []
        if device["inputs"]:
            capabilities.append(f"input:{device['inputs']}")
        if device["outputs"]:
            capabilities.append(f"output:{device['outputs']}")
        if capabilities:
            print(
                f"[{device['index']}] {device['name']} "
                f"({' '.join(capabilities)}, {device['sample_rate']} Hz)"
            )


def run() -> None:
    settings = get_settings()
    config = load_config()
    agent = config.defaults.agent
    elevenlabs = config.providers.elevenlabs
    configure_logging(settings)
    if not elevenlabs.elevenlabs_api_key:
        raise RuntimeError(
            "providers.elevenlabs.elevenlabsApiKey is not configured"
        )
    if not elevenlabs.elevenlabs_speech_engine_id:
        raise RuntimeError(
            "providers.elevenlabs.elevenlabsSpeechEngineId is not configured"
        )

    if agent.wake_word_enabled:
        detector = WakeWordDetector(
            model_name=agent.wake_word_model,
            threshold=agent.wake_word_threshold,
            input_device_index=agent.audio_input_device_index,
        )
        score = detector.wait()
        logger.info(
            'Wake word "{}" detected ({:.2f})',
            agent.wake_word_model,
            score,
        )

    audio = PyAudioInterface(
        input_device_index=agent.audio_input_device_index,
        output_device_index=agent.audio_output_device_index,
        gate_microphone_during_playback=agent.echo_suppression_mode == "gate",
        enable_echo_cancellation=agent.echo_suppression_mode == "aec",
        echo_guard_ms=agent.echo_guard_ms,
    )
    conversation = Conversation(
        ElevenLabs(api_key=elevenlabs.elevenlabs_api_key),
        elevenlabs.elevenlabs_speech_engine_id,
        requires_auth=True,
        audio_interface=audio,
        config=ConversationInitiationData(
            conversation_config_override={
                "agent": {"first_message": agent.first_message}
            }
        ),
        callback_user_transcript=_log_user_transcript,
    )
    session_active = False
    try:
        logger.info("Opening microphone and voice session…")
        with measure_performance("elevenlabs.conversation.start"):
            conversation.start_session()  # type: ignore[no-untyped-call]
        session_active = True
        logger.info("Session active. Speak normally; press Ctrl+C to stop.")
        conversation.wait_for_session_end()
    except KeyboardInterrupt:
        logger.info(
            "Closing session…" if session_active else "Session startup canceled."
        )
    finally:
        previous_sigint = signal.signal(signal.SIGINT, signal.SIG_IGN)
        try:
            with measure_performance("elevenlabs.conversation.close"):
                conversation.end_session()  # type: ignore[no-untyped-call]
                with suppress(RuntimeError):
                    conversation.wait_for_session_end()
        finally:
            signal.signal(signal.SIGINT, previous_sigint)
        logger.info("Session closed.")
