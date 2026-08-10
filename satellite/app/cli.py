import signal
from contextlib import suppress
from time import sleep

from elevenlabs import ElevenLabs
from elevenlabs.conversational_ai.conversation import (
    Conversation,
    ConversationInitiationData,
)
from heyclaw_shared.performance import measure_performance
from heyclaw_shared.settings import get_settings
from loguru import logger

from app.audio.pyaudio_interface import (
    PyAudioInterface,
    audio_devices,
)
from app.audio.wake_word import WakeWordDetector
from app.config import AgentConfig, load_config
from app.logging import configure_logging

_DEVICE_SETTLE_SECONDS = 0.5
_SESSION_RETRY_SECONDS = 2.0


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
    api_key = elevenlabs.require_api_key()
    speech_engine_id = elevenlabs.require_speech_engine_id()
    client = ElevenLabs(api_key=api_key)
    detector = (
        WakeWordDetector(
            model_name=agent.wake_word_model,
            threshold=agent.wake_word_threshold,
            input_device_index=agent.audio_input_device_index,
        )
        if agent.wake_word_enabled
        else None
    )

    try:
        while True:
            if detector is not None:
                score = detector.wait()
                logger.info(
                    'Wake word "{}" detected ({:.2f})',
                    agent.wake_word_model,
                    score,
                )
            try:
                _run_session(client, speech_engine_id, agent)
            except KeyboardInterrupt:
                raise
            except Exception as error:
                # A dropped connection or a refused session must not kill the satellite:
                # log it and fall back to listening for the wake word.
                logger.opt(exception=error).error("Voice session ended unexpectedly")
                sleep(_SESSION_RETRY_SECONDS)
            # Give the audio backend time to release the microphone before re-arming it.
            sleep(
                _DEVICE_SETTLE_SECONDS
                if detector is not None
                else _SESSION_RETRY_SECONDS
            )
    except KeyboardInterrupt:
        logger.info("Satellite stopped.")


def _run_session(client: ElevenLabs, speech_engine_id: str, agent: AgentConfig) -> None:
    audio = PyAudioInterface(
        input_device_index=agent.audio_input_device_index,
        output_device_index=agent.audio_output_device_index,
        gate_microphone_during_playback=agent.echo_suppression_mode == "gate",
        enable_echo_cancellation=agent.echo_suppression_mode == "aec",
        echo_guard_ms=agent.echo_guard_ms,
    )
    conversation = Conversation(
        client,
        speech_engine_id,
        requires_auth=True,
        audio_interface=audio,
        config=ConversationInitiationData(
            conversation_config_override={
                "agent": {"first_message": agent.first_message}
            }
        ),
        callback_user_transcript=_log_user_transcript,
    )
    interrupted = False
    try:
        logger.info("Opening microphone and voice session…")
        with measure_performance("elevenlabs.conversation.start"):
            conversation.start_session()  # type: ignore[no-untyped-call]
        logger.info("Session active. Speak normally; press Ctrl+C to stop.")
        conversation.wait_for_session_end()
    except KeyboardInterrupt:
        interrupted = True
        logger.info("Closing session…")
    finally:
        previous_sigint = signal.signal(signal.SIGINT, signal.SIG_IGN)
        try:
            with measure_performance("elevenlabs.conversation.close"):
                # Both raise RuntimeError when the session never opened, which is exactly
                # the case the loop needs to survive.
                with suppress(RuntimeError):
                    conversation.end_session()  # type: ignore[no-untyped-call]
                with suppress(RuntimeError):
                    conversation.wait_for_session_end()
        finally:
            signal.signal(signal.SIGINT, previous_sigint)
        logger.info("Session closed.")
    if interrupted:
        raise KeyboardInterrupt
