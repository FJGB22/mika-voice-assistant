import os

# change this path to your desired location for the environment file
ENV_PATH = os.path.normpath(os.path.expanduser("~/.secrets/mika-assistant.env"))
LLM_API_KEY_NAME = "LLM_API_KEY"


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


def get_llm_api_key():
    return os.environ.get(LLM_API_KEY_NAME)
