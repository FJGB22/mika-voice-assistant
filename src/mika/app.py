import os
from google import genai

# change this path to your desired location for the environment file
ENV_PATH = os.path.normpath(os.path.expanduser("~/.secrets/mika-assistant.env"))
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


def chat_loop(client):
    history = []

    try:
        while True:
            text = input("You: ").strip()

            if not text:
                continue

            if text.lower() in ("exit", "quit"):
                print("Powering down. Goodbye!")
                break

            history.append({"role": "user", "content": text})
            print("Mika: ", end="", flush=True)

            try:
                reply = ask(history, client)
            except Exception as e:
                print(f"i'm sorry, there was an error processing your request. Please try again later. error details: {e}")
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


def to_gemini_input(messages):
    gemini_messages = []

    for message in messages:
        if message["role"] == "user":
            step_type = "user_input"
        elif message["role"] == "assistant":
            step_type = "model_output"
        else:
            raise ValueError(f"unknown role: {message['role']}")

        gemini_messages.append({
            "type": step_type,
            "content": [{"type": "text", "text": message["content"]}],
        })

    return gemini_messages


def main():
    print("Hello there! this is Mika, your AI assistant. How can I help you today?")

    try:
        env = load_env(ENV_PATH)
    except FileNotFoundError:
        print(f"Error: {ENV_PATH} not found. Please create the file and add your environment variables.")
        return

    print(f"Loaded environment variables: {len(env)}")

    apply_env(env)
    llm_api_key = os.environ.get("LLM_API_KEY")

    if not llm_api_key:
        print(f"Error: {ENV_PATH} does not contain LLM_API_KEY. Please add it to the file.")
        return

    print(f"LLM_API_KEY is set. the key has {len(llm_api_key)} characters.")
    client = genai.Client(api_key=llm_api_key)

    chat_loop(client)
