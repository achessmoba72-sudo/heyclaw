import pytest

from app.services.llm.dspy_backend import (
    _normalize_model,
    _supports_configurable_temperature,
)
from app.services.mcp.config import ProvidersConfig


@pytest.mark.parametrize(
    ("provider", "model", "expected"),
    [
        ("gemini", "gemini-3.1-flash-lite", "gemini/gemini-3.1-flash-lite"),
        ("gemini", "google/gemini-2.5-flash", "gemini/gemini-2.5-flash"),
        ("openai", "gpt-5.6-luna", "openai/gpt-5.6-luna"),
        ("openai", "openai/gpt-5.6-terra", "openai/gpt-5.6-terra"),
        (
            "anthropic",
            "claude-sonnet-5",
            "anthropic/claude-sonnet-5",
        ),
    ],
)
def test_normalizes_provider_model(provider: str, model: str, expected: str) -> None:
    assert _normalize_model(provider, model) == expected  # type: ignore[arg-type]


def test_rejects_model_prefix_for_another_provider() -> None:
    with pytest.raises(ValueError, match="provider prefix does not match"):
        _normalize_model("openai", "anthropic/claude-sonnet-5")


@pytest.mark.parametrize(
    "model",
    [
        "openai/gpt-5.6-luna",
        "openai/gpt-5.4-mini",
        "anthropic/claude-sonnet-5",
        "anthropic/claude-opus-5",
        "anthropic/claude-fable-5",
    ],
)
def test_current_reasoning_models_use_default_temperature(model: str) -> None:
    assert not _supports_configurable_temperature(model)


def test_gemini_and_older_claude_models_keep_configured_temperature() -> None:
    assert _supports_configurable_temperature("gemini/gemini-3.1-flash-lite")
    assert _supports_configurable_temperature("anthropic/claude-sonnet-4-5-20250929")


def test_provider_config_selects_matching_api_key() -> None:
    providers = ProvidersConfig.model_validate(
        {
            "gemini": {"geminiApiKey": "gemini-key"},
            "openai": {"openaiApiKey": "openai-key"},
            "anthropic": {"anthropicApiKey": "anthropic-key"},
        }
    )

    assert providers.api_key_for("gemini") == "gemini-key"
    assert providers.api_key_for("openai") == "openai-key"
    assert providers.api_key_for("anthropic") == "anthropic-key"
