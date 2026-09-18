import wave

import numpy as np
import sounddevice as sd

SAMPLE_RATE = 16000
CHANNELS = 1
SAMPLE_WIDTH = 2  # 16-bit audio | int16 = 2 bytes each sample
RECORD_SECONDS = 5


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
