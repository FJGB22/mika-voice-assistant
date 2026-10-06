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
