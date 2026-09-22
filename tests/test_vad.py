import numpy as np
import pytest

from mika.vad import SILENCE_DB, frame_db


def test_silence_returns_silence_db():
    frame = np.zeros((320, 1), dtype=np.int16)
    assert frame_db(frame) == SILENCE_DB


def test_full_scale_is_about_zero_db():
    frame = np.full((320, 1), 32767, dtype=np.int16)
    assert frame_db(frame) == pytest.approx(0, abs=0.01)


def test_half_scale_is_minus_six_db():
    frame = np.full((320, 1), 16384, dtype=np.int16)
    assert frame_db(frame) == pytest.approx(-6.02, abs=0.01)
