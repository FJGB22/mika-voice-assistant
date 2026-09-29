import numpy as np

from mika.stt import to_whisper_input


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
