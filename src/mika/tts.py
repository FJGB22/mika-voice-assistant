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

from mika.config import ENV_PATH, apply_env, load_env
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
    # Blocking: Piper runs its model on the CPU, so synthesize calls this in a thread.
    chunks = list(piper.synthesize(text))  # one chunk per sentence
    if not chunks:
        return np.zeros((0, 1), dtype=np.int16)
    # Rejected instead of converted: a 22 kHz "medium" voice played as 16 kHz sounds
    # slow and deep, and nothing would point at the cause.
    for chunk in chunks:
        if chunk.sample_rate != SAMPLE_RATE:
            raise ValueError(f"Expected {SAMPLE_RATE} Hz, got {chunk.sample_rate} Hz")
        if chunk.sample_width != SAMPLE_WIDTH:
            raise ValueError(f"Expected {SAMPLE_WIDTH} bytes, got {chunk.sample_width} bytes")
        if chunk.sample_channels != 1:
            raise ValueError(f"Expected 1 channel, got {chunk.sample_channels} channels")
    return np.concatenate([chunk.audio_int16_array for chunk in chunks]).reshape(-1, 1)


async def synthesize(voices, text):
    try:
        # wait_for cancels Edge at the deadline, so a stalled request does not keep its
        # connection open for an answer nobody will use.
        return await asyncio.wait_for(synthesize_edge(voices.edge, text), EDGE_DEADLINE)
    # Exception, not BaseException: when the satellite disconnects, the answer is
    # cancelled, and Piper must not start speaking to nobody.
    except Exception as err:
        if isinstance(err, TimeoutError):
            print(f"Edge failed (no answer within {EDGE_DEADLINE:.1f} s); speaking with Piper")
        else:
            # !r: many network errors have an empty message; the type always shows.
            print(f"Edge failed ({err!r}); speaking with Piper")
        # Not caught: if Piper fails too, the server must send tts_error, not silence.
        return await asyncio.to_thread(synthesize_piper, voices.piper, text)


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

    # TTS_VOICE may be set in the secrets file, as for the server; without this, the
    # server and this command could quietly use different Piper voices. Edge needs no
    # key, so a missing file is fine here.
    try:
        apply_env(load_env(ENV_PATH))
    except FileNotFoundError:
        pass

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
