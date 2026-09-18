import time

from faster_whisper import WhisperModel

WHISPER_MODEL = "base"
WHISPER_LANGUAGE = "en"


def load_whisper():
    print(f"Loading Whisper model '{WHISPER_MODEL}'...")
    model = WhisperModel(WHISPER_MODEL, device="cpu", compute_type="int8")
    print("Whisper model loaded.")
    return model


def transcribe(model, path):
    start = time.perf_counter()

    segments, _info = model.transcribe(
        path,
        language=WHISPER_LANGUAGE,
        beam_size=1,
        vad_filter=True,
        condition_on_previous_text=False,
    )

    texts = []

    for segment in segments:
        texts.append(segment.text)

    text = "".join(texts).strip()
    end = time.perf_counter()

    print(f"Transcription time: {end - start:.2f} seconds")
    return text
