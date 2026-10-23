import asyncio
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass

import numpy as np
import pytest

from mika import tts
from mika.vad import SAMPLE_RATE

EDGE_RATE = 24000  # Edge always sends audio-24khz-48kbitrate-mono-mp3
# MP3 adds a few dozen milliseconds of silence (encoder delay and padding), so lengths
# are checked within 0.1 s. Mistakes this catches are far bigger: 1.5x or 2x.
MARGIN = SAMPLE_RATE // 10

needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not on PATH")


def make_mp3(seconds=1.0, rate=EDGE_RATE, channels=1, tone=440):
    """A real MP3 like the ones Edge sends: a sine tone, made by ffmpeg itself."""
    result = subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency={tone}:sample_rate={rate}:duration={seconds}",
            "-ac",
            str(channels),
            "-f",
            "mp3",
            "pipe:1",
        ],
        capture_output=True,
        check=True,
    )
    return result.stdout


# --- fetch_mp3: a fake stands in for edge_tts.Communicate, so no network is needed ---


class FakeCommunicate:
    """Same shape as edge_tts.Communicate: built with the text and voice settings,
    then stream() yields dicts, audio and boundary information mixed."""

    created = []

    def __init__(self, text, voice, *, rate, pitch, chunks):
        FakeCommunicate.created.append({"text": text, "voice": voice, "rate": rate, "pitch": pitch})
        self.chunks = chunks

    async def stream(self):
        for chunk in self.chunks:
            yield chunk


def fake_voice(chunks):
    """What load_voice() returns, but yielding the given chunks."""
    FakeCommunicate.created = []

    def voice(text, voice_name, *, rate, pitch):
        return FakeCommunicate(text, voice_name, rate=rate, pitch=pitch, chunks=chunks)

    return voice


def audio(data):
    return {"type": "audio", "data": data}


def boundary():
    # Edge interleaves these with the audio; they carry text timing, not sound.
    return {"type": "SentenceBoundary", "offset": 0, "duration": 0, "text": "Hi."}


def test_fetch_asks_for_mikas_voice():
    voice = fake_voice([audio(b"x")])

    asyncio.run(tts.fetch_mp3(voice, "Hello there."))

    assert FakeCommunicate.created == [
        {"text": "Hello there.", "voice": tts.VOICE, "rate": tts.RATE, "pitch": tts.PITCH}
    ]


def test_audio_chunks_are_joined_in_order():
    voice = fake_voice([audio(b"ab"), audio(b"c"), audio(b"def")])

    assert asyncio.run(tts.fetch_mp3(voice, "Hi.")) == b"abcdef"


def test_boundary_chunks_are_not_audio():
    # Joining a boundary dict's data would corrupt the MP3, or crash on a missing key.
    voice = fake_voice([boundary(), audio(b"ab"), boundary(), audio(b"cd")])

    assert asyncio.run(tts.fetch_mp3(voice, "Hi.")) == b"abcd"


def test_no_audio_gives_empty_bytes():
    voice = fake_voice([boundary()])

    assert asyncio.run(tts.fetch_mp3(voice, "Hi.")) == b""


# --- decode_mp3: real ffmpeg on real MP3 bytes ---


def test_nothing_to_decode_needs_no_ffmpeg(monkeypatch):
    def no_ffmpeg(*args, **kwargs):
        raise AssertionError("ffmpeg should not run for empty audio")

    monkeypatch.setattr(subprocess, "run", no_ffmpeg)

    out = tts.decode_mp3(b"")

    assert out.shape == (0, 1)
    assert out.dtype == np.int16


@needs_ffmpeg
def test_decoded_audio_has_the_microphone_shape():
    out = tts.decode_mp3(make_mp3())

    # (samples, 1) int16, exactly like a recording: protocol and playback need no special case.
    assert out.ndim == 2
    assert out.shape[1] == 1
    assert out.dtype == np.int16


@needs_ffmpeg
def test_one_second_stays_one_second():
    out = tts.decode_mp3(make_mp3(seconds=1.0))

    # 24000 samples per second in, 16000 out. Left at 24 kHz, Mika would play 1.5x
    # too slow and too deep.
    assert abs(len(out) - SAMPLE_RATE) <= MARGIN


@needs_ffmpeg
def test_stereo_is_mixed_down_to_mono():
    out = tts.decode_mp3(make_mp3(seconds=1.0, channels=2))

    # Two channels read as one would interleave left and right: twice the samples.
    assert abs(len(out) - SAMPLE_RATE) <= MARGIN


@needs_ffmpeg
def test_a_tone_keeps_its_pitch():
    out = tts.decode_mp3(make_mp3(seconds=1.0, tone=440))[:, 0].astype(np.float64)

    spectrum = np.abs(np.fft.rfft(out))
    peak_hz = np.argmax(spectrum) * SAMPLE_RATE / len(out)

    # Wrong sample rate moves the peak (to 293 or 660 Hz); the wrong byte order turns
    # the tone into noise with no clear peak at all.
    assert abs(peak_hz - 440) <= 5


@needs_ffmpeg
def test_a_tone_is_not_silence():
    out = tts.decode_mp3(make_mp3(seconds=1.0))[:, 0].astype(np.float64)

    # ffmpeg's sine is at 1/8 of full scale: RMS about 2900. Silence or a wrong
    # conversion (e.g. float read as int) would be far off.
    rms = np.sqrt(np.mean(out**2))
    assert 2000 < rms < 4000


@needs_ffmpeg
def test_garbage_is_rejected_with_ffmpegs_reason():
    with pytest.raises(ValueError, match="ffmpeg"):
        tts.decode_mp3(b"this is not an mp3 file at all" * 10)


# --- synthesize_edge: fetch, then decode ---


@needs_ffmpeg
def test_synthesize_fetches_then_decodes():
    mp3 = make_mp3(seconds=1.0)
    # Split as the network would deliver it.
    voice = fake_voice([audio(mp3[:1000]), boundary(), audio(mp3[1000:])])

    out = asyncio.run(tts.synthesize_edge(voice, "Hello there."))

    assert FakeCommunicate.created[0]["text"] == "Hello there."
    assert out.shape[1] == 1
    assert abs(len(out) - SAMPLE_RATE) <= MARGIN


def test_decoding_runs_outside_the_event_loop(monkeypatch):
    threads = []

    def fake_decode(mp3):
        threads.append(threading.current_thread())
        return np.zeros((0, 1), dtype=np.int16)

    monkeypatch.setattr(tts, "decode_mp3", fake_decode)

    async def run():
        loop_thread = threading.current_thread()
        await tts.synthesize_edge(fake_voice([audio(b"x")]), "Hi.")
        return loop_thread

    loop_thread = asyncio.run(run())

    # ffmpeg blocks; on the loop's own thread it would stall every connection on the
    # server, the same reason Whisper and Gemini run in asyncio.to_thread.
    assert threads and threads[0] is not loop_thread


# --- synthesize_piper: a fake voice stands in for Piper ---


@dataclass
class FakeChunk:
    """The fields of piper.AudioChunk that Mika reads."""

    sample_rate: int
    sample_width: int
    sample_channels: int
    audio_int16_array: np.ndarray


def piper_chunk(values, rate=SAMPLE_RATE, width=2, channels=1):
    return FakeChunk(rate, width, channels, np.array(values, dtype=np.int16))


class FakePiper:
    """Stands in for a loaded PiperVoice: one chunk per sentence, as Piper does."""

    def __init__(self, chunks):
        self.chunks = chunks
        self.texts = []
        self.threads = []

    def synthesize(self, text):
        self.texts.append(text)
        self.threads.append(threading.current_thread())
        yield from self.chunks


def test_piper_gets_the_text():
    piper = FakePiper([piper_chunk([1])])

    tts.synthesize_piper(piper, "Hello there.")

    assert piper.texts == ["Hello there."]


def test_piper_sentences_are_joined_in_order():
    piper = FakePiper([piper_chunk([1, 2]), piper_chunk([3]), piper_chunk([4, 5, 6])])

    out = tts.synthesize_piper(piper, "One. Two. Three.")

    assert out[:, 0].tolist() == [1, 2, 3, 4, 5, 6]


def test_piper_audio_has_the_microphone_shape():
    out = tts.synthesize_piper(FakePiper([piper_chunk([1, 2, 3])]), "Hi.")

    assert out.shape == (3, 1)
    assert out.dtype == np.int16


def test_piper_with_nothing_to_say_gives_empty_audio():
    out = tts.synthesize_piper(FakePiper([]), "")

    assert out.shape == (0, 1)
    assert out.dtype == np.int16


@pytest.mark.parametrize(
    "bad",
    [
        piper_chunk([1, 2], rate=22050),
        piper_chunk([1, 2], channels=2),
        piper_chunk([1, 2], width=1),
    ],
    ids=["22-khz", "stereo", "8-bit"],
)
def test_piper_audio_it_cannot_send_is_rejected(bad):
    # A medium voice is 22 kHz: played as 16 kHz it sounds slow and deep, far from the
    # cause. Failing here names the problem.
    with pytest.raises(ValueError):
        tts.synthesize_piper(FakePiper([bad]), "Hi.")


# --- synthesize: Edge within the deadline, otherwise Piper ---

EDGE_AUDIO = np.full((5, 1), 1, dtype=np.int16)
PIPER_VALUES = [2, 2, 2]


def voices(piper=None):
    return tts.Voices(edge=object(), piper=piper or FakePiper([piper_chunk(PIPER_VALUES)]))


def fake_edge(delay=0.0, error=None, cancelled=None):
    async def synthesize_edge(edge, text):
        try:
            await asyncio.sleep(delay)
        except asyncio.CancelledError:
            if cancelled is not None:
                cancelled.append(True)
            raise
        if error is not None:
            raise error
        return EDGE_AUDIO

    return synthesize_edge


def test_fallback_not_used_when_edge_is_in_time(monkeypatch):
    piper = FakePiper([piper_chunk(PIPER_VALUES)])
    monkeypatch.setattr(tts, "synthesize_edge", fake_edge())

    out = asyncio.run(tts.synthesize(voices(piper), "Hi."))

    assert np.array_equal(out, EDGE_AUDIO)
    assert piper.texts == []  # Piper is only the fallback


def test_fallback_to_piper_at_the_deadline(monkeypatch):
    monkeypatch.setattr(tts, "EDGE_DEADLINE", 0.05)
    monkeypatch.setattr(tts, "synthesize_edge", fake_edge(delay=5))

    started = time.perf_counter()
    out = asyncio.run(tts.synthesize(voices(), "Hi."))
    took = time.perf_counter() - started

    assert out[:, 0].tolist() == PIPER_VALUES
    # Piper answers at the deadline, not after Edge's 5 s: that is the whole point.
    assert took < 1.0


def test_fallback_at_the_deadline_is_logged(monkeypatch, capsys):
    monkeypatch.setattr(tts, "EDGE_DEADLINE", 0.05)
    monkeypatch.setattr(tts, "synthesize_edge", fake_edge(delay=5))

    asyncio.run(tts.synthesize(voices(), "Hi."))

    # The slow case is the common one (15-36 s in the 5C logs), so it must show up too.
    assert "Edge failed" in capsys.readouterr().out


def test_a_cancelled_answer_does_not_fall_back_to_piper(monkeypatch):
    piper = FakePiper([piper_chunk(PIPER_VALUES)])
    monkeypatch.setattr(tts, "synthesize_edge", fake_edge(delay=5))

    async def run():
        task = asyncio.create_task(tts.synthesize(voices(piper), "Hi."))
        await asyncio.sleep(0.05)
        task.cancel()  # what the server does when the satellite disconnects
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(run())

    # A cancel is not Edge failing: caught as one, Piper would speak to nobody and the
    # task would never learn it was cancelled.
    assert piper.texts == []


def test_fallback_cancels_the_slow_edge(monkeypatch):
    cancelled = []
    monkeypatch.setattr(tts, "EDGE_DEADLINE", 0.05)
    monkeypatch.setattr(tts, "synthesize_edge", fake_edge(delay=5, cancelled=cancelled))

    asyncio.run(tts.synthesize(voices(), "Hi."))

    # A stalled Edge request left running would hold its connection open for nothing.
    assert cancelled == [True]


def test_fallback_when_edge_fails(monkeypatch, capsys):
    monkeypatch.setattr(tts, "synthesize_edge", fake_edge(error=ConnectionError("no DNS")))

    out = asyncio.run(tts.synthesize(voices(), "Hi."))

    assert out[:, 0].tolist() == PIPER_VALUES
    # Logged, so a fallback is visible in the server's output, not silent.
    assert "Edge failed" in capsys.readouterr().out


def test_fallback_gives_piper_the_same_text(monkeypatch):
    piper = FakePiper([piper_chunk(PIPER_VALUES)])
    monkeypatch.setattr(tts, "synthesize_edge", fake_edge(error=ConnectionError()))

    asyncio.run(tts.synthesize(voices(piper), "It is noon."))

    assert piper.texts == ["It is noon."]


def test_fallback_runs_piper_outside_the_event_loop(monkeypatch):
    piper = FakePiper([piper_chunk(PIPER_VALUES)])
    monkeypatch.setattr(tts, "synthesize_edge", fake_edge(error=ConnectionError()))

    async def run():
        await tts.synthesize(voices(piper), "Hi.")
        return threading.current_thread()

    loop_thread = asyncio.run(run())

    # Piper works the CPU for a moment; on the loop's thread it would stall the server.
    assert piper.threads and piper.threads[0] is not loop_thread


def test_fallback_error_reaches_the_caller_when_both_fail(monkeypatch):
    monkeypatch.setattr(tts, "synthesize_edge", fake_edge(error=ConnectionError()))
    broken_piper = FakePiper([piper_chunk([1], rate=22050)])

    # The server turns this into tts_error; swallowing it would send silence instead.
    with pytest.raises(ValueError):
        asyncio.run(tts.synthesize(voices(broken_piper), "Hi."))


# --- setup checks ---


def test_missing_ffmpeg_is_reported_clearly(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda name: None)

    with pytest.raises(FileNotFoundError, match="ffmpeg"):
        tts.load_voice()


def test_missing_piper_voice_says_how_to_download_it(monkeypatch, tmp_path):
    monkeypatch.setattr(shutil, "which", lambda name: "ffmpeg")
    monkeypatch.setenv(tts.PIPER_VOICE_NAME, str(tmp_path / "missing.onnx"))

    with pytest.raises(FileNotFoundError, match="download_voices"):
        tts.load_voice()


def test_piper_voice_defaults_to_a_16_khz_voice(monkeypatch):
    monkeypatch.delenv(tts.PIPER_VOICE_NAME, raising=False)

    assert tts.piper_voice_path().endswith("en_US-lessac-low.onnx")


def test_piper_voice_can_be_overridden(monkeypatch):
    monkeypatch.setenv(tts.PIPER_VOICE_NAME, "C:/voices/other.onnx")

    assert tts.piper_voice_path() == "C:/voices/other.onnx"


def test_importing_mika_tts_needs_neither_engine():
    # Both are optional extras: mika.tts must import without them, which is why the
    # imports sit inside load_voice. A top-level import would break that.
    with open(tts.__file__, encoding="utf-8") as f:
        top_level = [line for line in f if line.startswith(("import ", "from "))]

    assert not any("edge_tts" in line or "piper" in line for line in top_level)
