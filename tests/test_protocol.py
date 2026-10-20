import json

import numpy as np
import pytest

from mika import protocol
from mika.vad import FRAME_SAMPLES


def test_frame_bytes_is_640():
    # The ESP32 firmware hard-codes this number, so changing it breaks the contract.
    assert protocol.FRAME_BYTES == 640
    assert FRAME_SAMPLES * protocol.SAMPLE_BYTES == protocol.FRAME_BYTES


def test_decode_frame_shape_and_type():
    data = np.zeros(FRAME_SAMPLES, dtype="<i2").tobytes()

    frame = protocol.decode_frame(data)

    assert frame.shape == (FRAME_SAMPLES, 1)
    assert frame.dtype == np.int16


def test_decode_frame_reads_little_endian():
    samples = np.zeros(FRAME_SAMPLES, dtype="<i2")
    samples[0] = 1
    samples[1] = -2
    samples[-1] = 32767
    data = samples.tobytes()

    assert data[:2] == b"\x01\x00"  # sanity: low byte first

    frame = protocol.decode_frame(data)

    assert frame[0, 0] == 1
    assert frame[1, 0] == -2
    assert frame[-1, 0] == 32767


@pytest.mark.parametrize("size", [0, 639, 641, 1280])
def test_decode_frame_rejects_wrong_length(size):
    with pytest.raises(ValueError):
        protocol.decode_frame(bytes(size))


def test_parse_message_returns_type():
    assert protocol.parse_message('{"type": "start"}') == "start"


def test_parse_message_ignores_extra_fields():
    assert protocol.parse_message('{"type": "start", "device": "esp32-1"}') == "start"


@pytest.mark.parametrize(
    "text",
    [
        "not json",
        "",
        "[]",
        '"start"',
        "5",
        "{}",
        '{"kind": "start"}',
        '{"type": 5}',
    ],
)
def test_parse_message_rejects_bad_input(text):
    with pytest.raises(ValueError):
        protocol.parse_message(text)


def test_encode_message_is_json_text():
    message = {"type": protocol.STOP, "reason": protocol.SPEECH_END}

    text = protocol.encode_message(message)

    assert isinstance(text, str)
    assert json.loads(text) == message


# --- split_frames: audio to the satellite, the opposite of decode_frame ---


def test_split_frames_gives_640_byte_frames():
    audio = np.zeros((3 * FRAME_SAMPLES, 1), dtype=np.int16)

    frames = protocol.split_frames(audio)

    assert len(frames) == 3
    assert all(len(frame) == protocol.FRAME_BYTES for frame in frames)


def test_split_frames_pads_the_last_frame_with_silence():
    audio = np.full((FRAME_SAMPLES + 5, 1), 7, dtype=np.int16)

    frames = protocol.split_frames(audio)

    # Two frames, not one and a short one: the firmware only accepts full frames.
    assert len(frames) == 2
    last = protocol.decode_frame(frames[1])[:, 0]
    assert last[:5].tolist() == [7] * 5
    assert not last[5:].any()


def test_split_frames_round_trips_through_decode_frame():
    audio = np.arange(-1000, 1000, dtype=np.int16).reshape(-1, 1)[: 3 * FRAME_SAMPLES]

    frames = protocol.split_frames(audio)

    decoded = np.concatenate([protocol.decode_frame(frame) for frame in frames])
    assert np.array_equal(decoded, audio)


def test_split_frames_writes_little_endian():
    audio = np.zeros((FRAME_SAMPLES, 1), dtype=np.int16)
    audio[0, 0] = 1
    audio[1, 0] = -2

    [frame] = protocol.split_frames(audio)

    # Explicit byte order, as in decode_frame: the ESP32 reads little-endian.
    assert frame[:4] == b"\x01\x00\xfe\xff"


def test_split_frames_of_nothing_is_no_frames():
    assert protocol.split_frames(np.zeros((0, 1), dtype=np.int16)) == []


def test_split_frames_accepts_a_flat_array_too():
    flat = np.ones(FRAME_SAMPLES, dtype=np.int16)

    frames = protocol.split_frames(flat)

    # len first: two functions that both return None would also be "equal".
    assert len(frames) == 1
    assert frames == protocol.split_frames(flat.reshape(-1, 1))
