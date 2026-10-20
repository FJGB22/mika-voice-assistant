import asyncio
import json
import threading
import time

import numpy as np
import pytest
from websockets.asyncio.client import connect
from websockets.asyncio.server import serve

from mika import protocol, server
from mika.vad import END_FRAMES, FRAME_SAMPLES, START_FRAMES

LOUD = np.full(FRAME_SAMPLES, 3000, dtype="<i2").tobytes()
QUIET = bytes(protocol.FRAME_BYTES)
START = json.dumps({"type": protocol.START})


# Stand-ins for the real Whisper model, Gemini client and Edge voice. The fakes below
# check that they arrive in the right argument, so a swapped call fails here, not on
# the device.
WHISPER = object()
CLIENT = object()
VOICE = object()

# What the fake TTS says for every reply: two frames with recognisable values.
SPEECH = np.concatenate(
    [
        np.full((FRAME_SAMPLES, 1), 7, dtype=np.int16),
        np.full((FRAME_SAMPLES, 1), -7, dtype=np.int16),
    ]
)
SPEECH_FRAMES = protocol.split_frames(SPEECH)
DONE_MSG = {"type": protocol.DONE}


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


class FakeTTS:
    """Stands in for tts.synthesize: records what it was asked to say."""

    def __init__(self, speech=SPEECH, error=None):
        self.speech = speech
        self.error = error
        self.texts = []

    async def __call__(self, voice, text):
        assert voice is VOICE, "synthesize(voice, text): arguments in the wrong order?"
        self.texts.append(text)
        if self.error is not None:
            raise self.error
        return self.speech


@pytest.fixture
def fake_tts(monkeypatch):
    def install(**kwargs):
        tts = FakeTTS(**kwargs)
        monkeypatch.setattr(server, "synthesize", tts)
        return tts

    install()  # every server test speaks; a test that cares installs its own
    return install


@pytest.fixture
def fake_backend(monkeypatch, fake_tts):
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
    # Text is JSON; binary messages (Mika's speech) are returned as the raw bytes.
    received = []
    for _ in range(count):
        message = await asyncio.wait_for(websocket.recv(), 5)
        received.append(json.loads(message) if isinstance(message, str) else message)
    return received


async def receive_until_done(websocket):
    received = []
    while not received or received[-1] != DONE_MSG:
        received += await receive(websocket, 1)
    return received


def talk(turns, connections=1):
    """Starts one server and connects to it `connections` times in a row, asking
    len(turns) questions each time. Returns the messages of the last connection."""

    async def scenario():
        handler = server.make_handler(client=CLIENT, whisper=WHISPER, voice=VOICE)
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

    [messages] = talk([7])

    assert messages == [
        LISTENING_MSG,
        SPEECH_END_MSG,
        {"type": protocol.TRANSCRIPT, "text": "what time is it"},
        {"type": protocol.REPLY, "text": "It is noon."},
        *SPEECH_FRAMES,
        DONE_MSG,
    ]
    # Off the event loop's thread, or pings go unanswered while Gemini is slow.
    assert llm.threads[0] is not threading.main_thread()


def test_the_recorded_speech_reaches_whisper(monkeypatch):
    heard = []

    def record(audio, history, client, whisper):
        heard.append(audio)
        return "hello", "Hi!"

    monkeypatch.setattr(server, "transcribe_and_ask", record)
    monkeypatch.setattr(server, "synthesize", FakeTTS())

    talk([7])

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

    first, second = talk([3, 7])

    assert first[2] == {"type": protocol.ERROR, "code": protocol.SERVER_ERROR}
    assert second[3] == {"type": protocol.REPLY, "text": "Hi!"}
    # The failed question is still in history when the retry is asked.
    assert [turn["content"] for turn in llm.seen[1]] == ["hello", "try again"]


def test_each_connection_has_its_own_history(fake_backend):
    llm = fake_backend(["one", "two"], ["A", "B"])

    talk([7], connections=2)  # one server, so a history shared by the server shows up

    assert llm.seen[1] == [{"role": "user", "content": "two"}]


# --- speaking the reply (Stage 5B) ---


def test_the_reply_text_is_what_gets_spoken(fake_backend, fake_tts):
    fake_backend(["what time is it"], ["It is noon."])
    tts = fake_tts()

    talk([7])

    assert tts.texts == ["It is noon."]


def test_speech_of_any_length_arrives_whole(fake_backend, fake_tts):
    # 2.5 frames: split_frames pads the last one, and every sample must arrive in order.
    speech = np.arange(FRAME_SAMPLES * 5 // 2, dtype=np.int16).reshape(-1, 1)
    fake_backend(["hi"], ["Hello."])
    fake_tts(speech=speech)

    [messages] = talk([8])

    frames = messages[4:7]
    assert frames == protocol.split_frames(speech)
    assert messages[7] == DONE_MSG


def test_tts_failure_ends_the_turn_after_the_reply(fake_backend, fake_tts, capsys):
    fake_backend(["hello", "again"], ["Hi!", "Hi again!"])
    fake_tts(error=RuntimeError("Edge is down"))

    first, second = talk([5, 5])

    # The text answer still arrives; then an error instead of speech, and no done.
    assert first[3] == {"type": protocol.REPLY, "text": "Hi!"}
    assert first[4] == {"type": protocol.ERROR, "code": protocol.TTS_ERROR}
    # The turn is really over: the next question is accepted, not busy.
    assert second[:2] == [LISTENING_MSG, SPEECH_END_MSG]
    # Handled, not crashed: a crash inside the answer task prints a traceback.
    assert "Traceback" not in capsys.readouterr().err


def long_speech(frames):
    return np.zeros((frames * FRAME_SAMPLES, 1), dtype=np.int16)


def test_start_while_speaking_gets_busy_before_done(fake_backend, fake_tts):
    fake_backend(["hello"], ["Hi!"])
    # 25 paced frames after the prebuffer: half a second of speaking to press into.
    fake_tts(speech=long_speech(server.PREBUFFER_FRAMES + 25))

    async def scenario(url):
        async with connect(url) as websocket:
            await ask_once(websocket)
            await receive(websocket, 5)  # listening, stop, transcript, reply, 1st frame
            await websocket.send(START)
            return await receive_until_done(websocket)

    messages = run_against_server(scenario)

    # busy arrives while frames are still coming, and the speech is not cut short.
    busy_at = messages.index({"type": protocol.ERROR, "code": protocol.BUSY})
    frames = [m for m in messages if isinstance(m, bytes)]
    assert busy_at < len(messages) - 2
    assert len(frames) == server.PREBUFFER_FRAMES + 25 - 1


def test_disconnect_while_speaking_is_not_a_crash(fake_backend, fake_tts, capsys):
    fake_backend(["hello"], ["Hi!"])
    fake_tts(speech=long_speech(server.PREBUFFER_FRAMES + 25))

    async def scenario(url):
        async with connect(url) as websocket:
            await ask_once(websocket)
            await receive(websocket, 5)
        # Closed mid-speech, like a satellite that loses power.
        await asyncio.sleep(0.3)

    run_against_server(scenario)

    assert "Traceback" not in capsys.readouterr().err


# --- frame_schedule and send_speech: pacing the speech (Stage 5B) ---


def test_schedule_sends_the_prebuffer_at_once():
    schedule = server.frame_schedule(server.PREBUFFER_FRAMES + 5)

    assert schedule[: server.PREBUFFER_FRAMES] == [0] * server.PREBUFFER_FRAMES


def test_schedule_then_keeps_the_playing_pace():
    schedule = server.frame_schedule(server.PREBUFFER_FRAMES + 5)

    after = schedule[server.PREBUFFER_FRAMES - 1 :]
    steps = [b - a for a, b in zip(after, after[1:], strict=False)]
    # One frame per 20 ms: faster would overflow the satellite's buffer, slower
    # would leave gaps in Mika's voice.
    assert steps == pytest.approx([server.FRAME_SECONDS] * 5)


def test_schedule_has_one_time_per_frame():
    assert len(server.frame_schedule(37)) == 37
    assert server.frame_schedule(0) == []


def test_schedule_sends_short_speech_at_once():
    assert server.frame_schedule(3) == [0, 0, 0]


class FakeWebSocket:
    """Records when each message is sent, on the event loop's clock."""

    def __init__(self):
        self.sent = []

    async def send(self, message):
        self.sent.append((asyncio.get_running_loop().time(), message))


def test_send_speech_sends_every_frame_in_order():
    frames = [bytes([i]) * protocol.FRAME_BYTES for i in range(server.PREBUFFER_FRAMES + 3)]
    websocket = FakeWebSocket()

    asyncio.run(server.send_speech(websocket, frames))

    assert [message for _, message in websocket.sent] == frames


def test_send_speech_follows_the_schedule():
    count = server.PREBUFFER_FRAMES + 10
    frames = [bytes(protocol.FRAME_BYTES)] * count
    websocket = FakeWebSocket()

    async def run():
        start = asyncio.get_running_loop().time()
        await server.send_speech(websocket, frames)
        return start

    start = asyncio.run(run())

    schedule = server.frame_schedule(count)
    for (sent_at, _), offset in zip(websocket.sent, schedule, strict=True):
        # Never early (the satellite's buffer is small); a timer may wake a little
        # early on Windows, hence 20 ms of slack. Never far behind either.
        assert sent_at - start >= offset - 0.02
        assert sent_at - start <= offset + 0.1


def test_send_speech_does_not_drift(monkeypatch):
    real_sleep = asyncio.sleep

    async def late_sleep(seconds):
        # Every sleep overruns by 5 ms, as timers on a busy laptop do.
        await real_sleep(seconds + 0.005)

    monkeypatch.setattr(asyncio, "sleep", late_sleep)
    count = server.PREBUFFER_FRAMES + 50
    websocket = FakeWebSocket()

    started = time.perf_counter()
    asyncio.run(server.send_speech(websocket, [bytes(protocol.FRAME_BYTES)] * count))
    took = time.perf_counter() - started

    # Timed from the start, one overrun is all that shows. Sleeping a fixed 20 ms per
    # frame would add all 50 up: a quarter of a second late, and growing with the reply.
    expected = server.frame_schedule(count)[-1]
    assert expected - 0.02 <= took <= expected + 0.1


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
def slow_backend(monkeypatch, fake_tts):
    backend = SlowBackend()
    monkeypatch.setattr(server, "transcribe_and_ask", backend)
    yield backend
    backend.release()  # never leave a worker thread waiting after a failed test


def run_against_server(scenario):
    async def main():
        handler = server.make_handler(client=CLIENT, whisper=WHISPER, voice=VOICE)
        async with serve(handler, "localhost", 0) as srv:
            port = srv.sockets[0].getsockname()[1]
            return await scenario(f"ws://localhost:{port}")

    return asyncio.run(main())


BUSY_MSG = {"type": protocol.ERROR, "code": protocol.BUSY}


def answer_msgs(n):
    return [
        {"type": protocol.TRANSCRIPT, "text": f"question {n}"},
        {"type": protocol.REPLY, "text": f"answer {n}"},
        *SPEECH_FRAMES,
        DONE_MSG,
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
            return busy, await receive(websocket, 5)

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
            first = await receive(websocket, 5)

            # The next question starts clean: no stale stop, no second answer queued.
            await ask_once(websocket)
            return first, await receive(websocket, 7)

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
    assert protocol.DONE not in sent


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
