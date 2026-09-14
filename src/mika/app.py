import os
from google import genai

ENV_PATH = os.path.expanduser("~/.secrets/mika-assistant.env")
MODEL = "gemini-3.5-flash-lite"
SYSTEM_PROMPT = "You are Mika, a helpful and friendly AI assistant. You are here to assist the user with their questions and tasks. max 2 - 3 sentences per response, no markdown, no emojis, no offer for futher assistance"


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


def chat_loop():
    history = []

    while True:
        text = input("You: ").strip()

        if not text:
            continue

        if text.lower() in ("exit", "quit"):
            print("Powering down. Goodbye!")
            break

        history.append({"role": "user", "content": text})
        reply = "how can i help you with that?"  # tempat resposno llmnya nanti
        print(f"Mika: {reply}")
        history.append({"role": "assistant", "content": reply})

    print(f"Total messages in history: {len(history)}")


def ask_once(question):
    client = genai.Client(api_key=os.environ["LLM_API_KEY"])

    stream = client.interactions.create(
        model=MODEL,
        input=question,
        system_instruction=SYSTEM_PROMPT,
        stream=True,
        generation_config={
            "max_output_tokens": 300,
        }
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


def main():
    print("Hello there! this is Mika, your AI assistant. How can I help you today?")
    env = load_env(ENV_PATH)
    print(f"Loaded environment variables: {len(env)}")
    apply_env(env)
    llm_api_key = os.environ.get("LLM_API_KEY")

    if not llm_api_key:
        print(f"Error: {ENV_PATH} does not contain LLM_API_KEY. Please add it to the file.")
        return

    print(f"LLM_API_KEY is set. the key has {len(llm_api_key)} characters.")

    ask_once("Hello Mika, can you tell me a joke?")
    ask_once("What is the capital of France?")
    ask_once("tell me about cats")
    ask_once("list the way to learn python programming")
