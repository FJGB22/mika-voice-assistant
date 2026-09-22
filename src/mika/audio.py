import wave

import numpy as np
import sounddevice as sd

from mika.vad import frame_db

SAMPLE_RATE = 16000
CHANNELS = 1
SAMPLE_WIDTH = 2  # 16-bit audio | int16 = 2 bytes each sample
RECORD_SECONDS = 5

FRAME_MS = 20
FRAME_SAMPLES = SAMPLE_RATE * FRAME_MS // 1000  # 320 samples for 20 ms at 16 kHz


def list_audio_devices():
    print(sd.query_devices())


def record_audio(seconds=RECORD_SECONDS):
    frame_count = int(SAMPLE_RATE * seconds)
    print(f"Recording audio for {seconds} seconds...")

    audio_data = sd.rec(frame_count, samplerate=SAMPLE_RATE, channels=CHANNELS, dtype="int16")
    sd.wait()  # Wait until recording is finished

    peak = np.abs(audio_data.astype(np.int32)).max()
    print(f"Peak audio level: {peak} out of 32767 ({peak / 32767:.2%})")
    if peak < 0.10 * 32767:
        print("Warning: input level is low. Check your microphone.")

    return audio_data


def save_wav(audio, path):
    with wave.open(path, "wb") as wf:
        wf.setnchannels(CHANNELS)
        wf.setsampwidth(SAMPLE_WIDTH)
        wf.setframerate(SAMPLE_RATE)
        wf.writeframes(audio.tobytes())


def meter(seconds=10):
    frame_count = seconds * 1000 // FRAME_MS
    overflows = 0
    print("Speak, stay quiet, speak again. watching levels...")

    with sd.InputStream(
        samplerate=SAMPLE_RATE, channels=CHANNELS, dtype="int16", blocksize=FRAME_SAMPLES
    ) as stream:
        for _ in range(frame_count):
            frame, overflowed = stream.read(FRAME_SAMPLES)
            if overflowed:
                overflows += 1
            db = frame_db(frame)
            bar = "#" * max(0, int((db + 80) / 2))  # Scale dB to a bar length
            print(f"\r{db:6.2f} dB | {bar:<40}", end="", flush=True)

    print()
    print(f"Overflows detected: {overflows}")
