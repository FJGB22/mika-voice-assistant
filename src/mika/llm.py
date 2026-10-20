import time

from google import genai

MODEL = "gemini-3.5-flash-lite"
# Replies will be spoken aloud later: short, and no symbols a TTS would read out.
SYSTEM_PROMPT = """You are Mika, a helpful and friendly AI assistant.
You help the user with their questions and tasks.
Answer in at most 2-3 sentences.
No markdown, no emoji.
Do not offer further assistance at the end of your replies."""


def ask(history, client):
    start = time.perf_counter()
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
    # Two moments tell different stories: the first event of any kind is Gemini
    # answering at all (network), the first text is the model starting to write.
    first_event = None
    first_text = None

    for part in stream:
        if first_event is None:
            first_event = time.perf_counter() - start
        if part.event_type != "step.delta":
            continue
        if part.delta.type != "text":
            continue

        if first_text is None:
            first_text = time.perf_counter() - start
        print(part.delta.text, end="", flush=True)
        parts.append(part.delta.text)

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


def make_client(llm_api_key):
    return genai.Client(api_key=llm_api_key)
