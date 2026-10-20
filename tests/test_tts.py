import asyncio
import shutil
import subprocess
import threading

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


# --- synthesize: fetch, then decode ---


@needs_ffmpeg
def test_synthesize_fetches_then_decodes():
    mp3 = make_mp3(seconds=1.0)
    # Split as the network would deliver it.
    voice = fake_voice([audio(mp3[:1000]), boundary(), audio(mp3[1000:])])

    out = asyncio.run(tts.synthesize(voice, "Hello there."))

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
        await tts.synthesize(fake_voice([audio(b"x")]), "Hi.")
        return loop_thread

    loop_thread = asyncio.run(run())

    # ffmpeg blocks; on the loop's own thread it would stall every connection on the
    # server, the same reason Whisper and Gemini run in asyncio.to_thread.
    assert threads and threads[0] is not loop_thread


# --- setup checks ---


def test_missing_ffmpeg_is_reported_clearly(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda name: None)

    with pytest.raises(FileNotFoundError, match="ffmpeg"):
        tts.load_voice()


def test_importing_mika_tts_does_not_need_edge_tts():
    # edge-tts is an optional extra: mika.tts must import without it, which is why
    # the import sits inside load_voice. A top-level import would break that.
    with open(tts.__file__, encoding="utf-8") as f:
        top_level = [line for line in f if line.startswith(("import ", "from "))]

    assert not any("edge_tts" in line for line in top_level)
