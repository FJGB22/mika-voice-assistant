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
