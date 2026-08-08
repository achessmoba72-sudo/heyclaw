from pathlib import Path

from app.audio.wake_word import _resolve_model_reference


def test_resolves_bundled_veronica_model() -> None:
    model_path = Path(_resolve_model_reference("veronica"))

    assert model_path.name == "veronica.tflite"
    assert model_path.is_file()


def test_resolves_custom_model_path(tmp_path: Path) -> None:
    model_path = tmp_path / "custom.tflite"
    model_path.touch()

    assert _resolve_model_reference(str(model_path)) == str(model_path.resolve())
