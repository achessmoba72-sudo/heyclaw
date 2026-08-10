import json
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

Integration = Literal["openai", "anthropic", "perplexity", "mem0"]


@dataclass(frozen=True)
class AudioRecommendation:
    input_index: int | None
    output_index: int | None
    echo_suppression_mode: Literal["off", "gate"]
    reason: str


@dataclass(frozen=True)
class OnboardResult:
    created: tuple[Path, ...]
    updated: tuple[Path, ...]
    unchanged: tuple[Path, ...]
    audio: AudioRecommendation


def find_project_root(start: Path | None = None) -> Path:
    """Find a checkout containing both component config examples."""
    candidates: list[Path] = []
    if start is not None:
        resolved = start.resolve()
        candidates.extend((resolved, *resolved.parents))
    package_root = Path(__file__).resolve().parents[2]
    candidates.extend((package_root, *package_root.parents))

    for candidate in dict.fromkeys(candidates):
        if (
            candidate.joinpath("heyclaw", "config.example.json").is_file()
            and candidate.joinpath("satellite", "config.example.json").is_file()
        ):
            return candidate
    raise RuntimeError(
        "HeyClaw repository not found; run onboard from inside a HeyClaw checkout"
    )


def recommend_audio_devices(devices: list[dict[str, Any]]) -> AudioRecommendation:
    """Choose a conservative render/capture pair from PortAudio discovery."""
    default_input = next(
        (device for device in devices if device.get("default_input")), None
    )
    default_output = next(
        (device for device in devices if device.get("default_output")), None
    )

    if default_output is not None:
        output_name = _normalized_name(default_output)
        output_host = default_output.get("host_api")
        paired_input = next(
            (
                device
                for device in devices
                if device.get("inputs")
                and _normalized_name(device) == output_name
                and device.get("host_api") == output_host
            ),
            None,
        )
        if paired_input is not None:
            return AudioRecommendation(
                input_index=int(paired_input["index"]),
                output_index=int(default_output["index"]),
                echo_suppression_mode="off",
                reason=(
                    "the default output has a matching capture endpoint on the same "
                    "PortAudio host API"
                ),
            )

    input_index = int(default_input["index"]) if default_input is not None else None
    output_index = int(default_output["index"]) if default_output is not None else None
    output_name = _normalized_name(default_output) if default_output else ""
    headphones = any(
        marker in output_name for marker in ("headphone", "headset", "earbud")
    )
    if headphones and input_index is not None:
        return AudioRecommendation(
            input_index=input_index,
            output_index=output_index,
            echo_suppression_mode="off",
            reason="the default output appears to be headphones and cannot feed the microphone",
        )

    return AudioRecommendation(
        input_index=input_index,
        output_index=output_index,
        echo_suppression_mode="gate",
        reason=(
            "no matching capture/playback endpoint was detected; gate is the safe "
            "speaker fallback"
        ),
    )


def onboard_project(
    project_root: Path,
    devices: list[dict[str, Any]],
    *,
    update_audio: bool = False,
    input_device_index: int | None = None,
    output_device_index: int | None = None,
    integrations: frozenset[Integration] | None = None,
) -> OnboardResult:
    """Create missing configs or add missing fields without replacing user values."""
    audio = recommend_audio_devices(devices)
    if input_device_index is not None:
        _validate_device_index(devices, input_device_index, "inputs")
        audio = AudioRecommendation(
            input_device_index,
            audio.output_index,
            audio.echo_suppression_mode,
            "the input device was selected explicitly",
        )
    if output_device_index is not None:
        _validate_device_index(devices, output_device_index, "outputs")
        audio = AudioRecommendation(
            audio.input_index,
            output_device_index,
            audio.echo_suppression_mode,
            "the audio devices were selected explicitly",
        )
    created: list[Path] = []
    updated: list[Path] = []
    unchanged: list[Path] = []
    configs = (
        (
            project_root / "heyclaw" / "config.example.json",
            project_root / "heyclaw" / "config.json",
        ),
        (
            project_root / "satellite" / "config.example.json",
            project_root / "satellite" / "config.json",
        ),
    )

    writes: list[tuple[Path, dict[str, Any], bool]] = []
    for example, target in configs:
        defaults = json.loads(example.read_text(encoding="utf-8"))
        existed = target.exists()
        if target.parent.name == "heyclaw" and integrations is not None:
            _filter_integration_defaults(defaults, integrations)
        data = (
            json.loads(target.read_text(encoding="utf-8"))
            if existed
            else deepcopy(defaults)
        )
        original = deepcopy(data)
        _merge_missing(data, defaults)
        if target.parent.name == "heyclaw" and integrations is not None:
            _scaffold_selected_integrations(data, integrations)
        elif target.parent.name == "satellite":
            agent = data["defaults"]["agent"]
            audio_is_unconfigured = (
                agent.get("audioInputDeviceIndex") is None
                and agent.get("audioOutputDeviceIndex") is None
            )
            if audio_is_unconfigured or update_audio:
                agent["audioInputDeviceIndex"] = audio.input_index
                agent["audioOutputDeviceIndex"] = audio.output_index
                if update_audio or agent.get("echoSuppressionMode") in (None, "off"):
                    agent["echoSuppressionMode"] = audio.echo_suppression_mode
        if not existed or data != original:
            writes.append((target, data, existed))
        else:
            unchanged.append(target)

    # Parse and prepare every file before replacing either one, so malformed local
    # JSON cannot leave onboarding half-applied.
    for target, data, existed in writes:
        _write_json_atomic(target, data)
        (updated if existed else created).append(target)

    return OnboardResult(
        created=tuple(created),
        updated=tuple(updated),
        unchanged=tuple(unchanged),
        audio=audio,
    )


def _normalized_name(device: dict[str, Any] | None) -> str:
    if device is None:
        return ""
    return " ".join(str(device.get("name", "")).casefold().split())


def _filter_integration_defaults(
    defaults: dict[str, Any], integrations: frozenset[Integration]
) -> None:
    providers = defaults["providers"]
    for provider in ("openai", "anthropic"):
        if provider not in integrations:
            providers.pop(provider, None)
    if "perplexity" not in integrations:
        defaults["tools"]["mcpServers"].pop("perplexity", None)
    mem0 = defaults["defaults"]["memory"]["mem0"]
    mem0["enabled"] = "mem0" in integrations
    mem0["apiKey"] = "your_mem0_api_key_here" if "mem0" in integrations else ""


def _scaffold_selected_integrations(
    data: dict[str, Any], integrations: frozenset[Integration]
) -> None:
    providers = data.setdefault("providers", {})
    if "openai" in integrations:
        openai = providers.setdefault("openai", {})
        if not openai.get("openaiApiKey"):
            openai["openaiApiKey"] = "your_openai_api_key_here"
    if "anthropic" in integrations:
        anthropic = providers.setdefault("anthropic", {})
        if not anthropic.get("anthropicApiKey"):
            anthropic["anthropicApiKey"] = "your_anthropic_api_key_here"
    if "mem0" in integrations:
        mem0 = data["defaults"]["memory"]["mem0"]
        mem0["enabled"] = True
        if not mem0.get("apiKey"):
            mem0["apiKey"] = "your_mem0_api_key_here"


def _validate_device_index(
    devices: list[dict[str, Any]], index: int, capability: Literal["inputs", "outputs"]
) -> None:
    device = next((item for item in devices if item.get("index") == index), None)
    if device is None or not device.get(capability):
        kind = "input" if capability == "inputs" else "output"
        raise ValueError(f"PortAudio device {index} is not a valid {kind} device")


def _merge_missing(current: dict[str, Any], defaults: dict[str, Any]) -> None:
    for key, default in defaults.items():
        if key not in current:
            current[key] = deepcopy(default)
        elif isinstance(current[key], dict) and isinstance(default, dict):
            _merge_missing(current[key], default)


def _write_json_atomic(path: Path, data: dict[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    temporary.replace(path)
