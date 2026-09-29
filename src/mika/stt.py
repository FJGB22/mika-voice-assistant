import time

import numpy as np
from faster_whisper import WhisperModel

from mika.vad import FULL_SCALE

WHISPER_MODEL = "base"
# A fixed language skips auto-detection, which is slow and unreliable on short clips.
WHISPER_LANGUAGE = "en"


def load_whisper():
    print(f"Loading Whisper model '{WHISPER_MODEL}'...")
    model = WhisperModel(WHISPER_MODEL, device="cpu", compute_type="int8")
    print("Whisper model loaded.")
    return model


def transcribe(model, audio):
    start = time.perf_counter()

    segments, _info = model.transcribe(
        to_whisper_input(audio),
        language=WHISPER_LANGUAGE,
        beam_size=1,
        vad_filter=True,
        condition_on_previous_text=False,  # avoids repeated or looping text
    )

    # segments is a generator: transcription happens inside this loop,
    # so the timer has to stop after it.
    texts = []

    for segment in segments:
        texts.append(segment.text)

    text = "".join(texts).strip()
    end = time.perf_counter()

    print(f"Transcription time: {end - start:.2f} seconds")
    return text


def to_whisper_input(audio):
    flat = audio[:, 0]
    return flat.astype(np.float32) / FULL_SCALE  # Whisper expects float32 in [-1, 1]
