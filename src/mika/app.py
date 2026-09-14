import os

ENV_PATH = os.path.expanduser("~/.secrets/mika-assistant.env")


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

    chat_loop()
