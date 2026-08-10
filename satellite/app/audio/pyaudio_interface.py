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
from heyclaw_shared.performance import measure_performance
from loguru import logger

_NATIVE_AUDIO_STDERR_LOCK = threading.Lock()
_BARGE_IN_VOICE_FRAMES = 3
_LOCAL_INTERRUPT_GUARD_SECONDS = 3.0


def close_stream(stream: Any) -> None:
    """Stop and close a PyAudio stream, tolerating an already closed device."""
    if stream is None:
        return
    with suppress(OSError):
        if stream.is_active():
            stream.stop_stream()
    with suppress(OSError):
        stream.close()


@contextmanager
def suppress_native_audio_probe_noise() -> Iterator[None]:
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
        enable_local_barge_in = not gate_microphone_during_playback
        self._echo_canceller = (
            AudioProcessor(
                enable_aec=enable_echo_cancellation,
                enable_ns=False,
                enable_agc=False,
                enable_vad=enable_local_barge_in,
            )
            if enable_echo_cancellation or enable_local_barge_in
            else None
        )
        self._echo_cancellation_enabled = enable_echo_cancellation
        self._local_vad_enabled = bool(
            enable_local_barge_in
            and self._echo_canceller is not None
            and self._echo_canceller.vad_enabled()
        )
        if enable_local_barge_in and not self._local_vad_enabled:
            logger.info(
                "Local VAD is unavailable; falling back to ElevenLabs interruption detection"
            )
        self._aec_lock = threading.Lock()
        self._aec_input_buffer = bytearray()
        self._aec_output_buffer = bytearray()
        self._aec_frame_bytes = 0
        if self._echo_canceller is not None:
            self._echo_canceller.set_stream_format(self.sample_rate, 1)
            if enable_echo_cancellation:
                self._echo_canceller.set_reverse_stream_format(self.sample_rate, 1)
                self._echo_canceller.set_stream_delay(50)
            if enable_local_barge_in:
                self._echo_canceller.set_vad_aggressiveness(2)
            self._aec_frame_bytes = self._echo_canceller.get_frame_size() * 2
        self._input_callback: Callable[[bytes], None] | None = None
        self._output_queue: queue.Queue[tuple[int, bytes]] = queue.Queue()
        self._should_stop = threading.Event()
        self._stop_lock = threading.Lock()
        self._stopped = False
        self._output_thread: threading.Thread | None = None
        self._assistant_speaking = threading.Event()
        self._consecutive_voice_frames = 0
        self._playback_lock = threading.Lock()
        self._playback_generation = 0
        self._drop_output_until = 0.0
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
            self._consecutive_voice_frames = 0
            with suppress_native_audio_probe_noise():
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
            close_stream(self._input_stream)
            self._input_stream = None
            close_stream(self._output_stream)
            self._output_stream = None
            if self._audio is not None:
                with suppress(OSError):
                    self._audio.terminate()
                self._audio = None
        finally:
            self._stop_lock.release()

    def output(self, audio: bytes) -> None:
        with self._playback_lock:
            if monotonic() < self._drop_output_until:
                return
            self._assistant_speaking.set()
            self._output_queue.put((self._playback_generation, audio))

    def interrupt(self) -> None:
        """Handle the authoritative interruption event received from ElevenLabs."""
        self._interrupt_playback(drop_until=0.0)

    def _interrupt_playback(self, *, drop_until: float) -> None:
        with self._playback_lock:
            self._playback_generation += 1
            self._drop_output_until = drop_until
        try:
            while True:
                self._output_queue.get_nowait()
        except queue.Empty:
            with self._aec_lock:
                self._aec_output_buffer.clear()
            self._assistant_speaking.clear()
            self._consecutive_voice_frames = 0
            self._microphone_gate_until = monotonic() + self._echo_guard_seconds

    def _play_output(self) -> None:
        while not self._should_stop.is_set():
            try:
                generation, audio = self._output_queue.get(timeout=0.1)
            except queue.Empty:
                self._pause_output_stream()
                continue
            if self._output_stream is not None:
                self._write_output(audio, generation=generation)
            if self._output_queue.empty():
                self._assistant_speaking.clear()
                if self._gate_microphone_during_playback:
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
        barge_in_detected = False
        with self._aec_lock:
            self._aec_input_buffer.extend(audio)
            processed = bytearray()
            while len(self._aec_input_buffer) >= self._aec_frame_bytes:
                frame = bytes(self._aec_input_buffer[: self._aec_frame_bytes])
                del self._aec_input_buffer[: self._aec_frame_bytes]
                processed_frame = self._echo_canceller.process_stream(frame)
                if self._echo_cancellation_enabled:
                    processed.extend(processed_frame)
                if self._local_vad_enabled and self._assistant_speaking.is_set():
                    if self._echo_canceller.has_voice():
                        self._consecutive_voice_frames += 1
                    else:
                        self._consecutive_voice_frames = 0
                    if self._consecutive_voice_frames >= _BARGE_IN_VOICE_FRAMES:
                        barge_in_detected = True
                else:
                    self._consecutive_voice_frames = 0
        if barge_in_detected:
            logger.info("Local barge-in detected; stopping playback")
            self._interrupt_playback(
                drop_until=monotonic() + _LOCAL_INTERRUPT_GUARD_SECONDS
            )
        return bytes(processed) if self._echo_cancellation_enabled else audio

    def _write_output(self, audio: bytes, *, generation: int | None = None) -> None:
        if self._output_stream is None:
            return
        if generation is None:
            with self._playback_lock:
                generation = self._playback_generation
        if not self._output_stream.is_active():
            self._output_stream.start_stream()
        if not self._echo_cancellation_enabled:
            frame_bytes = self.sample_rate // 100 * 2
            for offset in range(0, len(audio), frame_bytes):
                if self._playback_was_interrupted(generation):
                    return
                self._output_stream.write(
                    audio[offset : offset + frame_bytes],
                    exception_on_underflow=False,
                )
            return
        with self._aec_lock:
            self._aec_output_buffer.extend(audio)
        while True:
            with self._aec_lock:
                if len(self._aec_output_buffer) < self._aec_frame_bytes:
                    return
                frame = bytes(self._aec_output_buffer[: self._aec_frame_bytes])
                del self._aec_output_buffer[: self._aec_frame_bytes]
                if self._echo_cancellation_enabled:
                    self._echo_canceller.process_reverse_stream(frame)
            if self._playback_was_interrupted(generation):
                return
            self._output_stream.write(frame, exception_on_underflow=False)

    def _playback_was_interrupted(self, generation: int) -> bool:
        with self._playback_lock:
            return generation != self._playback_generation

    def _pause_output_stream(self) -> None:
        if self._output_stream is None:
            return
        with suppress(OSError):
            if self._output_stream.is_active():
                self._output_stream.stop_stream()


def audio_devices() -> list[dict[str, Any]]:
    with suppress_native_audio_probe_noise():
        audio = pyaudio.PyAudio()
    try:
        try:
            default_input_index = int(audio.get_default_input_device_info()["index"])
        except OSError:
            default_input_index = None
        try:
            default_output_index = int(audio.get_default_output_device_info()["index"])
        except OSError:
            default_output_index = None
        devices = []
        for index in range(audio.get_device_count()):
            info = audio.get_device_info_by_index(index)
            host_api = audio.get_host_api_info_by_index(int(info["hostApi"]))
            devices.append(
                {
                    "index": index,
                    "name": info.get("name", "unknown"),
                    "inputs": int(info.get("maxInputChannels", 0)),
                    "outputs": int(info.get("maxOutputChannels", 0)),
                    "sample_rate": int(info.get("defaultSampleRate", 0)),
                    "host_api": host_api.get("name", "unknown"),
                    "default_input": index == default_input_index,
                    "default_output": index == default_output_index,
                }
            )
        return devices
    finally:
        audio.terminate()
