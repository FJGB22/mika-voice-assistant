import numpy as np
import sounddevice as sd

from mika.vad import DONE, FRAME_MS, FRAME_SAMPLES, NO_SPEECH, SAMPLE_RATE, SpeechDetector, frame_db

CHANNELS = 1


def list_audio_devices():
    print(sd.query_devices())


def meter(seconds=10):
    frame_count = seconds * 1000 // FRAME_MS
    overflows = 0
    print("Speak, stay quiet, speak again. watching levels...")

    with sd.InputStream(
        samplerate=SAMPLE_RATE, channels=CHANNELS, dtype="int16", blocksize=FRAME_SAMPLES
    ) as stream:
        for _ in range(frame_count):
            frame, overflowed = stream.read(FRAME_SAMPLES)
            # Overflow means samples were dropped because we read too slowly.
            if overflowed:
                overflows += 1
            db = frame_db(frame)
            bar = "#" * max(0, int((db + 80) / 2))  # Scale dB to a bar length
            print(f"\r{db:6.2f} dB | {bar:<40}", end="", flush=True)

    print()
    print(f"Overflows detected: {overflows}")


def record_until_silence():
    detector = SpeechDetector()
    overflows = 0
    print("listening...")

    with sd.InputStream(
        samplerate=SAMPLE_RATE, channels=CHANNELS, dtype="int16", blocksize=FRAME_SAMPLES
    ) as stream:
        while True:
            frame, overflowed = stream.read(FRAME_SAMPLES)
            if overflowed:
                overflows += 1

            detection = detector.process(frame)
            if detection == DONE:
                print("done listening.")
                if overflows > 0:
                    print(f"Overflows detected: {overflows}")
                return np.concatenate(detector.frames, axis=0)

            if detection == NO_SPEECH:
                return None
