"""Text to speech: turns Mika's reply into 16 kHz mono int16 audio, the same format
the satellite's microphone sends. Only this module knows which TTS engines are used:
Edge first, and Piper on the laptop when Edge is too slow or fails.

Try it on the laptop:
    python -m mika.tts "Hello, I am Mika."
    python -m mika.tts "Hello, I am Mika." --piper    # hear the fallback voice
"""

import argparse
import asyncio
import os
import shutil
import subprocess
import time
import wave
from dataclasses import dataclass

import numpy as np

from mika.vad import SAMPLE_RATE

# The same voice as Mika 1.0 (the VTuber), so both are recognisably the same character.
VOICE = "en-US-AnaNeural"
RATE = "+18%"  # a bit fast, like a streamer
PITCH = "+8Hz"

# Edge normally answers in 1-3 s but has been seen to take 15-36 s. Past this, Piper
# speaks instead: a different voice, but well under a second and no network.
EDGE_DEADLINE = 4.0

# The fallback voice. A "low" Piper voice is already 16 kHz, so its audio needs no
# resampling. Not a secret, but machine-specific, so it can be set like one.
PIPER_VOICE_NAME = "TTS_VOICE"
DEFAULT_PIPER_VOICE = os.path.normpath(os.path.expanduser("~/.mika/voices/en_US-lessac-low.onnx"))

SAMPLE_WIDTH = 2  # bytes: int16


@dataclass
class Voices:
    """Both engines, loaded once at startup and passed around as one value."""

    edge: object  # the edge_tts.Communicate class: one instance per reply
    piper: object  # a loaded piper.PiperVoice


def piper_voice_path():
    return os.environ.get(PIPER_VOICE_NAME, DEFAULT_PIPER_VOICE)


def load_voice():
    # Both checked once at startup, so a missing piece is reported before the first
    # question instead of in the middle of an answer.
    if shutil.which("ffmpeg") is None:
        raise FileNotFoundError(
            "ffmpeg not found on PATH. It decodes Edge's MP3 into raw audio.\n"
            "Install it (e.g. winget install Gyan.FFmpeg), then open a new terminal."
        )
    path = piper_voice_path()
    if not os.path.exists(path):
        raise FileNotFoundError(
            f"Piper voice not found: {path}\n"
            "Download it with:\n"
            "    python -m piper.download_voices en_US-lessac-low "
            f"--data-dir {os.path.dirname(path)}"
        )
    # Imported here, not at the top: both are optional extras (and Piper is GPL), so
    # the rest of Mika and its tests must not need them installed.
    import edge_tts
    from piper import PiperVoice

    print(f"Loading Piper voice '{os.path.basename(path)}'...")
    return Voices(edge=edge_tts.Communicate, piper=PiperVoice.load(path))


async def fetch_mp3(edge, text):
    communicate = edge(text, VOICE, rate=RATE, pitch=PITCH)
    parts = []
    async for chunk in communicate.stream():
        if chunk["type"] == "audio":
            parts.append(chunk["data"])
    return b"".join(parts)


def decode_mp3(mp3):
    if len(mp3) == 0:
        return np.zeros((0, 1), dtype=np.int16)
    result = subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            "pipe:0",
            "-f",
            "s16le",
            "-ac",
            "1",
            "-ar",
            str(SAMPLE_RATE),
            "pipe:1",
        ],
        input=mp3,
        capture_output=True,
    )
    if result.returncode != 0:
        # errors="replace": on Windows ffmpeg may print non-UTF-8 bytes (e.g. folder names),
        # and a UnicodeDecodeError here would hide the real error.
        error = result.stderr.decode(errors="replace").strip()
        raise ValueError(f"ffmpeg failed: {error}")
    return np.frombuffer(result.stdout, dtype="<i2").reshape(-1, 1)


async def synthesize_edge(edge, text):
    mp3 = await fetch_mp3(edge, text)
    return await asyncio.to_thread(decode_mp3, mp3)


def synthesize_piper(piper, text):
    # TODO (5D): speak `text` with Piper; return (samples, 1) int16 at SAMPLE_RATE.
    #   - piper.synthesize(text) yields one chunk per sentence
    #   - each chunk has sample_rate, sample_width (bytes), sample_channels, and
    #     audio_int16_array (1-D int16)
    #   - anything but SAMPLE_RATE, SAMPLE_WIDTH and 1 channel: raise ValueError that
    #     names what it got (a 22 kHz "medium" voice would play slow and deep)
    #   - join the chunks in order with np.concatenate, then reshape to (-1, 1)
    #   - no chunks at all: np.zeros((0, 1), dtype=np.int16)
    raise NotImplementedError


async def synthesize(voices, text):
    # TODO (5D): Edge if it is ready within EDGE_DEADLINE, otherwise Piper.
    #   - try: return await asyncio.wait_for(synthesize_edge(voices.edge, text),
    #     EDGE_DEADLINE)
    #   - except Exception as err: print why Edge was not used, e.g.
    #     "Edge failed (...); speaking with Piper". At the deadline err is a
    #     TimeoutError, so you can print "no answer within 4.0 s" for that case.
    #   - then: return await asyncio.to_thread(synthesize_piper, voices.piper, text)
    #   Do not catch errors from Piper: if both fail, the server must send tts_error.
    raise NotImplementedError


def save_wav(path, audio):
    with wave.open(path, "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(SAMPLE_WIDTH)
        f.setframerate(SAMPLE_RATE)
        f.writeframes(audio.astype("<i2").tobytes())


def main():
    parser = argparse.ArgumentParser(description="Speak a sentence with Mika's voice.")
    parser.add_argument("text", help="what Mika should say")
    parser.add_argument("--wav", help="save the audio to this file instead of playing it")
    parser.add_argument("--piper", action="store_true", help="skip Edge and use Piper")
    args = parser.parse_args()

    try:
        voices = load_voice()
    except FileNotFoundError as err:
        print(f"Error: {err}")
        return

    start = time.perf_counter()
    if args.piper:
        audio = synthesize_piper(voices.piper, args.text)
    else:
        audio = asyncio.run(synthesize(voices, args.text))
    took = time.perf_counter() - start

    seconds = len(audio) / SAMPLE_RATE
    # With Edge this is mostly network time: the wait before Mika can start talking.
    print(f"{seconds:.1f} s of speech, ready after {took:.2f} s")

    if args.wav:
        save_wav(args.wav, audio)
        print(f"Saved to {args.wav}")
        return

    import sounddevice as sd  # only needed for playback

    sd.play(audio, SAMPLE_RATE)
    sd.wait()


if __name__ == "__main__":
    main()
