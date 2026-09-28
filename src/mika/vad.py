import numpy as np

SILENCE_DB = -100.0  # Returned for a frame with zero energy (no sound)
FULL_SCALE = 32768.0  # Maximum amplitude for 16-bit audio

# Threshold from the Stage 3A meter: silence -100 to -80 dB, speech -30 to -15 dB.
SPEECH_THRESHOLD_DB = -40.0
START_FRAMES = 10  # 200 ms of sound in a row -> speech started
END_FRAMES = 25  # 500 ms of silence in a row -> speech ended
MAX_SPEECH_FRAMES = 500  # 10 s cap on the whole recording, pre-roll included
MAX_WAIT_FRAMES = 150  # 3 s to start talking

WAITING = "waiting"
SPEAKING = "speaking"

CONTINUE = "continue"
DONE = "done"
NO_SPEECH = "no_speech"  # nobody spoke: skip transcription


def frame_db(frame):
    cvt_frame = frame.astype(np.float64)  # squaring int16 would overflow
    rms = np.sqrt(np.mean(cvt_frame**2))
    if rms == 0:
        return SILENCE_DB
    return 20 * np.log10(rms / FULL_SCALE)


class SpeechDetector:
    """Decides frame by frame when speech starts and ends.

    Frames come from the caller, so the same logic works for a laptop mic,
    an ESP32 over the network, or a test.
    """

    def __init__(self):
        self.state = WAITING
        self.frames = []
        self.waited = 0
        self.count_loud_frames = 0
        self.count_silent_frames = 0

    def process(self, frame):
        is_loud = frame_db(frame) > SPEECH_THRESHOLD_DB

        if self.state == WAITING:
            return self._process_waiting(is_loud, frame)
        return self._process_speaking(is_loud, frame)

    def _process_waiting(self, is_loud, frame):
        self.waited += 1

        # Pre-roll: keep the frames used to detect the start, or the first 200 ms is lost.
        if is_loud:
            self.frames.append(frame)
            self.count_loud_frames += 1
        else:
            self.count_loud_frames = 0
            self.frames = []

        # After the counter update, and only with no loud run in progress, so speech
        # that starts right at the deadline is not cut off.
        if self.waited >= MAX_WAIT_FRAMES and self.count_loud_frames == 0:
            return NO_SPEECH

        if self.count_loud_frames >= START_FRAMES:
            self.state = SPEAKING

        return CONTINUE

    def _process_speaking(self, is_loud, frame):
        # Keep every frame: pauses and quiet word endings are part of the speech,
        # and Whisper's vad_filter skips real silence anyway.
        self.frames.append(frame)

        if is_loud:
            self.count_silent_frames = 0
        else:
            self.count_silent_frames += 1

        # len(self.frames) includes the pre-roll, which counts toward the cap.
        if self.count_silent_frames >= END_FRAMES or len(self.frames) >= MAX_SPEECH_FRAMES:
            return DONE

        return CONTINUE
