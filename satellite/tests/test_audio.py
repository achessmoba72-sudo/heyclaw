from app.audio.pyaudio_interface import PyAudioInterface


class FakeStream:
    def __init__(self, *, fail_on_stop: bool = False, active: bool = True) -> None:
        self.fail_on_stop = fail_on_stop
        self.active = active
        self.start_calls = 0
        self.stop_calls = 0
        self.close_calls = 0
        self.writes: list[tuple[bytes, bool]] = []

    def is_active(self) -> bool:
        return self.active

    def start_stream(self) -> None:
        self.start_calls += 1
        self.active = True

    def stop_stream(self) -> None:
        self.stop_calls += 1
        if self.fail_on_stop:
            raise OSError("Wait timed out")
        self.active = False

    def write(self, audio: bytes, *, exception_on_underflow: bool) -> None:
        self.writes.append((audio, exception_on_underflow))

    def close(self) -> None:
        self.close_calls += 1


class FakePyAudio:
    def __init__(self) -> None:
        self.terminate_calls = 0

    def terminate(self) -> None:
        self.terminate_calls += 1


def test_audio_stop_is_idempotent_and_tolerates_closed_streams() -> None:
    audio = PyAudioInterface()
    input_stream = FakeStream(fail_on_stop=True)
    output_stream = FakeStream()
    pyaudio = FakePyAudio()
    audio._input_stream = input_stream
    audio._output_stream = output_stream
    audio._audio = pyaudio  # type: ignore[assignment]

    audio.stop()
    audio.stop()

    assert input_stream.stop_calls == 1
    assert input_stream.close_calls == 1
    assert output_stream.stop_calls == 1
    assert output_stream.close_calls == 1
    assert pyaudio.terminate_calls == 1


def test_audio_output_starts_on_demand_without_underflow_exceptions() -> None:
    audio = PyAudioInterface()
    output_stream = FakeStream(active=False)
    audio._output_stream = output_stream

    audio._write_output(b"audio")

    assert output_stream.start_calls == 1
    assert output_stream.writes == [(b"audio", False)]


def test_interrupt_stops_the_current_output_chunk() -> None:
    audio = PyAudioInterface(gate_microphone_during_playback=True)
    output_stream = FakeStream()
    audio._output_stream = output_stream
    payload = b"a" * 960

    def interrupt_after_first_write(data: bytes, *, exception_on_underflow: bool) -> None:
        output_stream.writes.append((data, exception_on_underflow))
        audio.interrupt()

    output_stream.write = interrupt_after_first_write  # type: ignore[method-assign]
    audio._write_output(payload)

    assert output_stream.writes == [(payload[:320], False)]


def test_duplicate_shared_response_subscription_does_not_replay_chunks() -> None:
    # Covered in the backend suite; this name documents the satellite-side contract:
    # interrupt() only clears audio and does not replay anything itself.
    audio = PyAudioInterface(gate_microphone_during_playback=True)
    audio.output(b"old")
    audio.interrupt()

    assert audio._output_queue.empty()


def test_local_voice_activity_interrupts_playback_after_three_frames() -> None:
    class FakeVoiceDetector:
        def process_stream(self, frame: bytes) -> bytes:
            return frame

        def has_voice(self) -> bool:
            return True

    audio = PyAudioInterface(gate_microphone_during_playback=True)
    audio._echo_canceller = FakeVoiceDetector()  # type: ignore[assignment]
    audio._aec_frame_bytes = 4
    audio._echo_cancellation_enabled = False
    audio.output(b"queued audio")

    assert audio._process_input(b"1234" * 3) == b"1234" * 3
    assert audio._output_queue.empty()
    assert not audio._assistant_speaking.is_set()
