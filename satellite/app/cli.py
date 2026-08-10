import argparse
import json
import signal
import sys
from contextlib import suppress
from pathlib import Path
from time import sleep

import questionary
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
from app.onboarding import (
    Integration,
    find_project_root,
    onboard_project,
    recommend_audio_devices,
)

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
            defaults = []
            if device["default_input"]:
                defaults.append("default input")
            if device["default_output"]:
                defaults.append("default output")
            suffix = f", {', '.join(defaults)}" if defaults else ""
            print(
                f"[{device['index']}] {device['name']} "
                f"({' '.join(capabilities)}, {device['sample_rate']} Hz, "
                f"{device['host_api']}{suffix})"
            )


def onboard(
    project_root: Path | None = None,
    *,
    update_audio: bool = False,
    input_device_index: int | None = None,
    output_device_index: int | None = None,
) -> None:
    root = find_project_root(project_root or Path.cwd())
    devices = audio_devices()
    integrations = (
        _select_integrations(root / "heyclaw" / "config.json")
        if not update_audio and sys.stdin.isatty()
        else None
    )
    if (
        update_audio or _audio_is_unconfigured(root / "satellite" / "config.json")
    ) and sys.stdin.isatty():
        recommendation = recommend_audio_devices(devices)
        if input_device_index is None:
            input_device_index = _select_audio_device(
                devices, "input", recommendation.input_index
            )
        if output_device_index is None:
            output_device_index = _select_audio_device(
                devices, "output", recommendation.output_index
            )
    result = onboard_project(
        root,
        devices,
        update_audio=update_audio,
        input_device_index=input_device_index,
        output_device_index=output_device_index,
        integrations=integrations,
    )

    for path in result.created:
        print(f"Created {path.relative_to(root)}")
    for path in result.updated:
        print(f"Updated {path.relative_to(root)} (existing values preserved)")
    for path in result.unchanged:
        print(f"Already up to date: {path.relative_to(root)}")

    audio = result.audio
    print("\nAudio recommendation:")
    print(f"  audioInputDeviceIndex: {audio.input_index}")
    print(f"  audioOutputDeviceIndex: {audio.output_index}")
    print(f"  echoSuppressionMode: {audio.echo_suppression_mode}")
    print(f"  Reason: {audio.reason}.")
    satellite_config = root / "satellite" / "config.json"
    if not update_audio and satellite_config not in result.created:
        print(
            "  Existing audio selections were preserved; use --update-audio to "
            "replace them."
        )
    print("\nNext: fill provider keys, IDs, publicWsUrl, and Mem0/MCP settings.")


def _audio_is_unconfigured(config_path: Path) -> bool:
    if not config_path.exists():
        return True
    data = json.loads(config_path.read_text(encoding="utf-8"))
    agent = data.get("defaults", {}).get("agent", {})
    return (
        agent.get("audioInputDeviceIndex") is None
        and agent.get("audioOutputDeviceIndex") is None
    )


def _select_audio_device(
    devices: list[dict[str, object]], kind: str, default: int | None
) -> int:
    capability = "inputs" if kind == "input" else "outputs"
    choices = [
        questionary.Choice(
            title=(
                f"[{device['index']}] {device['name']} — "
                f"{device[capability]} channel(s), {device['host_api']}"
            ),
            value=device["index"],
        )
        for device in devices
        if device.get(capability)
    ]
    if not choices:
        raise ValueError(f"no PortAudio {kind} devices were detected")
    selected = questionary.select(
        f"Select the {kind} device:",
        choices=choices,
        default=default,
        use_shortcuts=True,
    ).ask()
    if selected is None:
        raise SystemExit(130)
    return int(selected)


def _select_integrations(config_path: Path) -> frozenset[Integration]:
    configured: set[str] = set()
    if config_path.exists():
        data = json.loads(config_path.read_text(encoding="utf-8"))
        providers = data.get("providers", {})
        if providers.get("openai", {}).get("openaiApiKey"):
            configured.add("openai")
        if providers.get("anthropic", {}).get("anthropicApiKey"):
            configured.add("anthropic")
        if "perplexity" in data.get("tools", {}).get("mcpServers", {}):
            configured.add("perplexity")
        if data.get("defaults", {}).get("memory", {}).get("mem0", {}).get("enabled"):
            configured.add("mem0")

    choices = [
        questionary.Choice(
            "OpenAI models", value="openai", checked="openai" in configured
        ),
        questionary.Choice(
            "Anthropic models",
            value="anthropic",
            checked="anthropic" in configured,
        ),
        questionary.Choice(
            "Perplexity web search",
            value="perplexity",
            checked="perplexity" in configured,
        ),
        questionary.Choice(
            "Mem0 cloud memory", value="mem0", checked="mem0" in configured
        ),
    ]
    selected = questionary.checkbox(
        "Which optional integrations should be scaffolded?",
        choices=choices,
        instruction="Space to toggle, Enter to confirm",
    ).ask()
    if selected is None:
        raise SystemExit(130)
    return frozenset(selected)


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="heyclaw-satellite", description="HeyClaw voice satellite"
    )
    commands = parser.add_subparsers(dest="command")
    commands.add_parser("run", help="start wake-word detection and voice sessions")
    commands.add_parser("devices", help="list available PortAudio devices")
    onboard_parser = commands.add_parser(
        "onboard",
        help="create local config scaffolds and recommend audio devices",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""echo suppression modes:
  off   Full duplex with no software suppression. Best for headphones or a
        hardware echo-cancelling speakerphone; unsafe with ordinary speakers.
  gate  Mutes the microphone during playback and for echoGuardMs afterward.
        Safest default for speakers, but the user cannot interrupt playback.
  aec   Software acoustic echo cancellation using playback as its reference.
        Allows interruption, but depends strongly on the audio devices/driver.""",
    )
    onboard_parser.add_argument(
        "--project-root",
        type=Path,
        help="HeyClaw checkout root; normally discovered automatically",
    )
    onboard_parser.add_argument(
        "--update-audio",
        action="store_true",
        help="reselect existing audio indices and refresh the echo mode",
    )
    onboard_parser.add_argument(
        "--input-device-index",
        type=int,
        help="use this PortAudio input index when updating audio",
    )
    onboard_parser.add_argument(
        "--output-device-index",
        type=int,
        help="use this PortAudio output index when updating audio",
    )
    args = parser.parse_args()

    if args.command == "onboard":
        explicit_audio = (
            args.input_device_index is not None or args.output_device_index is not None
        )
        if explicit_audio and not args.update_audio:
            parser.error("device indices require --update-audio")
        try:
            onboard(
                args.project_root,
                update_audio=args.update_audio,
                input_device_index=args.input_device_index,
                output_device_index=args.output_device_index,
            )
        except ValueError as error:
            parser.error(str(error))
    elif args.command == "devices":
        list_audio_devices()
    else:
        run()


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
