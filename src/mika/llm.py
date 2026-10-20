import time

from google import genai
from google.genai import types

MODEL = "gemini-3.5-flash-lite"
# Replies will be spoken aloud later: short, and no symbols a TTS would read out.
SYSTEM_PROMPT = """You are Mika, a helpful and friendly AI assistant.
You help the user with their questions and tasks.
Answer in at most 2-3 sentences.
No markdown, no emoji.
Do not offer further assistance at the end of your replies."""


# generate_content, not the Interactions API: measured on the same network, the
# first text came after about 1 s instead of 4-6 s (notes/bench_llm.py, Oct 2026).
# The history is kept here anyway, so Interactions' server-side state is not needed.
def ask(history, client):
    start = time.perf_counter()
    stream = client.models.generate_content_stream(
        model=MODEL,
        contents=to_gemini_input(history),
        config=types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT,
            max_output_tokens=300,
            # Mika gives Gemini no functions to call (yet). Off, the SDK also skips
            # its function-calling loop and the warning that loop prints.
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        ),
    )

    parts = []
    # Two moments tell different stories: the first event of any kind is Gemini
    # answering at all (network), the first text is the model starting to write.
    first_event = None
    first_text = None

    for chunk in stream:
        if first_event is None:
            first_event = time.perf_counter() - start
        # A chunk can carry no text, e.g. only usage information at the end.
        if not chunk.text:
            continue

        if first_text is None:
            first_text = time.perf_counter() - start
        print(chunk.text, end="", flush=True)
        parts.append(chunk.text)

    print()
    print(f"Gemini time: {timings(first_event, first_text, time.perf_counter() - start)}")
    return "".join(parts)


def timings(first_event, first_text, done):
    def seconds(value):
        return "none" if value is None else f"{value:.2f} s"

    return (
        f"first event {seconds(first_event)}, "
        f"first text {seconds(first_text)}, "
        f"done {seconds(done)}"
    )


def to_gemini_input(messages):
    gemini_messages = []

    for message in messages:
        if message["role"] == "user":
            role = "user"
        elif message["role"] == "assistant":
            role = "model"  # Gemini's name for its own turns
        else:
            raise ValueError(f"unknown role: {message['role']}")

        gemini_messages.append({"role": role, "parts": [{"text": message["content"]}]})

    return gemini_messages


def make_client(llm_api_key):
    return genai.Client(api_key=llm_api_key)
