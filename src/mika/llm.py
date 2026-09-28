from google import genai

MODEL = "gemini-3.5-flash-lite"
# Replies will be spoken aloud later: short, and no symbols a TTS would read out.
SYSTEM_PROMPT = """You are Mika, a helpful and friendly AI assistant.
You help the user with their questions and tasks.
Answer in at most 2-3 sentences.
No markdown, no emoji.
Do not offer further assistance at the end of your replies."""


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


def make_client(llm_api_key):
    return genai.Client(api_key=llm_api_key)
