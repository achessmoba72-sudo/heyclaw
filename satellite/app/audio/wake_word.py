from contextlib import suppress
from pathlib import Path
from time import monotonic

import numpy as np
import pyaudio
from loguru import logger

from app.audio.pyaudio_interface import (
    _suppress_native_audio_probe_noise,
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

    def wait(self) -> float:
        from openwakeword.model import Model
        from openwakeword.utils import download_models

        download_models([self._model_name])
        model_reference = _resolve_model_reference(self._model_name)
        model = Model(wakeword_models=[model_reference])
        audio: pyaudio.PyAudio | None = None
        stream = None
        try:
            with _suppress_native_audio_probe_noise():
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
            if stream is not None:
                with suppress(OSError):
                    if stream.is_active():
                        stream.stop_stream()
                with suppress(OSError):
                    stream.close()
            if audio is not None:
                with suppress(OSError):
                    audio.terminate()
