from mika.audio import record_until_silence
from mika.config import ENV_PATH, LLM_API_KEY_NAME, apply_env, get_llm_api_key, load_env
from mika.llm import ask, make_client
from mika.stt import load_whisper, transcribe


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
                audio = record_until_silence()
                if audio is None:
                    print("No speech detected. Please try again.")
                    continue

                text = transcribe(whisper, audio)
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

    llm_api_key = get_llm_api_key()

    if not llm_api_key:
        print(f"Error: {ENV_PATH} does not contain {LLM_API_KEY_NAME}. Please add it to the file.")
        return

    # Only the length: enough to spot an empty or cut-off key without showing it.
    print(f"{LLM_API_KEY_NAME} is set. the key has {len(llm_api_key)} characters.")

    client = make_client(llm_api_key)
    whisper = load_whisper()
    chat_loop(client, whisper)
