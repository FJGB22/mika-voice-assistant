import numpy as np
import pytest

from mika.vad import (
    CONTINUE,
    DONE,
    END_FRAMES,
    MAX_SPEECH_FRAMES,
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


def test_waiting_enough_loud_frames_starts_speaking():
    detector = SpeechDetector()
    results = feed(detector, LOUD, START_FRAMES)

    assert detector.state == SPEAKING
    assert len(detector.frames) == START_FRAMES
    assert results[-1] == CONTINUE


def test_waiting_single_knock_is_ignored():
    detector = SpeechDetector()
    results = feed(detector, LOUD, 1)
    results += feed(detector, QUIET, 1)

    assert detector.state == WAITING
    assert len(detector.frames) == 0
    assert results[-1] == CONTINUE


def test_waiting_silence_times_out():
    detector = SpeechDetector()
    results = feed(detector, QUIET, MAX_WAIT_FRAMES)

    assert detector.state == WAITING
    assert len(detector.frames) == 0
    assert results[-1] == NO_SPEECH
    assert results[-2] == CONTINUE


def test_waiting_speech_near_timeout_is_not_cut():
    # Regression: speech starting exactly at the 3 s deadline used to get NO_SPEECH.
    detector = SpeechDetector()
    results = feed(detector, QUIET, MAX_WAIT_FRAMES - 1)
    results += feed(detector, LOUD, START_FRAMES)

    assert detector.state == SPEAKING
    assert len(detector.frames) == START_FRAMES
    assert results[-1] == CONTINUE


def start_speaking():
    # Through the real WAITING path, so these tests also cover the switch and pre-roll.
    detector = SpeechDetector()
    feed(detector, LOUD, START_FRAMES)
    return detector


def test_speaking_silence_ends_speech():
    detector = start_speaking()
    results = feed(detector, QUIET, END_FRAMES)

    assert results[-1] == DONE
    assert results[-2] == CONTINUE
    assert len(detector.frames) == START_FRAMES + END_FRAMES


def test_speaking_short_pause_does_not_end_speech():
    detector = start_speaking()
    results = feed(detector, QUIET, END_FRAMES - 1)
    results += feed(detector, LOUD, 1)
    results += feed(detector, QUIET, END_FRAMES - 1)

    assert set(results) == {CONTINUE}
    assert len(detector.frames) == START_FRAMES + 2 * (END_FRAMES - 1) + 1


def test_speaking_speech_is_capped_at_max_length():
    detector = start_speaking()
    # start_speaking() already stored START_FRAMES of pre-roll, which counts.
    results = feed(detector, LOUD, MAX_SPEECH_FRAMES - START_FRAMES)

    assert results[-1] == DONE
    assert results[-2] == CONTINUE
    assert len(detector.frames) == MAX_SPEECH_FRAMES
