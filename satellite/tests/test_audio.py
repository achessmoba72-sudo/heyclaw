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
