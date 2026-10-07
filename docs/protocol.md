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
| `{"type": "start"}` | A new question begins. Sent when the button is pressed; later also when the wake word ("Hey Mika") is detected on the device. The server does not need to know which one it was. |

### Server → satellite

| Message | Meaning |
|---|---|
| `{"type": "listening"}` | Question accepted; frames are being processed. |
| `{"type": "stop", "reason": "speech_end"}` | The speaker finished. Stop streaming and wait for the answer. |
| `{"type": "stop", "reason": "no_speech"}` | Nobody spoke within 3 s. Stop streaming; the turn is over. |
| `{"type": "transcript", "text": "..."}` | What the server heard. |
| `{"type": "reply", "text": "..."}` | Mika's answer. The turn is over. |
| `{"type": "error", "code": "..."}` | Something went wrong (codes below). |

Error codes:

| Code | When | Turn over? |
|---|---|---|
| `busy` | `start` arrived while a question is still running | no, the running question continues |
| `bad_message` | a text message that is not valid JSON, has no string `type`, or has an unknown type | no |
| `bad_frame` | an audio frame that is not 640 bytes | yes |
| `empty_transcript` | speech was detected but nothing was recognised | yes |
| `server_error` | transcription or the language model failed | yes |

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
    | <-- {"type":"reply","text":...} ------ |
```

The satellite may start streaming right after sending `start`; it does not need
to wait for `listening`, because messages on one connection arrive in order.

Frames that were already in flight when `stop` was sent are expected and
silently ignored, as are any frames sent while no question is running.

Start and end of speech are decided on the server (200 ms of sound starts it,
500 ms of silence ends it, 10 s maximum), so the satellite only needs a button
and a microphone.

## Satellite state and the status LED

There is no separate "state" message. The satellite follows the messages it
already receives and keeps its own state (IDLE, LISTENING, THINKING), which is
also what the status LED shows:

| Received | Satellite state | LED |
|---|---|---|
| (connected, nothing running) | IDLE | off / idle |
| `listening` | LISTENING: stream frames | listening |
| `stop` with `speech_end` | THINKING: stop streaming, wait | thinking |
| `stop` with `no_speech` | IDLE | off / idle |
| `reply` | IDLE | off / idle |
| `error` that ends the turn (`bad_frame`, `empty_transcript`, `server_error`) | IDLE | error, briefly |
| `error` with `busy` or `bad_message` | unchanged | unchanged |
| connection lost | IDLE, then reconnect | error until reconnected |

`busy` and `bad_message` do not end the running question, so they must not
change the state or the LED. That is what the "Turn over?" column above means.

## Decided

- Trigger: a push button for now; a wake word ("Hey Mika") on the device later.
  Both send the same `start` message.
- Server discovery: mDNS, name `mika.local`, service `_mika._tcp`; the LAN IP
  as a fallback where multicast is blocked.
- Server location: the laptop for now; where it runs later is still open.

## Open questions for the group

- WebSocket itself is still a proposal: the firmware side has to agree before
  this protocol is final.
- Does the satellite have a speaker? If yes, a later version can send Mika's
  voice back as audio frames in the same format.
