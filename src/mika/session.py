import numpy as np

from mika import protocol
from mika.vad import DONE, NO_SPEECH, SpeechDetector

IDLE = "idle"
LISTENING = "listening"
THINKING = "thinking"


def stop_message(reason):
    return {"type": protocol.STOP, "reason": reason}


def error_message(code):
    return {"type": protocol.ERROR, "code": code}


class Session:
    """Protocol state for one connected satellite.

    It never touches a socket: messages go in, messages to send come out. That keeps
    it testable without a network, and only server.py changes if the transport does.
    """

    def __init__(self):
        self.state = IDLE
        self.detector = None
        self.utterance = None

    # Every handler returns a list of messages to send (often empty), so the server
    # can always call send_all without asking how many came back.
    def handle_text(self, text):
        try:
            message_type = protocol.parse_message(text)
        except ValueError:
            return [error_message(protocol.BAD_MESSAGE)]

        if message_type != protocol.START:
            return [error_message(protocol.BAD_MESSAGE)]

        if self.state != IDLE:
            return [error_message(protocol.BUSY)]

        # A fresh detector per press, or frames from the last question leak into this one.
        self.detector = SpeechDetector()
        self.state = LISTENING
        return [{"type": protocol.LISTENING}]

    def handle_audio(self, data):
        # Frames still in flight after STOP are normal, so they are dropped undecoded.
        if self.state != LISTENING:
            return []

        # Only decode_frame sits in the try: a ValueError from anywhere else is a
        # server bug and must not be reported to the satellite as bad_frame.
        try:
            frame = protocol.decode_frame(data)
        except ValueError:
            self._reset()
            return [error_message(protocol.BAD_FRAME)]

        result = self.detector.process(frame)
        if result == DONE:
            self.utterance = np.concatenate(self.detector.frames, axis=0)
            self.state = THINKING
            return [stop_message(protocol.SPEECH_END)]
        if result == NO_SPEECH:
            self._reset()
            return [stop_message(protocol.NO_SPEECH)]
        return []

    def take_utterance(self):
        # The state check stays even though _reset clears the utterance: it names the
        # real mistake, and it still guards if some path ever forgets to reset.
        if self.state != THINKING:
            raise RuntimeError("cannot take the utterance outside THINKING")
        if self.utterance is None:
            raise RuntimeError("the utterance was already taken")
        utterance = self.utterance
        self.utterance = None  # None means taken, so a second call raises
        return utterance

    def finish(self, transcript, reply):
        if self.state != THINKING:
            raise RuntimeError("cannot finish outside THINKING")
        self._reset()
        return [
            {"type": protocol.TRANSCRIPT, "text": transcript},
            {"type": protocol.REPLY, "text": reply},
        ]

    def fail(self, code):
        if self.state != THINKING:
            raise RuntimeError("cannot fail outside THINKING")
        self._reset()
        return [error_message(code)]

    # The one place that means "back to the start", so no path forgets a field.
    def _reset(self):
        self.state = IDLE
        self.detector = None
        self.utterance = None
