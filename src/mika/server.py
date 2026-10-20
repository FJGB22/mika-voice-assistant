import asyncio
import time
import traceback

from websockets.asyncio.server import serve
from websockets.exceptions import ConnectionClosed, ConnectionClosedError

from mika import protocol
from mika.config import ENV_PATH, LLM_API_KEY_NAME, apply_env, get_llm_api_key, load_env
from mika.discovery import announce
from mika.llm import ask, make_client
from mika.session import THINKING, Session
from mika.stt import load_whisper, transcribe
from mika.tts import load_voice, synthesize
from mika.vad import FRAME_MS

HOST = "0.0.0.0"  # every network interface, so an ESP32 on the same Wi-Fi can connect
PORT = 8765

FRAME_SECONDS = FRAME_MS / 1000
# Sent at once before pacing starts: the satellite holds 200 ms in hand, so a short
# Wi-Fi hiccup does not cut Mika off mid-word.
PREBUFFER_FRAMES = 10


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


def frame_schedule(count):
    # Seconds after the first frame at which each frame is sent: the prebuffer at
    # once, then one frame per 20 ms, the pace the satellite plays them at.
    return [max(0, i - PREBUFFER_FRAMES + 1) * FRAME_SECONDS for i in range(count)]


async def send_speech(websocket, frames):
    loop = asyncio.get_running_loop()
    start = loop.time()
    for frame, offset in zip(frames, frame_schedule(len(frames)), strict=True):
        # Measured from start, not "sleep 20 ms each time": every sleep and send
        # overruns a little, and that error would add up over a long reply.
        delay = start + offset - loop.time()
        if delay > 0:
            await asyncio.sleep(delay)
        await websocket.send(frame)


async def answer(websocket, session, audio, history, client, whisper, voice):
    try:
        # Whisper and Gemini block for seconds; in a thread the event loop keeps
        # answering pings, so the connection is not dropped.
        transcript, reply = await asyncio.to_thread(
            transcribe_and_ask, audio, history, client, whisper
        )
    except Exception as err:
        print(f"Question failed: {err!r}")
        await send_all(websocket, session.fail(protocol.SERVER_ERROR))
        return
    if reply is None:
        await send_all(websocket, session.fail(protocol.EMPTY_TRANSCRIPT))
        return

    # The text first: the satellite learns the answer while the speech is made.
    await send_all(websocket, session.finish(transcript, reply))
    await speak_reply(websocket, session, voice, reply)


async def speak_reply(websocket, session, voice, reply):
    start = time.perf_counter()
    try:
        speech = await synthesize(voice, reply)
    except Exception as err:
        print(f"Failed to synthesize speech: {err!r}")
        await send_all(websocket, session.fail(protocol.TTS_ERROR))
        return
    print(f"Speech time: {time.perf_counter() - start:.2f} seconds")
    await send_speech(websocket, protocol.split_frames(speech))
    await send_all(websocket, session.finish_speaking())


def report_crash(task):
    # Runs when an answer task ends. Without it a bug inside the task would only show
    # up as "Task exception was never retrieved", if at all.
    # cancelled() first: exception() raises on a cancelled task.
    if task.cancelled():
        return
    err = task.exception()
    if err is None:
        return
    # A satellite that disconnects while the reply is being sent is normal for a device.
    if isinstance(err, ConnectionClosed):
        return
    traceback.print_exception(err)


def start_answer(answering, websocket, session, history, client, whisper, voice):
    # Called from the loop, not the task: a RuntimeError from take_utterance is a server
    # bug and should crash loudly, not reach the satellite as a server_error.
    audio = session.take_utterance()
    # A task, not an await: the loop keeps reading, so a start that arrives meanwhile
    # gets busy and stray frames are dropped by Session.
    task = asyncio.create_task(answer(websocket, session, audio, history, client, whisper, voice))
    answering.add(task)
    task.add_done_callback(answering.discard)
    task.add_done_callback(report_crash)


def make_handler(client, whisper, voice):
    async def handler(websocket):
        # Created per connection: a reconnect starts a fresh session and conversation.
        session = Session()
        history = []
        # asyncio keeps only weak references to tasks, so a task nobody holds can be
        # garbage-collected mid-answer. The set holds them until they finish.
        answering = set()
        print(f"Connected: {websocket.remote_address}")
        try:
            async for message in websocket:
                was_thinking = session.state == THINKING
                if isinstance(message, bytes):
                    messages_to_send = session.handle_audio(message)
                else:
                    messages_to_send = session.handle_text(message)
                await send_all(websocket, messages_to_send)

                # Only on the step into THINKING: while a question is answered, every
                # later message also sees THINKING and must not start a second answer.
                if session.state == THINKING and not was_thinking:
                    start_answer(answering, websocket, session, history, client, whisper, voice)
        except ConnectionClosedError:
            # How a satellite that loses power or Wi-Fi ends: normal for a device, so one
            # line instead of a "connection handler failed" traceback.
            print(f"Connection lost: {websocket.remote_address}")
        finally:
            # Nobody is left to hear the answer. The thread still runs to the end (a
            # thread cannot be cancelled), but its result is dropped instead of sent.
            # list(): a task that finishes removes itself from the set.
            for task in list(answering):
                task.cancel()
            # In finally, so the line also appears when the connection drops abruptly.
            print(f"Disconnected: {websocket.remote_address}")

    return handler


async def run(client, whisper, voice):
    # Raw PCM barely compresses, so deflate would only cost CPU on both ends.
    async with serve(make_handler(client, whisper, voice), HOST, PORT, compression=None) as server:
        print(f"Listening on ws://{HOST}:{PORT}")
        async with announce(PORT):
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

    # Before Whisper, which takes a while to load: a missing ffmpeg is reported at once.
    try:
        voice = load_voice()
    except FileNotFoundError as err:
        print(f"Error: {err}")
        return

    client = make_client(llm_api_key)
    whisper = load_whisper()

    try:
        asyncio.run(run(client, whisper, voice))
    except KeyboardInterrupt:
        print("\nServer stopped.")


if __name__ == "__main__":
    main()
