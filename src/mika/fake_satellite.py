"""Stands in for the ESP32: same protocol, audio from the laptop mic or a WAV file.

Run the server first, then:
    python -m mika.fake_satellite                 # talk into the laptop mic
    python -m mika.fake_satellite --wav temp.wav  # replay a recording instead
Add a server URL to reach another machine, e.g. ws://192.168.1.20:8765.
"""

import argparse
import json
import time
import wave

import sounddevice as sd
from websockets.exceptions import ConnectionClosed
from websockets.sync.client import connect

from mika import protocol
from mika.audio import CHANNELS
from mika.vad import FRAME_MS, FRAME_SAMPLES, SAMPLE_RATE

DEFAULT_URL = "ws://localhost:8765"

# These errors leave the running question alive (see docs/protocol.md, "Turn over?").
NOT_TURN_ENDING = (protocol.BUSY, protocol.BAD_MESSAGE)


def receive(websocket, timeout=None):
    return json.loads(websocket.recv(timeout=timeout))


def is_turn_ending_error(message):
    return message["type"] == protocol.ERROR and message["code"] not in NOT_TURN_ENDING


# Frame sources are generators: each yields one 640-byte frame per step, so
# stream_until_stop does not care whether the audio comes from a mic or a file.
def mic_frames():
    with sd.InputStream(
        samplerate=SAMPLE_RATE, channels=CHANNELS, dtype="int16", blocksize=FRAME_SAMPLES
    ) as stream:
        while True:
            frame, overflowed = stream.read(FRAME_SAMPLES)
            if overflowed:
                print("Warning: audio input overflowed")
            yield frame.tobytes()


def check_wav(path):
    # Checked before connecting, so a wrong file fails at once instead of mid-question.
    with wave.open(path, "rb") as wav:
        found = (wav.getframerate(), wav.getnchannels(), wav.getsampwidth())
    expected = (SAMPLE_RATE, CHANNELS, protocol.SAMPLE_BYTES)
    if found != expected:
        raise ValueError(f"{path}: need {expected} (rate, channels, bytes), got {found}")


def wav_frames(path):
    with wave.open(path, "rb") as wav:
        while True:
            data = wav.readframes(FRAME_SAMPLES)
            if len(data) < protocol.FRAME_BYTES:
                break  # a last partial frame is under 20 ms: not worth padding
            # Real-time pace, like the ESP32: the whole file at once would not test that.
            time.sleep(FRAME_MS / 1000)
            yield data

    # The file may end mid-word; without trailing silence the server would wait
    # forever for the 500 ms of quiet that marks the end of speech.
    silence = bytes(protocol.FRAME_BYTES)
    while True:
        time.sleep(FRAME_MS / 1000)
        yield silence


def stream_until_stop(websocket, frames):
    try:
        for frame in frames:
            websocket.send(frame)
            try:
                # timeout=0: only look, never wait, or streaming would stall.
                message = receive(websocket, timeout=0)
            except TimeoutError:
                continue
            print(f"<- {message}")
            if message["type"] == protocol.STOP or is_turn_ending_error(message):
                return message
    finally:
        # Closes the mic (or file) now, not whenever Python gets around to it.
        frames.close()


def wait_for_answer(websocket):
    while True:
        message = receive(websocket)
        print(f"<- {message}")
        if message["type"] == protocol.REPLY or is_turn_ending_error(message):
            return


def main():
    parser = argparse.ArgumentParser(description="Fake satellite for testing the Mika server.")
    parser.add_argument("url", nargs="?", default=DEFAULT_URL, help=f"default: {DEFAULT_URL}")
    parser.add_argument("--wav", help="16 kHz mono 16-bit WAV to send instead of the mic")
    args = parser.parse_args()

    if args.wav:
        try:
            check_wav(args.wav)
        except (OSError, wave.Error, ValueError) as err:
            print(f"Error: {err}")
            return

    try:
        with connect(args.url, compression=None) as websocket:
            print(f"Connected to {args.url}")
            # Caught inside the with block, so Ctrl-C closes the connection normally
            # (code 1000) instead of as an error (1011) that the server logs as a crash.
            try:
                while True:
                    prompt = "send the WAV" if args.wav else "talk"
                    input(f"Press Enter to {prompt} (Ctrl-C to quit) ")
                    websocket.send(protocol.encode_message({"type": protocol.START}))

                    frames = wav_frames(args.wav) if args.wav else mic_frames()
                    ended = stream_until_stop(websocket, frames)
                    if ended.get("reason") == protocol.SPEECH_END:
                        wait_for_answer(websocket)
            except KeyboardInterrupt:
                print("\nBye.")
    except KeyboardInterrupt:
        print("\nBye.")  # Ctrl-C while still connecting
    except ConnectionClosed:
        # Parent of ConnectionClosedOK and ConnectionClosedError: one except for both.
        print("Server closed the connection.")
    except OSError as err:
        print(f"Could not connect to {args.url}: {err}. Is the server running?")


if __name__ == "__main__":
    main()
