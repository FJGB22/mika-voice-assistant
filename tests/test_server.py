import asyncio
import json
import threading

import numpy as np
import pytest
from websockets.asyncio.client import connect
from websockets.asyncio.server import serve

from mika import protocol, server
from mika.vad import END_FRAMES, FRAME_SAMPLES, START_FRAMES

LOUD = np.full(FRAME_SAMPLES, 3000, dtype="<i2").tobytes()
QUIET = bytes(protocol.FRAME_BYTES)
START = json.dumps({"type": protocol.START})


# Stand-ins for the real Whisper model and Gemini client. The fakes below check that
# they arrive in the right argument, so a swapped call fails here, not on the device.
WHISPER = object()
CLIENT = object()


def fake_transcribe(transcripts):
    def transcribe(whisper, audio):
        assert whisper is WHISPER, "transcribe(whisper, audio): arguments in the wrong order?"
        return transcripts.pop(0)

    return transcribe


class FakeLLM:
    """Stands in for ask(): records the history it saw, answers or raises."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.seen = []
        self.threads = []

    def __call__(self, history, client):
        assert client is CLIENT, "ask(history, client): arguments in the wrong order?"
        self.threads.append(threading.current_thread())
        self.seen.append([turn.copy() for turn in history])
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


@pytest.fixture
def fake_backend(monkeypatch):
    def install(transcripts, replies):
        transcripts = list(transcripts)
        llm = FakeLLM(replies)
        monkeypatch.setattr(server, "transcribe", fake_transcribe(transcripts))
        monkeypatch.setattr(server, "ask", llm)
        return llm

    return install


# --- transcribe_and_ask: the blocking part, tested without a network ---


def test_transcribe_and_ask_records_both_turns(fake_backend):
    fake_backend(["hello"], ["Hi!"])
    history = []

    result = server.transcribe_and_ask(None, history, CLIENT, WHISPER)

    assert result == ("hello", "Hi!")
    assert history == [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "Hi!"},
    ]


def test_transcribe_and_ask_skips_llm_on_empty_text(fake_backend):
    llm = fake_backend([""], [])
    history = []

    result = server.transcribe_and_ask(None, history, CLIENT, WHISPER)

    assert result == ("", None)
    assert history == []
    assert llm.seen == []


def test_transcribe_and_ask_keeps_question_when_llm_fails(fake_backend):
    fake_backend(["hello"], [RuntimeError("quota")])
    history = []

    with pytest.raises(RuntimeError):
        server.transcribe_and_ask(None, history, CLIENT, WHISPER)

    assert history == [{"role": "user", "content": "hello"}]


# --- handler: the whole path over a real local WebSocket ---


async def ask_once(websocket):
    await websocket.send(START)
    for _ in range(START_FRAMES):
        await websocket.send(LOUD)
    for _ in range(END_FRAMES):
        await websocket.send(QUIET)


async def receive(websocket, count):
    # A timeout turns a missing message into a failure instead of a hung test run.
    return [json.loads(await asyncio.wait_for(websocket.recv(), 5)) for _ in range(count)]


def talk(turns, connections=1):
    """Starts one server and connects to it `connections` times in a row, asking
    len(turns) questions each time. Returns the messages of the last connection."""

    async def scenario():
        handler = server.make_handler(client=CLIENT, whisper=WHISPER)
        async with serve(handler, "localhost", 0) as srv:
            port = srv.sockets[0].getsockname()[1]
            for _ in range(connections):
                async with connect(f"ws://localhost:{port}") as websocket:
                    results = []
                    for count in turns:
                        await ask_once(websocket)
                        results.append(await receive(websocket, count))
            return results

    return asyncio.run(scenario())


LISTENING_MSG = {"type": protocol.LISTENING}
SPEECH_END_MSG = {"type": protocol.STOP, "reason": protocol.SPEECH_END}


def test_handler_answers_a_question(fake_backend):
    llm = fake_backend(["what time is it"], ["It is noon."])

    [messages] = talk([4])

    assert messages == [
        LISTENING_MSG,
        SPEECH_END_MSG,
        {"type": protocol.TRANSCRIPT, "text": "what time is it"},
        {"type": protocol.REPLY, "text": "It is noon."},
    ]
    # Off the event loop's thread, or pings go unanswered while Gemini is slow.
    assert llm.threads[0] is not threading.main_thread()


def test_the_recorded_speech_reaches_whisper(monkeypatch):
    heard = []

    def record(audio, history, client, whisper):
        heard.append(audio)
        return "hello", "Hi!"

    monkeypatch.setattr(server, "transcribe_and_ask", record)

    talk([4])

    # Every frame of the question, in order: the loud start first, the silence last.
    # The fakes above ignore the audio, so without this a server that sent Whisper
    # nothing (or the wrong recording) would still pass.
    [audio] = heard
    assert audio.shape == ((START_FRAMES + END_FRAMES) * FRAME_SAMPLES, 1)
    assert audio[0, 0] == 3000
    assert audio[-1, 0] == 0


def test_handler_reports_empty_transcript(fake_backend):
    fake_backend([""], [])

    [messages] = talk([3])

    assert messages[2] == {"type": protocol.ERROR, "code": protocol.EMPTY_TRANSCRIPT}


def test_handler_survives_llm_failure_and_remembers(fake_backend):
    llm = fake_backend(["hello", "try again"], [RuntimeError("quota"), "Hi!"])

    first, second = talk([3, 4])

    assert first[2] == {"type": protocol.ERROR, "code": protocol.SERVER_ERROR}
    assert second[3] == {"type": protocol.REPLY, "text": "Hi!"}
    # The failed question is still in history when the retry is asked.
    assert [turn["content"] for turn in llm.seen[1]] == ["hello", "try again"]


def test_each_connection_has_its_own_history(fake_backend):
    llm = fake_backend(["one", "two"], ["A", "B"])

    talk([4], connections=2)  # one server, so a history shared by the server shows up

    assert llm.seen[1] == [{"role": "user", "content": "two"}]


# --- while THINKING: the server keeps reading (Stage 4C) ---


class SlowBackend:
    """Stands in for transcribe_and_ask and holds every answer until release().

    That keeps the server in THINKING for as long as a test needs, without sleeps.
    """

    def __init__(self):
        self.gate = threading.Event()
        self.calls = 0

    def __call__(self, audio, history, client, whisper):
        self.calls += 1
        # The timeout ends a forgotten release() as a failure instead of a hung run.
        assert self.gate.wait(5), "release() was never called"
        return f"question {self.calls}", f"answer {self.calls}"

    def release(self):
        self.gate.set()


@pytest.fixture
def slow_backend(monkeypatch):
    backend = SlowBackend()
    monkeypatch.setattr(server, "transcribe_and_ask", backend)
    yield backend
    backend.release()  # never leave a worker thread waiting after a failed test


def run_against_server(scenario):
    async def main():
        handler = server.make_handler(client=CLIENT, whisper=WHISPER)
        async with serve(handler, "localhost", 0) as srv:
            port = srv.sockets[0].getsockname()[1]
            return await scenario(f"ws://localhost:{port}")

    return asyncio.run(main())


BUSY_MSG = {"type": protocol.ERROR, "code": protocol.BUSY}


def answer_msgs(n):
    return [
        {"type": protocol.TRANSCRIPT, "text": f"question {n}"},
        {"type": protocol.REPLY, "text": f"answer {n}"},
    ]


def test_start_while_thinking_gets_busy_before_the_answer(slow_backend):
    async def scenario(url):
        async with connect(url) as websocket:
            await ask_once(websocket)
            assert await receive(websocket, 2) == [LISTENING_MSG, SPEECH_END_MSG]

            await websocket.send(START)
            # busy must come while the answer is still held back: a server that stops
            # reading while it thinks would only see this start after the reply.
            busy = await receive(websocket, 1)

            slow_backend.release()
            return busy, await receive(websocket, 2)

    busy, answer = run_against_server(scenario)

    assert busy == [BUSY_MSG]
    assert answer == answer_msgs(1)


def test_speech_while_thinking_does_not_become_a_question(slow_backend):
    async def scenario(url):
        async with connect(url) as websocket:
            await ask_once(websocket)
            await receive(websocket, 2)

            await ask_once(websocket)  # pressed again and spoken over the thinking
            assert await receive(websocket, 1) == [BUSY_MSG]

            slow_backend.release()
            first = await receive(websocket, 2)

            # The next question starts clean: no stale stop, no second answer queued.
            await ask_once(websocket)
            return first, await receive(websocket, 4)

    first, second = run_against_server(scenario)

    assert first == answer_msgs(1)
    assert second == [LISTENING_MSG, SPEECH_END_MSG, *answer_msgs(2)]
    assert slow_backend.calls == 2  # the frames sent while thinking were never transcribed


def test_disconnect_while_thinking_sends_nothing_later(slow_backend, monkeypatch):
    sent = []
    real_send_all = server.send_all

    async def spy_send_all(websocket, messages):
        sent.extend(message["type"] for message in messages)
        await real_send_all(websocket, messages)

    monkeypatch.setattr(server, "send_all", spy_send_all)

    async def scenario(url):
        async with connect(url) as websocket:
            await ask_once(websocket)
            await receive(websocket, 2)
        # Closed while thinking. Let the answer finish, then give the loop time to act.
        slow_backend.release()
        await asyncio.sleep(0.2)

    run_against_server(scenario)

    # The answer task was cancelled with the connection: nobody is left to tell.
    assert protocol.TRANSCRIPT not in sent
    assert protocol.REPLY not in sent


def test_a_bug_inside_the_answer_is_reported(slow_backend, monkeypatch, capsys):
    def broken_finish(self, transcript, reply):
        raise RuntimeError("bug in finish")

    monkeypatch.setattr(server.Session, "finish", broken_finish)

    async def scenario(url):
        async with connect(url) as websocket:
            await ask_once(websocket)
            await receive(websocket, 2)
            slow_backend.release()
            await asyncio.sleep(0.2)

    run_against_server(scenario)

    # A server bug must leave a traceback, not vanish inside a task nobody awaits.
    assert "RuntimeError: bug in finish" in capsys.readouterr().err
