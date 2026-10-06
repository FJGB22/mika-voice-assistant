import json

import numpy as np
import pytest

from mika import protocol
from mika.session import IDLE, LISTENING, THINKING, Session
from mika.vad import END_FRAMES, FRAME_SAMPLES, MAX_WAIT_FRAMES, START_FRAMES

LOUD = np.full(FRAME_SAMPLES, 3000, dtype="<i2").tobytes()  # about -21 dB, like speech
QUIET = bytes(protocol.FRAME_BYTES)
START = json.dumps({"type": protocol.START})

LISTENING_MSG = {"type": protocol.LISTENING}
SPEECH_END_MSG = {"type": protocol.STOP, "reason": protocol.SPEECH_END}
NO_SPEECH_MSG = {"type": protocol.STOP, "reason": protocol.NO_SPEECH}


def error(code):
    return {"type": protocol.ERROR, "code": code}


def feed(session, data, count):
    messages = []
    for _ in range(count):
        messages += session.handle_audio(data)
    return messages


def listening_session():
    session = Session()
    session.handle_text(START)
    return session


def thinking_session():
    session = listening_session()
    feed(session, LOUD, START_FRAMES)
    feed(session, QUIET, END_FRAMES)
    return session


def test_new_session_is_idle():
    assert Session().state == IDLE


def test_start_begins_listening():
    session = Session()

    messages = session.handle_text(START)

    assert messages == [LISTENING_MSG]
    assert session.state == LISTENING


def test_audio_while_idle_is_ignored():
    session = Session()

    messages = feed(session, LOUD, START_FRAMES + END_FRAMES)

    assert messages == []
    assert session.state == IDLE


def test_bad_frame_while_idle_is_ignored():
    # In-flight frames are never decoded while idle, so their size does not matter yet.
    session = Session()

    assert session.handle_audio(b"\x00") == []
    assert session.state == IDLE


def test_speech_then_silence_stops_and_starts_thinking():
    session = listening_session()

    messages = feed(session, LOUD, START_FRAMES)
    messages += feed(session, QUIET, END_FRAMES)

    assert messages == [SPEECH_END_MSG]
    assert session.state == THINKING


def test_stop_is_sent_exactly_once():
    session = listening_session()

    messages = feed(session, LOUD, START_FRAMES)
    messages += feed(session, QUIET, END_FRAMES + 30)  # frames still in flight after STOP

    assert messages == [SPEECH_END_MSG]


def test_utterance_holds_every_frame_in_order():
    session = thinking_session()

    audio = session.take_utterance()

    assert audio.shape == ((START_FRAMES + END_FRAMES) * FRAME_SAMPLES, 1)
    assert np.all(audio[: START_FRAMES * FRAME_SAMPLES] == 3000)
    assert np.all(audio[START_FRAMES * FRAME_SAMPLES :] == 0)


def test_utterance_can_be_taken_only_once():
    session = thinking_session()
    session.take_utterance()

    with pytest.raises(RuntimeError):
        session.take_utterance()


def test_take_utterance_while_listening_raises():
    session = listening_session()

    with pytest.raises(RuntimeError):
        session.take_utterance()


def test_nobody_speaks_sends_no_speech_and_goes_idle():
    session = listening_session()

    messages = feed(session, QUIET, MAX_WAIT_FRAMES)

    assert messages == [NO_SPEECH_MSG]
    assert session.state == IDLE


def test_bad_frame_while_listening_aborts():
    session = listening_session()
    feed(session, LOUD, 3)

    messages = session.handle_audio(LOUD + LOUD)  # two frames glued into one message

    assert messages == [error(protocol.BAD_FRAME)]
    assert session.state == IDLE


def test_start_while_listening_is_busy():
    session = listening_session()
    feed(session, LOUD, 3)

    messages = session.handle_text(START)

    assert messages == [error(protocol.BUSY)]
    assert session.state == LISTENING


def test_start_while_thinking_is_busy():
    session = thinking_session()

    messages = session.handle_text(START)

    assert messages == [error(protocol.BUSY)]
    assert session.state == THINKING


def test_audio_while_thinking_is_ignored():
    session = thinking_session()

    assert feed(session, LOUD, 5) == []
    assert session.state == THINKING


@pytest.mark.parametrize("text", ["not json", '{"type": "dance"}', "{}"])
def test_bad_control_message_is_reported_and_state_kept(text):
    session = listening_session()

    messages = session.handle_text(text)

    assert messages == [error(protocol.BAD_MESSAGE)]
    assert session.state == LISTENING


def test_finish_sends_transcript_then_reply_and_goes_idle():
    session = thinking_session()
    session.take_utterance()

    messages = session.finish("what time is it", "It is noon.")

    assert messages == [
        {"type": protocol.TRANSCRIPT, "text": "what time is it"},
        {"type": protocol.REPLY, "text": "It is noon."},
    ]
    assert session.state == IDLE


def test_fail_sends_error_and_goes_idle():
    session = thinking_session()
    session.take_utterance()

    messages = session.fail(protocol.SERVER_ERROR)

    assert messages == [error(protocol.SERVER_ERROR)]
    assert session.state == IDLE


@pytest.mark.parametrize("make", [Session, listening_session])
def test_finish_outside_thinking_raises(make):
    with pytest.raises(RuntimeError):
        make().finish("a", "b")


@pytest.mark.parametrize("make", [Session, listening_session])
def test_fail_outside_thinking_raises(make):
    with pytest.raises(RuntimeError):
        make().fail(protocol.SERVER_ERROR)


def test_second_question_does_not_contain_the_first():
    # Regression guard: a detector reused across presses would carry old frames along.
    session = thinking_session()
    session.take_utterance()
    session.finish("first", "one")

    session.handle_text(START)
    feed(session, LOUD, START_FRAMES)
    feed(session, QUIET, END_FRAMES)
    audio = session.take_utterance()

    assert audio.shape == ((START_FRAMES + END_FRAMES) * FRAME_SAMPLES, 1)


def test_can_start_again_after_no_speech():
    session = listening_session()
    feed(session, QUIET, MAX_WAIT_FRAMES)

    assert session.handle_text(START) == [LISTENING_MSG]
