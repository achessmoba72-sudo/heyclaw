from contextlib import suppress
from pathlib import Path
from time import monotonic
from typing import Any

import numpy as np
import pyaudio
from loguru import logger

from app.audio.pyaudio_interface import (
    close_stream,
    suppress_native_audio_probe_noise,
)

_BUNDLED_MODELS = {
    "veronica": Path(__file__).with_name("models") / "veronica.tflite",
    "andromeda": Path(__file__).with_name("models") / "andromeda.tflite",
}


def _resolve_model_reference(model_name: str) -> str:
    bundled_model = _BUNDLED_MODELS.get(model_name)
    if bundled_model is not None:
        return str(bundled_model)

    model_path = Path(model_name).expanduser()
    if model_path.is_file():
        return str(model_path.resolve())

    from openwakeword.utils import download_models

    download_models([model_name])
    return model_name


class WakeWordDetector:
    """Wait for a local openWakeWord activation before starting a voice session."""

    sample_rate = 16_000
    frames_per_buffer = 1_280

    def __init__(
        self,
        *,
        model_name: str,
        threshold: float,
        input_device_index: int | None,
    ) -> None:
        self._model_name = model_name
        self._threshold = threshold
        self._input_device_index = input_device_index
        self._model: Any = None

    def _load_model(self) -> Any:
        """Load the model once and reuse it, so re-arming between sessions is free."""
        if self._model is None:
            from openwakeword.model import Model
            from openwakeword.utils import download_models

            # Also fetches the shared feature models that Model() needs for a bundled path.
            download_models([self._model_name])
            self._model = Model(
                wakeword_models=[_resolve_model_reference(self._model_name)]
            )
        else:
            # Drop the audio buffered before the previous conversation, which would
            # otherwise be scored again and could re-trigger immediately.
            self._model.reset()
        return self._model

    def wait(self) -> float:
        model = self._load_model()
        audio: pyaudio.PyAudio | None = None
        stream = None
        try:
            with suppress_native_audio_probe_noise():
                audio = pyaudio.PyAudio()
                stream = audio.open(
                    format=pyaudio.paInt16,
                    channels=1,
                    rate=self.sample_rate,
                    input=True,
                    input_device_index=self._input_device_index,
                    frames_per_buffer=self.frames_per_buffer,
                )
            logger.info('Listening for wake word "{}"…', self._model_name)
            score_window_started_at = monotonic()
            peak_score = 0.0
            while True:
                frame = stream.read(
                    self.frames_per_buffer,
                    exception_on_overflow=False,
                )
                predictions = model.predict(np.frombuffer(frame, dtype=np.int16))
                score = max((float(value) for value in predictions.values()), default=0)
                peak_score = max(peak_score, score)
                now = monotonic()
                if now - score_window_started_at >= 1:
                    logger.debug(
                        'Wake word analysis model="{}" peak_score={:.3f} threshold={:.3f}',
                        self._model_name,
                        peak_score,
                        self._threshold,
                    )
                    score_window_started_at = now
                    peak_score = 0.0
                if score >= self._threshold:
                    return score
        finally:
            close_stream(stream)
            if audio is not None:
                with suppress(OSError):
                    audio.terminate()
