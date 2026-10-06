import asyncio

from websockets.asyncio.server import serve
from websockets.exceptions import ConnectionClosedError

from mika import protocol
from mika.config import ENV_PATH, LLM_API_KEY_NAME, apply_env, get_llm_api_key, load_env
from mika.llm import ask, make_client
from mika.session import THINKING, Session
from mika.stt import load_whisper, transcribe

HOST = "0.0.0.0"  # every network interface, so an ESP32 on the same Wi-Fi can connect
PORT = 8765


# Called by plain name, not stt.transcribe or llm.ask, so the tests can monkeypatch them.
def transcribe_and_ask(audio, history, client, whisper):
    transcript = transcribe(whisper, audio)
    if not transcript:
        return transcript, None
    # Recorded before asking, so a failed ask keeps the question for the retry.
    history.append({"role": "user", "content": transcript})
    reply = ask(history, client)
    history.append({"role": "assistant", "content": reply})
    # No try/except here: the handler decides what a failure means for the satellite.
    return transcript, reply


async def send_all(websocket, messages):
    for message in messages:
        await websocket.send(protocol.encode_message(message))


def make_handler(client, whisper):
    async def handler(websocket):
        # Created per connection: a reconnect starts a fresh session and conversation.
        session = Session()
        history = []
        print(f"Connected: {websocket.remote_address}")
        try:
            async for message in websocket:
                if isinstance(message, bytes):
                    messages_to_send = session.handle_audio(message)
                else:
                    messages_to_send = session.handle_text(message)
                await send_all(websocket, messages_to_send)

                if session.state == THINKING:
                    # Outside the try: a RuntimeError here is a server bug and should
                    # crash loudly, not reach the satellite as a server_error.
                    audio = session.take_utterance()
                    try:
                        # Whisper and Gemini block for seconds; in a thread the event
                        # loop keeps answering pings, so the connection is not dropped.
                        transcript, reply = await asyncio.to_thread(
                            transcribe_and_ask, audio, history, client, whisper
                        )
                    except Exception as err:
                        print(f"Question failed: {err!r}")
                        await send_all(websocket, session.fail(protocol.SERVER_ERROR))
                    else:
                        if reply is None:
                            await send_all(websocket, session.fail(protocol.EMPTY_TRANSCRIPT))
                        else:
                            await send_all(websocket, session.finish(transcript, reply))
        except ConnectionClosedError:
            # How a satellite that loses power or Wi-Fi ends: normal for a device, so one
            # line instead of a "connection handler failed" traceback.
            print(f"Connection lost: {websocket.remote_address}")
        finally:
            # In finally, so the line also appears when the connection drops abruptly.
            print(f"Disconnected: {websocket.remote_address}")

    return handler


async def run(client, whisper):
    # Raw PCM barely compresses, so deflate would only cost CPU on both ends.
    async with serve(make_handler(client, whisper), HOST, PORT, compression=None) as server:
        print(f"Listening on ws://{HOST}:{PORT}")
        await server.serve_forever()


def main():
    try:
        apply_env(load_env(ENV_PATH))
    except FileNotFoundError:
        print(f"Error: {ENV_PATH} not found.")
        return

    llm_api_key = get_llm_api_key()
    if not llm_api_key:
        print(f"Error: {LLM_API_KEY_NAME} is not set.")
        return

    client = make_client(llm_api_key)
    whisper = load_whisper()

    try:
        asyncio.run(run(client, whisper))
    except KeyboardInterrupt:
        print("\nServer stopped.")


if __name__ == "__main__":
    main()
