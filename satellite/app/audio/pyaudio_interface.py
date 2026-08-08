import os
import queue
import sys
import threading
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager, suppress
from time import monotonic
from typing import Any

import pyaudio
from aec_audio_processing import AudioProcessor

from app.performance import measure_performance

_NATIVE_AUDIO_STDERR_LOCK = threading.Lock()


@contextmanager
def _suppress_native_audio_probe_noise() -> Iterator[None]:
    """Hide Linux audio-backend probe noise while preserving Python exceptions."""
    if not sys.platform.startswith("linux"):
        yield
        return

    with _NATIVE_AUDIO_STDERR_LOCK:
        with suppress(OSError):
            sys.stderr.flush()
        saved_stderr = os.dup(2)
        null_stderr = os.open(os.devnull, os.O_WRONLY)
        try:
            os.dup2(null_stderr, 2)
            yield
        finally:
            os.dup2(saved_stderr, 2)
            os.close(null_stderr)
            os.close(saved_stderr)


class PyAudioInterface:
    """Low-latency mono PCM audio compatible with ElevenLabs Conversation."""

    sample_rate = 16_000
    input_frames_per_buffer = 640
    output_frames_per_buffer = 640

    def __init__(
        self,
        *,
        input_device_index: int | None = None,
        output_device_index: int | None = None,
        gate_microphone_during_playback: bool = False,
        enable_echo_cancellation: bool = False,
        echo_guard_ms: int = 350,
    ) -> None:
        self._input_device_index = input_device_index
        self._output_device_index = output_device_index
        self._gate_microphone_during_playback = gate_microphone_during_playback
        self._echo_guard_seconds = echo_guard_ms / 1000
        self._echo_canceller = (
            AudioProcessor(
                enable_aec=True,
                enable_ns=False,
                enable_agc=False,
                enable_vad=False,
            )
            if enable_echo_cancellation
            else None
        )
        self._aec_lock = threading.Lock()
        self._aec_input_buffer = bytearray()
        self._aec_output_buffer = bytearray()
        self._aec_frame_bytes = 0
        if self._echo_canceller is not None:
            self._echo_canceller.set_stream_format(self.sample_rate, 1)
            self._echo_canceller.set_reverse_stream_format(self.sample_rate, 1)
            self._echo_canceller.set_stream_delay(50)
            self._aec_frame_bytes = self._echo_canceller.get_frame_size() * 2
        self._input_callback: Callable[[bytes], None] | None = None
        self._output_queue: queue.Queue[bytes] = queue.Queue()
        self._should_stop = threading.Event()
        self._stop_lock = threading.Lock()
        self._stopped = False
        self._output_thread: threading.Thread | None = None
        self._assistant_speaking = threading.Event()
        self._microphone_gate_until = 0.0
        self._audio: pyaudio.PyAudio | None = None
        self._input_stream: Any = None
        self._output_stream: Any = None

    def start(self, input_callback: Callable[[bytes], None]) -> None:
        with measure_performance("audio.devices.open"):
            self._input_callback = input_callback
            self._should_stop.clear()
            self._stopped = False
            self._aec_input_buffer.clear()
            self._aec_output_buffer.clear()
            with _suppress_native_audio_probe_noise():
                self._audio = pyaudio.PyAudio()
                self._input_stream = self._audio.open(
                    format=pyaudio.paInt16,
                    channels=1,
                    rate=self.sample_rate,
                    input=True,
                    input_device_index=self._input_device_index,
                    stream_callback=self._on_input,
                    frames_per_buffer=self.input_frames_per_buffer,
                    start=True,
                )
                self._output_stream = self._audio.open(
                    format=pyaudio.paInt16,
                    channels=1,
                    rate=self.sample_rate,
                    output=True,
                    output_device_index=self._output_device_index,
                    frames_per_buffer=self.output_frames_per_buffer,
                    start=False,
                )
            self._output_thread = threading.Thread(
                target=self._play_output,
                name="heyclaw-audio-output",
                daemon=True,
            )
            self._output_thread.start()

    def stop(self) -> None:
        if not self._stop_lock.acquire(blocking=False):
            return
        try:
            if self._stopped:
                return
            self._stopped = True
            self._should_stop.set()

            if self._output_thread is not None:
                self._output_thread.join(timeout=2)
                self._output_thread = None
            self._close_stream(self._input_stream)
            self._input_stream = None
            self._close_stream(self._output_stream)
            self._output_stream = None
            if self._audio is not None:
                with suppress(OSError):
                    self._audio.terminate()
                self._audio = None
        finally:
            self._stop_lock.release()

    @staticmethod
    def _close_stream(stream: Any) -> None:
        if stream is None:
            return
        with suppress(OSError):
            if stream.is_active():
                stream.stop_stream()
        with suppress(OSError):
            stream.close()

    def output(self, audio: bytes) -> None:
        if self._gate_microphone_during_playback:
            self._assistant_speaking.set()
        self._output_queue.put(audio)

    def interrupt(self) -> None:
        try:
            while True:
                self._output_queue.get_nowait()
        except queue.Empty:
            with self._aec_lock:
                self._aec_output_buffer.clear()
            self._assistant_speaking.clear()
            self._microphone_gate_until = monotonic() + self._echo_guard_seconds

    def _play_output(self) -> None:
        while not self._should_stop.is_set():
            try:
                audio = self._output_queue.get(timeout=0.1)
            except queue.Empty:
                self._pause_output_stream()
                continue
            if self._output_stream is not None:
                self._write_output(audio)
            if self._gate_microphone_during_playback and self._output_queue.empty():
                self._assistant_speaking.clear()
                self._microphone_gate_until = monotonic() + self._echo_guard_seconds

    def _on_input(
        self,
        input_data: bytes | None,
        _frame_count: int,
        _time_info: Mapping[str, float],
        _status: int,
    ) -> tuple[bytes | None, int]:
        microphone_is_gated = self._gate_microphone_during_playback and (
            self._assistant_speaking.is_set()
            or monotonic() < self._microphone_gate_until
        )
        if input_data is not None and not microphone_is_gated:
            input_audio = self._process_input(input_data)
            if input_audio and self._input_callback is not None:
                self._input_callback(input_audio)
        return (None, pyaudio.paContinue)

    def _process_input(self, audio: bytes) -> bytes:
        if self._echo_canceller is None:
            return audio
        with self._aec_lock:
            self._aec_input_buffer.extend(audio)
            processed = bytearray()
            while len(self._aec_input_buffer) >= self._aec_frame_bytes:
                frame = bytes(self._aec_input_buffer[: self._aec_frame_bytes])
                del self._aec_input_buffer[: self._aec_frame_bytes]
                processed.extend(self._echo_canceller.process_stream(frame))
            return bytes(processed)

    def _write_output(self, audio: bytes) -> None:
        if self._output_stream is None:
            return
        if not self._output_stream.is_active():
            self._output_stream.start_stream()
        if self._echo_canceller is None:
            self._output_stream.write(audio, exception_on_underflow=False)
            return
        with self._aec_lock:
            self._aec_output_buffer.extend(audio)
        while True:
            with self._aec_lock:
                if len(self._aec_output_buffer) < self._aec_frame_bytes:
                    return
                frame = bytes(self._aec_output_buffer[: self._aec_frame_bytes])
                del self._aec_output_buffer[: self._aec_frame_bytes]
                self._echo_canceller.process_reverse_stream(frame)
            self._output_stream.write(frame, exception_on_underflow=False)

    def _pause_output_stream(self) -> None:
        if self._output_stream is None:
            return
        with suppress(OSError):
            if self._output_stream.is_active():
                self._output_stream.stop_stream()


def audio_devices() -> list[dict[str, Any]]:
    with _suppress_native_audio_probe_noise():
        audio = pyaudio.PyAudio()
    try:
        devices = []
        for index in range(audio.get_device_count()):
            info = audio.get_device_info_by_index(index)
            devices.append(
                {
                    "index": index,
                    "name": info.get("name", "unknown"),
                    "inputs": int(info.get("maxInputChannels", 0)),
                    "outputs": int(info.get("maxOutputChannels", 0)),
                    "sample_rate": int(info.get("defaultSampleRate", 0)),
                }
            )
        return devices
    finally:
        audio.terminate()
