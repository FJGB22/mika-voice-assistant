import numpy as np
import pytest

from mika.vad import (
    CONTINUE,
    MAX_WAIT_FRAMES,
    NO_SPEECH,
    SILENCE_DB,
    SPEAKING,
    START_FRAMES,
    WAITING,
    SpeechDetector,
    frame_db,
)

LOUD = np.full((320, 1), 3000, dtype=np.int16)  # about -21 dB, like normal speech
QUIET = np.zeros((320, 1), dtype=np.int16)


def test_silence_returns_silence_db():
    frame = np.zeros((320, 1), dtype=np.int16)
    assert frame_db(frame) == SILENCE_DB


def test_full_scale_is_about_zero_db():
    frame = np.full((320, 1), 32767, dtype=np.int16)
    assert frame_db(frame) == pytest.approx(0, abs=0.01)


def test_half_scale_is_minus_six_db():
    frame = np.full((320, 1), 16384, dtype=np.int16)
    assert frame_db(frame) == pytest.approx(-6.02, abs=0.01)


def feed(detector, frame, count):
    results = []
    for _ in range(count):
        results.append(detector.process(frame))
    return results


def test_enough_loud_frames_starts_speaking():
    detector = SpeechDetector()
    results = feed(detector, LOUD, START_FRAMES)

    assert detector.state == SPEAKING
    assert len(detector.frames) == START_FRAMES
    assert results[-1] == CONTINUE


def test_single_knock_is_ignored():
    detector = SpeechDetector()
    results = feed(detector, LOUD, 1)
    results += feed(detector, QUIET, 1)

    assert detector.state == WAITING
    assert len(detector.frames) == 0
    assert results[-1] == CONTINUE


def test_silence_times_out():
    detector = SpeechDetector()
    results = feed(detector, QUIET, MAX_WAIT_FRAMES)

    assert detector.state == WAITING
    assert len(detector.frames) == 0
    assert results[-1] == NO_SPEECH
    assert results[-2] == CONTINUE


def test_speech_near_timeout_is_not_cut():
    detector = SpeechDetector()
    results = feed(detector, QUIET, MAX_WAIT_FRAMES - 1)
    results += feed(detector, LOUD, START_FRAMES)

    assert detector.state == SPEAKING
    assert len(detector.frames) == START_FRAMES
    assert results[-1] == CONTINUE
