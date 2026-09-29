# Mika

A small voice-assistant project, built one stage at a time. Right now it runs on
a laptop: you can type, or just speak. It records until you stop talking and
transcribes locally with faster-whisper. It reads its configuration from outside
the repository, streams answers from Gemini, and remembers the conversation.

Later stages add speech output and an ESP32 microphone client.

## Requirements

- Python 3.11 or newer
- A Gemini API key from [aistudio.google.com](https://aistudio.google.com)

Use a separate Google Cloud project for this key. Free-tier quota is counted
per project, not per key, so sharing a project with another app means sharing
the daily limit.

## Installation

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows
# source .venv/bin/activate     # macOS / Linux

pip install -e ".[dev,audio,whisper]"
```

Running `mika` needs both `audio` and `whisper`, because `app.py` imports the
microphone and transcription code at startup. The tests need `audio` too, for numpy.
faster-whisper is a large download, so the first install takes a few minutes.

Verify from outside the project folder:

```bash
cd ..
python -c "import mika; print(mika.__file__)"
```

It should print the path to `src/mika/__init__.py`. If it raises
`ModuleNotFoundError`, the install did not succeed — the `src/` layout is
deliberate, so a broken install fails loudly instead of silently working.

## Configuration

Secrets live **outside** the repository, at:

```
~/.secrets/mika-assistant.env
```

See `.env.example` for the variables the program expects. Currently:

| Variable | Required | Purpose |
|---|---|---|
| `LLM_API_KEY` | yes | Gemini API key |

The lookup order is **system environment first, file second**. A value already
present in the environment is never overwritten by the file. That way the same
code runs unchanged on a server, where the key is supplied as a real
environment variable rather than a file.

## Running

```bash
mika
```

Type your message and press Enter, or press Enter on an empty line and speak.
Recording stops by itself about half a second after you stop talking. To quit:
type `exit` or `quit`, or press Ctrl-C at any point — including while an answer
is still streaming.

If speech is never detected, your microphone may be quieter than the
-40 dB threshold in `vad.py`. Check its levels with:

```bash
python -c "from mika.audio import meter; meter()"
```

## Development

```bash
ruff check src tests        # lint
ruff format src tests       # format
pytest                      # tests
```

Keep the linter output at zero. A standing warning trains you to ignore the
output, and then the warnings that matter get lost in the noise.

### Pre-commit hook

A hook at `.git/hooks/pre-commit` runs lint, format check, and tests before
every commit, and refuses the commit if any of them fail.

**Git hooks are not committed**, so this does not travel with the repository.
To set it up on a fresh clone, create `.git/hooks/pre-commit` containing:

```sh
#!/bin/sh
set -e

.venv/Scripts/ruff check src tests
.venv/Scripts/ruff format --check src tests
.venv/Scripts/pytest -q
```

Save it with LF line endings, not CRLF — the shell cannot read the shebang
otherwise. Use `git commit --no-verify` to bypass it when you genuinely need to.

## Design decisions

Recorded so they are not silently undone later.

**Conversation history uses a generic shape.** Turns are stored as
`{"role": ..., "content": ...}`, not in Gemini's own format. A single function,
`to_gemini_input`, translates on the way out. Switching providers means
rewriting that one function instead of every place history is touched.

**The API key variable is `LLM_API_KEY`, not `GEMINI_API_KEY`.** The name does
not mention a vendor, so it survives a provider change. The trade-off: the
Gemini SDK auto-detects only `GEMINI_API_KEY`, so the key is passed to the
client explicitly.

**Secrets live outside the project folder.** Nothing to commit by accident,
nothing for editor tooling to index, nothing inside a folder granted to an
assistant.

**A failed request keeps the question in history.** When the API call fails,
the user turn stays and no assistant turn is added. That way "try again" in
the next turn still has something to refer to. The apology is printed to the
screen only — it never enters history, because history holds what was actually
said, not notes about the program's state.

**Ctrl-C is caught separately from API errors.** `KeyboardInterrupt` is not a
subclass of `Exception`, so the broad handler around the API call does not
swallow it. Without that separation, the program could not be stopped while
it was answering.

**Speech is detected by a small state machine, not a fixed timer.**
`SpeechDetector` has two states. WAITING starts recording after 200 ms of sound
in a row and keeps those frames as pre-roll, so the first word is not cut off;
it gives up after 3 s with nobody speaking. SPEAKING ends after 500 ms of
unbroken silence, or at a 10 s cap. It counts consecutive frames instead of
averaging levels, because with an average the real timeout would depend on how
loud you speak. When nobody speaks, the recorder returns `None` and Whisper is
skipped entirely. The detector never touches the microphone: frames are handed
in, so the same logic will work for audio arriving from the ESP32, and it is
tested with synthetic frames.

**Language convention.** Code, comments and documentation in English. Commit
messages are in Indonesian, using conventional prefixes (`feat:`, `fix:`,
`refactor:`, `docs:`, `chore:`).

## Project layout

```
mika/
├─ src/mika/
│  ├─ __init__.py
│  ├─ __main__.py      # entry point for `python -m mika`
│  ├─ app.py           # chat loop: typed or spoken input, history, errors
│  ├─ config.py        # reads secrets from outside the repo
│  ├─ llm.py           # Gemini streaming + history translation
│  ├─ audio.py         # microphone input until silence, WAV output, level meter
│  ├─ stt.py           # faster-whisper transcription
│  └─ vad.py           # loudness in dB, start/end-of-speech detection
├─ tests/
│  ├─ test_config.py
│  ├─ test_llm.py
│  └─ test_vad.py
├─ .env.example
└─ pyproject.toml
```

`app.py` was split once audio arrived and the file started changing for unrelated
reasons: each module now has one reason to change.
