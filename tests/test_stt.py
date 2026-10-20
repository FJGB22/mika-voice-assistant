from types import SimpleNamespace

import numpy as np

from mika.stt import WHISPER_HOTWORDS, to_whisper_input, transcribe


def test_whisper_input_is_flat_float32():
    audio = np.zeros((320, 1), dtype=np.int16)
    result = to_whisper_input(audio)

    assert result.shape == (320,)
    assert result.dtype == np.float32


def test_whisper_input_min_sample_is_minus_one():
    audio = np.full((320, 1), -32768, dtype=np.int16)
    result = to_whisper_input(audio)

    assert result.min() == -1.0


def test_whisper_input_stays_within_range():
    audio = np.array([[-32768], [32767]], dtype=np.int16)
    result = to_whisper_input(audio)

    assert result.min() >= -1.0
    assert result.max() <= 1.0


class FakeWhisper:
    """Same shape as WhisperModel.transcribe: segments with .text, and an info value."""

    def __init__(self, texts):
        self.texts = texts
        self.kwargs = None

    def transcribe(self, audio, **kwargs):
        self.kwargs = kwargs
        return (SimpleNamespace(text=text) for text in self.texts), None


def test_transcribe_joins_the_segments():
    whisper = FakeWhisper([" Hello,", " Mika."])

    assert transcribe(whisper, np.zeros((320, 1), dtype=np.int16)) == "Hello, Mika."


def test_whisper_is_told_to_expect_mikas_name():
    whisper = FakeWhisper([" Hello, Mika."])

    transcribe(whisper, np.zeros((320, 1), dtype=np.int16))

    # Without the hint, base.en heard "Mita" every time, and Gemini spent its reply
    # correcting the user instead of answering.
    assert "Mika" in whisper.kwargs["hotwords"]
    assert whisper.kwargs["hotwords"] == WHISPER_HOTWORDS
