import app.api.routes.health as health_routes
from fastapi.testclient import TestClient
from pytest import MonkeyPatch

from app.main import create_app
from app.services.mcp.config import HeyClawConfig


def test_health() -> None:
    with TestClient(create_app()) as client:
        response = client.get("/api/v1/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_readiness_uses_selected_llm_provider(monkeypatch: MonkeyPatch) -> None:
    config = HeyClawConfig.model_validate(
        {
            "defaults": {
                "agent": {
                    "firstMessage": "Hello",
                    "llmProvider": "openai",
                    "llmModel": "gpt-5-mini",
                },
                "memory": {"mem0": {"apiKey": "mem0-key"}},
            },
            "providers": {
                "openai": {"openaiApiKey": "openai-key"},
                "elevenlabs": {
                    "elevenlabsApiKey": "elevenlabs-key",
                    "elevenlabsSpeechEngineId": "seng_test",
                },
            },
        }
    )
    monkeypatch.setattr(health_routes, "load_config", lambda: config)

    with TestClient(create_app()) as client:
        response = client.get("/api/v1/ready")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ready",
        "speech_engine_configured": True,
        "llm_configured": True,
        "llm_provider": "openai",
        "gemini_configured": False,
        "openai_configured": True,
        "anthropic_configured": False,
        "mem0_configured": True,
        "llm_model": "gpt-5-mini",
    }


def test_readiness_does_not_require_mem0_when_disabled(
    monkeypatch: MonkeyPatch,
) -> None:
    config = HeyClawConfig.model_validate(
        {
            "defaults": {
                "agent": {"firstMessage": "Hello"},
                "memory": {"mem0": {"apiKey": "", "enabled": False}},
            },
            "providers": {
                "gemini": {"geminiApiKey": "gemini-key"},
                "elevenlabs": {
                    "elevenlabsApiKey": "elevenlabs-key",
                    "elevenlabsSpeechEngineId": "seng_test",
                },
            },
        }
    )
    monkeypatch.setattr(health_routes, "load_config", lambda: config)

    with TestClient(create_app()) as client:
        response = client.get("/api/v1/ready")

    assert response.status_code == 200
    assert response.json()["status"] == "ready"
    assert response.json()["mem0_configured"] is False
