# Satellite protocol (draft)

How a satellite (the ESP32-S3) talks to the Mika server. This is a proposal for
the group: nothing here is final until the firmware side agrees.

## Connection

- WebSocket, plain `ws://`, port `8765`.
- Server address: `ws://mika.local:8765`. The server announces itself over mDNS,
  so the firmware does not hard-code an IP that changes with every network
  (home Wi-Fi, campus, phone hotspot). Two ways to find it on Arduino-ESP32:
  - `MDNS.queryHost("mika")` returns the address; the port is 8765.
  - `MDNS.queryService("mika", "tcp")` finds the `_mika._tcp` service and
    returns both address and port, so a changed port needs no new firmware.
- Look the address up again before every reconnect: the laptop may have moved
  to another network and got a new IP.
- Fallback: some networks (often campus or office Wi-Fi) block the multicast
  that mDNS relies on. There, use the laptop's LAN IP instead
  (`ipconfig` → Wi-Fi → IPv4 Address), for example `ws://192.168.1.20:8765`.
  `python -m mika.discovery` on another laptop shows whether mDNS works on
  that network.
- One connection per satellite. The server keeps one conversation history per
  connection, so reconnecting starts a fresh conversation.
- No compression extension (raw PCM barely compresses).

### Keeping the connection alive

- The server sends a WebSocket ping every 20 s and closes the connection if no
  pong arrives within 20 s. The satellite must answer pings; most ESP32
  WebSocket libraries do this automatically, but check that it is enabled.
- When the connection drops, the satellite goes back to idle (any question in
  progress is lost) and reconnects on its own, waiting a little longer after
  each failed attempt (for example 1 s, 2 s, 4 s, up to 30 s) so it does not
  flood the network while the server is down.
- After reconnecting, the conversation starts fresh (see above).

## Audio

Each audio frame is **one binary WebSocket message of exactly 640 bytes**:

| Property | Value |
|---|---|
| Encoding | signed 16-bit PCM, little-endian |
| Sample rate | 16 000 Hz |
| Channels | 1 (mono) |
| Frame length | 20 ms = 320 samples = 640 bytes |

Do not batch several frames into one message, and do not split one frame over two
messages. A frame of any other size aborts the current question with `bad_frame`.

The server does not care which microphone produced the samples; converting to
this format is the firmware's job. Two common cases:

- **I2S MEMS mic (INMP441 and similar):** samples arrive in 32-bit slots with the
  data in the top 24 bits. Shift right to keep the top 16 bits.
- **Analog mic through the ADC (MAX9814 and similar):** 12-bit unsigned values
  around a DC offset. Subtract the offset, then scale to the 16-bit range.

## Control messages

Text WebSocket messages holding one JSON object with a `"type"` field. Unknown
extra fields are ignored.

### Satellite → server

| Message | Meaning |
|---|---|
| `{"type": "start"}` | A new question begins. Sent when the device detects the wake word ("Hey Mika"). A push button, if the firmware has one, sends the same message: the server does not need to know which one it was. |

### Server → satellite

| Message | Meaning |
|---|---|
| `{"type": "listening"}` | Question accepted; frames are being processed. |
| `{"type": "stop", "reason": "speech_end"}` | The speaker finished. Stop streaming and wait for the answer. |
| `{"type": "stop", "reason": "no_speech"}` | Nobody spoke within 3 s. Stop streaming; the turn is over. |
| `{"type": "transcript", "text": "..."}` | What the server heard. |
| `{"type": "reply", "text": "..."}` | Mika's answer as text. Her voice follows as audio frames (see "Mika's voice" below). |
| `{"type": "done"}` | The last audio frame of the answer has been sent. The turn is over once the satellite has played what it holds. |
| `{"type": "error", "code": "..."}` | Something went wrong (codes below). |

Error codes:

| Code | When | Turn over? |
|---|---|---|
| `busy` | `start` arrived while a question is still running | no, the running question continues |
| `bad_message` | a text message that is not valid JSON, has no string `type`, or has an unknown type | no |
| `bad_frame` | an audio frame that is not 640 bytes | yes |
| `empty_transcript` | speech was detected but nothing was recognised | yes |
| `server_error` | transcription or the language model failed | yes |
| `tts_error` | the reply text was sent, but making Mika's voice failed (no frames, no `done`) | yes |

## One question, step by step

```
satellite                                server
    | --- {"type":"start"} ---------------> |
    | <-- {"type":"listening"} ------------ |
    | === 640-byte frame ==================> |   every 20 ms
    | === 640-byte frame ==================> |
    |            ...                         |
    | <-- {"type":"stop","reason":"speech_end"} |
    |   (stop streaming)                     |   transcribe + ask
    | <-- {"type":"transcript","text":...} - |
    | <-- {"type":"reply","text":...} ------ |   make the voice (about 1.5 s)
    | <== 640-byte frame =================== |   10 frames at once,
    | <== 640-byte frame =================== |   then one every 20 ms
    |            ...                         |
    | <-- {"type":"done"} ------------------ |
    |   (play what is left, then idle)       |
```

The satellite may start streaming right after sending `start`; it does not need
to wait for `listening`, because messages on one connection arrive in order.

Frames that were already in flight when `stop` was sent are expected and
silently ignored, as are any frames sent while the server is working on the
answer, while Mika is speaking, or while no question is running. The server keeps
reading during that time, so a `start` sent before `done` (or a turn-ending
error) is answered with `busy` at once; it does not start a second question after
the first one ends.

Start and end of speech are decided on the server (200 ms of sound starts it,
500 ms of silence ends it, 10 s maximum), so the satellite only needs its wake
word and a microphone.

The wake word runs on the device, so no audio leaves it until "Hey Mika" is heard.
After `start`, the server waits up to 3 s for the question to begin. For a
question said in one breath ("Hey Mika, what time is it?"), start streaming right
after the wake word is detected, or the first words are lost.

## Mika's voice (server → satellite)

After `reply`, the server sends Mika's spoken answer as binary messages in
**exactly the same format as the microphone frames**: 640 bytes, 16 kHz, mono,
signed 16-bit little-endian, 20 ms each. The last frame is padded with zeros
(silence), so every frame is full. `done` follows the last frame.

Pacing:

- The first **10 frames (200 ms) are sent at once**, so the satellite has some
  audio in hand before playing; a short Wi-Fi hiccup then does not cut Mika off.
- After that, **one frame every 20 ms**, the pace they are played at. The server
  never runs ahead, so the satellite never needs to hold a whole answer (10 s of
  audio would be 320 KB).

What the satellite does:

- Keep a small queue of frames, at least 16 (about 10 KB), and play from it
  through I2S as frames arrive.
- **Turn the microphone and the wake word off while speaking**, or Mika's own
  voice from the speaker could trigger a new question.
- On `done`, play what is still queued, then go back to idle.
- On `tts_error`, there is no speech: show the error briefly and go idle.

## Satellite state and the status LED

There is no separate "state" message. The satellite follows the messages it
already receives and keeps its own state (IDLE, LISTENING, THINKING, SPEAKING),
which is also what the status LED shows:

| Received | Satellite state | LED |
|---|---|---|
| (connected, nothing running) | IDLE | off / idle |
| `listening` | LISTENING: stream frames | listening |
| `stop` with `speech_end` | THINKING: stop streaming, wait | thinking |
| `stop` with `no_speech` | IDLE | off / idle |
| `reply` | SPEAKING: mic off, play frames as they come | speaking |
| `done` | IDLE once the queue has been played | off / idle |
| `error` that ends the turn (`bad_frame`, `empty_transcript`, `server_error`, `tts_error`) | IDLE | error, briefly |
| `error` with `busy` or `bad_message` | unchanged | unchanged |
| connection lost | IDLE, then reconnect | error until reconnected |

`busy` and `bad_message` do not end the running question, so they must not
change the state or the LED. That is what the "Turn over?" column above means.

## Decided

- Trigger: the wake word ("Hey Mika"), detected on the device. A push button is
  optional (handy for testing, or in a noisy room); it sends the same `start`.
- Server discovery: mDNS, name `mika.local`, service `_mika._tcp`; the LAN IP
  as a fallback where multicast is blocked.
- Server location: the laptop for now; where it runs later is still open.
- The satellite has a speaker, so the server sends Mika's voice back as audio
  frames (see "Mika's voice").

## Open questions for the group

- WebSocket itself is still a proposal: the firmware side has to agree before
  this protocol is final.
- Is a 200 ms head start and one frame per 20 ms right for the firmware's
  buffer and I2S output? The numbers are constants on the server
  (`PREBUFFER_FRAMES` in `server.py`) and easy to change.
