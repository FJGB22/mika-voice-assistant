"""Wire format between the server and a satellite (the ESP32). See docs/protocol.md."""

import json

import numpy as np

from mika.vad import FRAME_SAMPLES

SAMPLE_BYTES = 2  # int16
FRAME_BYTES = FRAME_SAMPLES * SAMPLE_BYTES  # 640 bytes per 20 ms frame

# Client -> server
START = "start"

# Server -> client
LISTENING = "listening"
STOP = "stop"
TRANSCRIPT = "transcript"
REPLY = "reply"
ERROR = "error"

# Reasons carried by STOP
SPEECH_END = "speech_end"
NO_SPEECH = "no_speech"

# Codes carried by ERROR
BUSY = "busy"
BAD_FRAME = "bad_frame"
BAD_MESSAGE = "bad_message"
EMPTY_TRANSCRIPT = "empty_transcript"
SERVER_ERROR = "server_error"


def decode_frame(data):
    # numpy would reject a wrong length too, but only by accident of reshape and
    # frombuffer; this keeps the ValueError contract ours and the message readable.
    if len(data) != FRAME_BYTES:
        raise ValueError(f"expected a {FRAME_BYTES}-byte frame, got {len(data)} bytes")
    # Explicit byte order: the ESP32 sends little-endian, whatever this machine uses.
    samples = np.frombuffer(data, dtype="<i2")
    # Same (samples, 1) shape as the laptop mic, so vad.py and stt.py stay unchanged.
    return samples.reshape((FRAME_SAMPLES, 1))


def parse_message(text):
    # JSONDecodeError is already a ValueError; converting it here keeps all three
    # rejections in one visible place for the reader.
    try:
        message = json.loads(text)
    except json.JSONDecodeError as err:
        raise ValueError("not valid JSON") from err
    if not isinstance(message, dict):
        raise ValueError("message is not a JSON object")
    if not isinstance(message.get("type"), str):
        raise ValueError('message has no string "type" field')
    return message["type"]


def encode_message(message):
    return json.dumps(message)
