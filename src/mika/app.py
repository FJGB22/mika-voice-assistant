import os
import time
import wave

import numpy as np
import sounddevice as sd
from faster_whisper import WhisperModel
from google import genai

# change this path to your desired location for the environment file
ENV_PATH = os.path.normpath(os.path.expanduser("~/.secrets/mika-assistant.env"))

MODEL = "gemini-3.5-flash-lite"

WHISPER_MODEL = "base"
WHISPER_LANGUAGE = "en"

SAMPLE_RATE = 16000
CHANNELS = 1
SAMPLE_WIDTH = 2  # 16-bit audio | int16 = 2 bytes each sample
RECORD_SECONDS = 5

SYSTEM_PROMPT = """You are Mika, a helpful and friendly AI assistant.
You help the user with their questions and tasks.
Answer in at most 2-3 sentences.
No markdown, no emoji.
Do not offer further assistance at the end of your replies."""


def load_env(path):
    values = {}

    with open(path, encoding="utf-8") as f:
        content = f.read()

    for line in content.splitlines():
        line = line.strip()

        if not line or line.startswith("#"):
            continue

        key, _, value = line.partition("=")
        values[key.strip()] = value.strip()

    return values


def apply_env(values):
    for key, value in values.items():
        os.environ.setdefault(key, value)


def chat_loop(client, whisper):
    history = []

    try:
        while True:
            command = input("Press enter to speak, or type a message: ").strip()

            if command.lower() in ("exit", "quit"):
                print("Powering down. Goodbye!")
                break

            if command:
                text = command
            else:
                audio = record_audio()
                save_wav(audio, "temp.wav")
                text = transcribe(whisper, "temp.wav")
                print(f"You said: {text}")

            if not text:
                print("No input detected. Please try again.")
                continue

            history.append({"role": "user", "content": text})
            print("Mika: ", end="", flush=True)

            try:
                reply = ask(history, client)
            except Exception as e:
                print(
                    "i'm sorry, there was an error processing your request. "
                    f"Please try again later. Error details: {e}"
                )
                continue

            history.append({"role": "assistant", "content": reply})

    except KeyboardInterrupt:
        print("\nPowering down. Goodbye!")
    finally:
        print(f"Total messages in history: {len(history)}")


def ask(history, client):
    stream = client.interactions.create(
        model=MODEL,
        input=to_gemini_input(history),
        system_instruction=SYSTEM_PROMPT,
        stream=True,
        generation_config={
            "max_output_tokens": 300,
        },
    )

    parts = []

    for part in stream:
        if part.event_type != "step.delta":
            continue
        if part.delta.type != "text":
            continue

        print(part.delta.text, end="", flush=True)
        parts.append(part.delta.text)

    print()
    return "".join(parts)


def to_gemini_input(messages):
    gemini_messages = []

    for message in messages:
        if message["role"] == "user":
            step_type = "user_input"
        elif message["role"] == "assistant":
            step_type = "model_output"
        else:
            raise ValueError(f"unknown role: {message['role']}")

        gemini_messages.append(
            {
                "type": step_type,
                "content": [{"type": "text", "text": message["content"]}],
            }
        )

    return gemini_messages


def list_audio_devices():
    print(sd.query_devices())


def record_audio(seconds=RECORD_SECONDS):
    frame_count = int(SAMPLE_RATE * seconds)
    print(f"Recording audio for {seconds} seconds...")

    audio_data = sd.rec(frame_count, samplerate=SAMPLE_RATE, channels=CHANNELS, dtype="int16")
    sd.wait()  # Wait until recording is finished

    peak = np.abs(audio_data).max()
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


def main():
    print("Hello there! this is Mika, your AI assistant. How can I help you today?")

    try:
        env = load_env(ENV_PATH)
    except FileNotFoundError:
        print(
            f"Error: {ENV_PATH} not found. "
            "Please create the file and add your environment variables."
        )
        return

    print(f"Loaded environment variables: {len(env)}")

    apply_env(env)
    llm_api_key = os.environ.get("LLM_API_KEY")

    if not llm_api_key:
        print(f"Error: {ENV_PATH} does not contain LLM_API_KEY. Please add it to the file.")
        return

    print(f"LLM_API_KEY is set. the key has {len(llm_api_key)} characters.")
    client = genai.Client(api_key=llm_api_key)

    whisper = load_whisper()
    chat_loop(client, whisper)
