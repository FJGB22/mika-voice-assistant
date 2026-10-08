# Mika

A small voice-assistant project, built one stage at a time. It runs in two ways:

- **On the laptop** (`mika`): type, or just speak. It records until you stop
  talking, transcribes locally with faster-whisper, streams the answer from
  Gemini, and remembers the conversation.
- **As a server for a satellite** (`python -m mika.server`): a small device
  (an ESP32-S3 with a microphone) streams audio over WebSocket on the local
  network; the server detects the end of speech, transcribes, asks Gemini, and
  sends the text back. The server announces itself as `mika.local` (mDNS), so
  the device finds it without a hard-coded IP address. Until the firmware is
  ready, `fake_satellite` plays the device's part from the laptop.

Later stages add speech output (TTS), a wake word, and real-time data such as
weather and time.

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

pip install -e ".[dev,audio,whisper,net]"
```

The extras keep installs small where a part is not needed:

| Extra | Brings | Needed for |
|---|---|---|
| `audio` | sounddevice, numpy | the laptop mic: `mika` and `fake_satellite` |
| `whisper` | faster-whisper | transcription: `mika`, the server |
| `net` | websockets, numpy, zeroconf | the server, `fake_satellite`, and `mika.discovery` |
| `dev` | pytest, ruff | tests and linting (the tests also need `whisper` and `net`) |

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

### On the laptop

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

### As a server

```bash
python -m mika.server
```

Wait for two lines:

```
Listening on ws://0.0.0.0:8765
mDNS: announced as mika.local (192.168.1.20)
```

The address in the second line should match `ipconfig` → Wi-Fi → IPv4 Address.
The first time, Windows Firewall asks whether to allow Python: allow **Private
networks** only, never Public, and make sure your Wi-Fi profile is set to
Private. mDNS uses UDP port 5353, so a blocked firewall stops the announcement
from being heard even though the server prints it.

If the server cannot announce, it says why on the `mDNS:` line and keeps
running: with no network at all, or when another machine on the same Wi-Fi is
already `mika.local` (for example a groupmate running the server too). In both
cases satellites can still connect by IP (see below).

Check that the announcement reaches the network, in a second terminal:

```bash
python -m mika.discovery
```

It should print `Found mika.local. at 192.168.1.20, port 8765` (the trailing dot
is normal). It asks the same question the ESP32 asks, so if this works from a
second laptop on the same Wi-Fi, the device will find the server too.

Then play the device's part:

```bash
python -m mika.fake_satellite                 # press Enter, then speak
python -m mika.fake_satellite --wav temp.wav  # replay a recording instead
```

`--wav` takes a 16 kHz, mono, 16-bit WAV, the same format the device sends. It
is sent in real time, followed by silence until the server stops it, so the
whole path (end-of-speech detection, Whisper, Gemini) runs without anyone
speaking. Each Enter replays the file from the start.

For one question the satellite should print, in order: `listening`, `stop`
(`speech_end`), `transcript`, `reply`. Pressing Enter and staying silent ends
with `stop` (`no_speech`).

Without a URL, `fake_satellite` connects to `localhost`. To go through the name
the ESP32 uses, pass it explicitly:

```bash
python -m mika.fake_satellite --wav temp.wav ws://mika.local:8765
```

Windows 10 and 11 resolve `.local` names themselves. If `mika.discovery` finds
the server but this fails to connect, the problem is the Windows resolver, not
the server; the ESP32 has its own resolver.

Some networks, often campus or office Wi-Fi, block the multicast that mDNS
relies on. There, use the laptop's LAN address instead, for example
`ws://192.168.1.20:8765`. Hotspots from a phone usually work. Try
`python -m mika.discovery` on the network you will demo on, beforehand.

Ctrl-C stops the server cleanly: it first tells the network to forget
`mika.local`, so running `python -m mika.discovery` afterwards finds nothing.

The wire format is in [`docs/protocol.md`](docs/protocol.md).

**There is no authentication.** Anyone on the same Wi-Fi can connect and use
your Gemini quota, and the mDNS announcement tells every device on the network
where the server is. Fine for a home or lab network; never expose the port to
the internet.

## Development

```bash
ruff check src tests        # lint
ruff format src tests       # format
pytest                      # tests
```

Keep the linter output at zero. A standing warning trains you to ignore the
output, and then the warnings that matter get lost in the noise.

The tests need no microphone, no network access and no API key: Whisper and
Gemini are replaced by fakes, the server tests talk to a real WebSocket on
`localhost` with a port the OS picks, and the mDNS tests use a fake socket and a
fake zeroconf, so nothing is ever announced on your network.

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
said, not notes about the program's state. The server follows the same rule.

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
in, so the same logic serves the laptop mic and the ESP32 alike.

**Audio reaches Whisper as an array, not a file.** The recording is converted in
memory from int16 to float32 in [-1.0, 1.0] and handed to faster-whisper
directly; nothing is written to disk. The divisor is 32768, not 32767: int16
runs from -32768 to 32767, and dividing by 32767 would put the lowest sample
just outside the range.

**Speech is detected on the server, not on the device.** The satellite only
needs a button and a microphone; start and end of speech, timeouts and the
length cap live in one place, in Python, where they are tested. The device
streams 20 ms frames until the server tells it to stop.

**The protocol rules never touch a socket.** `Session` takes messages in and
returns the messages to send, as a list; only `server.py` knows about
WebSocket. The rules are tested in milliseconds without a network, and if the
group picks another transport (MQTT, for example), only `server.py` is
rewritten.

**The wire format is written out explicitly.** Frames are decoded as
little-endian int16 (`"<i2"`) rather than "whatever this machine uses", and a
frame of any length other than 640 bytes is rejected with an error that names
the size. Control messages are JSON with string constants and short error
codes, which the firmware can compare without parsing sentences.

**Whisper and Gemini run in a worker thread.** They block for seconds. Called
directly, they would freeze the event loop, pings would go unanswered, and the
connection would be dropped during a slow answer. `asyncio.to_thread` keeps the
loop free.

**The server keeps listening while it answers.** Each answer runs as its own
task, so the connection loop goes on reading instead of waiting for it. A
`start` that arrives meanwhile is answered with `busy` right away, and audio
sent meanwhile is dropped. If the loop waited instead, those messages would
queue up and be read after the reply, and speech meant for nobody would become
a second question. When the satellite disconnects, its unfinished answer is
cancelled rather than sent to a closed connection. Tasks are kept in a set
until they finish, because asyncio holds them only weakly, and a callback
prints the traceback of any task that crashes, so a bug inside an answer is
never silent.

**One session and one history per connection.** A reconnect starts a fresh
conversation, and two satellites never see each other's history.

**The server is found by name, over mDNS.** The laptop's IP changes with every
network (home Wi-Fi, campus, phone hotspot) and every router restart; with the
IP in the firmware, each change would mean flashing the device again. The
server announces both the host name `mika.local` and the service `_mika._tcp`
(address and port in one answer), and the satellite looks the name up before
every reconnect. The LAN IP stays the fallback for networks that block
multicast.

**Only the interface that faces the network is announced.** A laptop also has
virtual adapters (WSL, VirtualBox) with addresses a satellite cannot reach.
`local_ip` asks the OS which interface it would use to go out, through a UDP
socket whose `connect()` sends nothing, and only that address is announced.

**Announcing is best effort; serving is not.** With no network, or with the
name already taken by another machine, the server prints why and keeps running
unannounced. A server that cannot be found by name can still be reached by IP;
one that crashed cannot be reached at all.

**The server says goodbye before it closes.** mDNS answers are cached for up to
two minutes. On shutdown, including Ctrl-C and crashes, the server unregisters
(an answer with TTL 0, "forget me now") and waits for those packets to go out
before closing the socket. Otherwise a satellite could keep trying the old
address after the server moved to another network.

**Language convention.** Code, comments and documentation in English. Commit
messages are in Indonesian, using conventional prefixes (`feat:`, `fix:`,
`refactor:`, `docs:`, `chore:`).

## Project layout

```
mika/
├─ src/mika/
│  ├─ __init__.py
│  ├─ __main__.py        # entry point for `python -m mika`
│  ├─ app.py             # laptop chat loop: typed or spoken input, history, errors
│  ├─ config.py          # reads secrets from outside the repo
│  ├─ llm.py             # Gemini streaming + history translation
│  ├─ audio.py           # microphone input until silence, level meter
│  ├─ stt.py             # faster-whisper transcription
│  ├─ vad.py             # frame size, loudness in dB, start/end-of-speech detection
│  ├─ protocol.py        # wire format: frame decoding, JSON messages, constants
│  ├─ session.py         # protocol rules for one satellite, no networking
│  ├─ server.py          # WebSocket server: one Session per connection
│  ├─ discovery.py       # mDNS: announce as mika.local, find a running server
│  └─ fake_satellite.py  # stands in for the ESP32: laptop mic or a WAV file
├─ tests/
│  ├─ test_config.py
│  ├─ test_llm.py
│  ├─ test_stt.py
│  ├─ test_vad.py
│  ├─ test_protocol.py
│  ├─ test_session.py
│  ├─ test_server.py
│  └─ test_discovery.py
├─ docs/
│  └─ protocol.md        # how a satellite talks to the server
├─ .env.example
└─ pyproject.toml
```

Each module has one reason to change: `protocol.py` when the contract with the
firmware changes, `session.py` when the conversation rules change, `server.py`
when the transport changes, `discovery.py` when the way satellites find the
server changes.
