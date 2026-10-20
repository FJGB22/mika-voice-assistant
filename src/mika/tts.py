"""Text to speech: turns Mika's reply into 16 kHz mono int16 audio, the same format
the satellite's microphone sends. Only this module knows which TTS engine is used.

Try it on the laptop:
    python -m mika.tts "Hello, I am Mika."
"""

import argparse
import asyncio
import shutil
import subprocess
import time
import wave

import numpy as np

from mika.vad import SAMPLE_RATE

# The same voice as Mika 1.0 (the VTuber), so both are recognisably the same character.
VOICE = "en-US-AnaNeural"
RATE = "+18%"  # a bit fast, like a streamer
PITCH = "+8Hz"

SAMPLE_WIDTH = 2  # bytes: int16


def load_voice():
    # Checked once at startup, so a missing ffmpeg is reported before the first
    # question instead of in the middle of an answer.
    if shutil.which("ffmpeg") is None:
        raise FileNotFoundError(
            "ffmpeg not found on PATH. It decodes Edge's MP3 into raw audio.\n"
            "Install it (e.g. winget install Gyan.FFmpeg), then open a new terminal."
        )
    # Imported here, not at the top: edge-tts is an optional extra, so the rest of
    # Mika (and its tests) must not need it installed.
    import edge_tts

    # The class itself, not an instance: one Communicate is made per sentence to speak.
    return edge_tts.Communicate


async def fetch_mp3(voice, text):
    communicate = voice(text, VOICE, rate=RATE, pitch=PITCH)
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


async def synthesize(voice, text):
    mp3 = await fetch_mp3(voice, text)
    audio = await asyncio.to_thread(decode_mp3, mp3)
    return audio


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
    args = parser.parse_args()

    try:
        voice = load_voice()
    except FileNotFoundError as err:
        print(f"Error: {err}")
        return

    start = time.perf_counter()
    audio = asyncio.run(synthesize(voice, args.text))
    took = time.perf_counter() - start

    seconds = len(audio) / SAMPLE_RATE
    # Edge works in the cloud, so this is mostly network time: the wait before Mika
    # can start talking, not how hard the laptop works.
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
