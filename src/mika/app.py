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


def main():
    print("Hello there! this is Mika, your AI assistant. How can I help you today?")

    env = load_env(ENV_PATH)
    print(f"Loaded environment variables: {len(env)}")
