import json
from pathlib import Path

import pytest

from app.onboarding import onboard_project, recommend_audio_devices


def paired_devices() -> list[dict[str, object]]:
    return [
        {
            "index": 1,
            "name": "Microphone (Camera)",
            "inputs": 1,
            "outputs": 0,
            "host_api": "MME",
            "default_input": True,
            "default_output": False,
        },
        {
            "index": 2,
            "name": "Echo Cancelling Speakerphone",
            "inputs": 2,
            "outputs": 0,
            "host_api": "MME",
            "default_input": False,
            "default_output": False,
        },
        {
            "index": 4,
            "name": "Echo Cancelling Speakerphone",
            "inputs": 0,
            "outputs": 2,
            "host_api": "MME",
            "default_input": False,
            "default_output": True,
        },
    ]


def test_recommends_capture_endpoint_matching_the_default_output() -> None:
    recommendation = recommend_audio_devices(paired_devices())

    assert recommendation.input_index == 2
    assert recommendation.output_index == 4
    assert recommendation.echo_suppression_mode == "off"


def test_uses_gate_for_unmatched_speakers() -> None:
    devices = [
        {
            "index": 1,
            "name": "USB microphone",
            "inputs": 1,
            "outputs": 0,
            "host_api": "MME",
            "default_input": True,
            "default_output": False,
        },
        {
            "index": 7,
            "name": "Living room speakers",
            "inputs": 0,
            "outputs": 2,
            "host_api": "MME",
            "default_input": False,
            "default_output": True,
        },
    ]

    recommendation = recommend_audio_devices(devices)

    assert recommendation.input_index == 1
    assert recommendation.output_index == 7
    assert recommendation.echo_suppression_mode == "gate"


def test_onboard_creates_both_configs_and_applies_audio_recommendation(
    tmp_path: Path,
) -> None:
    backend = tmp_path / "heyclaw"
    satellite = tmp_path / "satellite"
    backend.mkdir()
    satellite.mkdir()
    backend.joinpath("config.example.json").write_text(
        '{"defaults": {"agent": {}}}', encoding="utf-8"
    )
    satellite.joinpath("config.example.json").write_text(
        '{"defaults": {"agent": {"audioInputDeviceIndex": null, '
        '"audioOutputDeviceIndex": null, "echoSuppressionMode": "off"}}}',
        encoding="utf-8",
    )

    result = onboard_project(tmp_path, paired_devices())

    generated = json.loads(satellite.joinpath("config.json").read_text())
    assert len(result.created) == 2
    assert generated["defaults"]["agent"]["audioInputDeviceIndex"] == 2
    assert generated["defaults"]["agent"]["audioOutputDeviceIndex"] == 4
    assert generated["defaults"]["agent"]["echoSuppressionMode"] == "off"


def test_onboard_refreshes_existing_configs_without_replacing_values(
    tmp_path: Path,
) -> None:
    for component in ("heyclaw", "satellite"):
        directory = tmp_path / component
        directory.mkdir()
    tmp_path.joinpath("heyclaw", "config.example.json").write_text(
        '{"keep": false, "newField": 42}', encoding="utf-8"
    )
    tmp_path.joinpath("heyclaw", "config.json").write_text(
        '{"keep": true}', encoding="utf-8"
    )
    tmp_path.joinpath("satellite", "config.example.json").write_text(
        '{"defaults": {"agent": {"audioInputDeviceIndex": null, '
        '"audioOutputDeviceIndex": null, "echoSuppressionMode": "off", '
        '"newField": 42}}}',
        encoding="utf-8",
    )
    tmp_path.joinpath("satellite", "config.json").write_text(
        '{"defaults": {"agent": {"audioInputDeviceIndex": 8, '
        '"audioOutputDeviceIndex": 9, "echoSuppressionMode": "gate"}}, '
        '"secret": "preserve-me"}',
        encoding="utf-8",
    )

    result = onboard_project(tmp_path, paired_devices())

    assert not result.created
    assert len(result.updated) == 2
    backend = json.loads(tmp_path.joinpath("heyclaw", "config.json").read_text())
    satellite = json.loads(
        tmp_path.joinpath("satellite", "config.json").read_text()
    )
    assert backend == {"keep": True, "newField": 42}
    assert satellite["secret"] == "preserve-me"
    assert satellite["defaults"]["agent"]["audioInputDeviceIndex"] == 8
    assert satellite["defaults"]["agent"]["audioOutputDeviceIndex"] == 9
    assert satellite["defaults"]["agent"]["echoSuppressionMode"] == "gate"
    assert satellite["defaults"]["agent"]["newField"] == 42


def test_onboard_fills_unconfigured_audio_in_existing_scaffold(tmp_path: Path) -> None:
    for component in ("heyclaw", "satellite"):
        directory = tmp_path / component
        directory.mkdir()
    tmp_path.joinpath("heyclaw", "config.example.json").write_text(
        "{}", encoding="utf-8"
    )
    tmp_path.joinpath("heyclaw", "config.json").write_text("{}", encoding="utf-8")
    satellite_config = (
        '{"defaults": {"agent": {"audioInputDeviceIndex": null, '
        '"audioOutputDeviceIndex": null, "echoSuppressionMode": "off"}}}'
    )
    tmp_path.joinpath("satellite", "config.example.json").write_text(
        satellite_config, encoding="utf-8"
    )
    tmp_path.joinpath("satellite", "config.json").write_text(
        satellite_config, encoding="utf-8"
    )

    result = onboard_project(tmp_path, paired_devices())

    generated = json.loads(
        tmp_path.joinpath("satellite", "config.json").read_text()
    )
    assert len(result.updated) == 1
    assert generated["defaults"]["agent"]["audioInputDeviceIndex"] == 2
    assert generated["defaults"]["agent"]["audioOutputDeviceIndex"] == 4
    assert generated["defaults"]["agent"]["echoSuppressionMode"] == "off"


def test_onboard_can_explicitly_replace_an_existing_audio_selection(
    tmp_path: Path,
) -> None:
    for component in ("heyclaw", "satellite"):
        directory = tmp_path / component
        directory.mkdir()
    tmp_path.joinpath("heyclaw", "config.example.json").write_text(
        "{}", encoding="utf-8"
    )
    tmp_path.joinpath("heyclaw", "config.json").write_text("{}", encoding="utf-8")
    satellite_config = (
        '{"defaults": {"agent": {"audioInputDeviceIndex": 30, '
        '"audioOutputDeviceIndex": 31, "echoSuppressionMode": "aec"}}, '
        '"secret": "keep"}'
    )
    tmp_path.joinpath("satellite", "config.example.json").write_text(
        satellite_config, encoding="utf-8"
    )
    tmp_path.joinpath("satellite", "config.json").write_text(
        satellite_config, encoding="utf-8"
    )

    onboard_project(tmp_path, paired_devices(), update_audio=True)

    generated = json.loads(
        tmp_path.joinpath("satellite", "config.json").read_text()
    )
    assert generated["defaults"]["agent"]["audioInputDeviceIndex"] == 2
    assert generated["defaults"]["agent"]["audioOutputDeviceIndex"] == 4
    assert generated["defaults"]["agent"]["echoSuppressionMode"] == "off"
    assert generated["secret"] == "keep"


def test_update_audio_accepts_explicit_connected_devices(tmp_path: Path) -> None:
    for component in ("heyclaw", "satellite"):
        (tmp_path / component).mkdir()
    (tmp_path / "heyclaw" / "config.example.json").write_text(
        "{}", encoding="utf-8"
    )
    (tmp_path / "satellite" / "config.example.json").write_text(
        '{"defaults": {"agent": {"audioInputDeviceIndex": 1, '
        '"audioOutputDeviceIndex": 3, "echoSuppressionMode": "gate"}}}',
        encoding="utf-8",
    )

    onboard_project(
        tmp_path,
        paired_devices(),
        update_audio=True,
        input_device_index=2,
        output_device_index=4,
    )

    updated = json.loads(
        (tmp_path / "satellite" / "config.json").read_text(encoding="utf-8")
    )
    assert updated["defaults"]["agent"]["audioInputDeviceIndex"] == 2
    assert updated["defaults"]["agent"]["audioOutputDeviceIndex"] == 4


def test_update_audio_rejects_index_without_requested_capability(
    tmp_path: Path,
) -> None:
    with pytest.raises(ValueError, match="not a valid input device"):
        onboard_project(
            tmp_path,
            paired_devices(),
            update_audio=True,
            input_device_index=4,
        )


def test_onboard_scaffolds_only_selected_optional_integrations(
    tmp_path: Path,
) -> None:
    backend = tmp_path / "heyclaw"
    satellite = tmp_path / "satellite"
    backend.mkdir()
    satellite.mkdir()
    backend.joinpath("config.example.json").write_text(
        json.dumps(
            {
                "defaults": {
                    "agent": {},
                    "memory": {
                        "mem0": {"apiKey": "your_key_here", "enabled": True}
                    },
                },
                "providers": {
                    "gemini": {"geminiApiKey": ""},
                    "openai": {"openaiApiKey": ""},
                    "anthropic": {"anthropicApiKey": ""},
                },
                "tools": {
                    "mcpServers": {
                        "perplexity": {
                            "command": "npx",
                            "args": ["@perplexity-ai/mcp-server"],
                            "env": {"PERPLEXITY_API_KEY": "your_key_here"},
                            "url": "",
                        }
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    satellite.joinpath("config.example.json").write_text(
        '{"defaults": {"agent": {"audioInputDeviceIndex": null, '
        '"audioOutputDeviceIndex": null, "echoSuppressionMode": "off"}}}',
        encoding="utf-8",
    )

    onboard_project(
        tmp_path,
        paired_devices(),
        integrations=frozenset({"openai", "perplexity"}),
    )

    generated = json.loads(backend.joinpath("config.json").read_text())
    assert generated["providers"]["openai"]["openaiApiKey"] == (
        "your_openai_api_key_here"
    )
    assert "anthropic" not in generated["providers"]
    assert "perplexity" in generated["tools"]["mcpServers"]
    assert generated["defaults"]["memory"]["mem0"] == {
        "apiKey": "",
        "enabled": False,
    }
