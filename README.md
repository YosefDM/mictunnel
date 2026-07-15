# virtmic

**Use your laptop's microphone on a remote Linux box that has no sound card.**

Containers and cloud VMs — GitHub Codespaces, Docker, a bare DigitalOcean droplet — have no audio hardware. There's no `/dev/snd`, no ALSA cards, nothing. So any tool that wants to record fails immediately:

```
$ rec test.wav
rec FAIL sox: Sorry, there is no default audio device configured
```

Installing SoX doesn't help. The machine genuinely has no microphone, and your microphone is on your laptop, hundreds of miles away.

virtmic bridges the gap. It captures audio in your browser, streams it to the remote host, and presents it there as a **real ALSA/PulseAudio capture device**. Anything that records — `rec`, `arecord`, `ffmpeg`, a voice-input CLI, a speech-to-text script — just works, with no awareness that the mic is somewhere else.

```
browser (getUserMedia)
    │  16 kHz mono PCM over a WebSocket
    ▼
server.py  ──writes──►  FIFO  ──►  PulseAudio module-pipe-source
                                        │
                                        ▼
                                   ALSA default
                                        │
                                        ▼
                            rec / arecord / ffmpeg / your tool
```

## Why not just forward the device?

You can't. Browser microphone permission grants audio to a page's JavaScript sandbox on your laptop; it creates no path into a container in a datacenter. SSH doesn't forward audio devices. `snd-aloop` (the ALSA loopback module) needs a kernel module you can't load in a container. So something on your laptop has to hold the capture and stream it in — which is what this does, entirely in userspace.

## Install

Linux host (the remote box), Python 3.8+:

```bash
git clone https://github.com/YosefDM/virtmic.git
cd virtmic
./setup.sh     # apt packages + ~/.asoundrc + pulse client.conf
./start.sh     # starts PulseAudio, the virtual source, and the server
```

`start.sh` prints a URL. Open it in your browser, click the mic, and grant permission. **Keep the tab open** — it's what holds the microphone.

Verify it works:

```bash
rec -c 1 -r 16000 test.wav trim 0 3   # say something
sox test.wav -n stat                   # "Maximum amplitude" should be well above 0
```

### Reaching the page

The server listens on port `8777` on the remote host. Get to it however you normally reach that box:

| Setup | How |
|---|---|
| SSH / VM | `ssh -L 8777:localhost:8777 user@host`, then open `http://localhost:8777` |
| GitHub Codespaces | Port 8777 auto-forwards; `start.sh` prints the URL. Keep it **private** |
| Local Docker | `-p 8777:8777`, then open `http://localhost:8777` |

`getUserMedia` requires a secure context, so the page must be on `https://` **or** `localhost`. An SSH tunnel satisfies this because the browser sees `localhost`. A bare `http://192.168.1.5:8777` will not work — the mic button will fail with `NotAllowedError`.

## After a restart

PulseAudio, the FIFO, and the server are runtime state and don't survive a reboot or container restart. Re-run `./start.sh`. Setup only needs to happen once.

## Troubleshooting

Check `curl localhost:8777/status`:

| Counter | Meaning |
|---|---|
| `frames_in` | Chunks received from the browser. **0 while you're speaking = the tab isn't connected** |
| `underruns` | Frames where no audio was available, so silence was sent. High while speaking = audio isn't arriving |
| `dropped` | Pipe was full; normal and expected whenever nothing is recording |
| `trimmed` | Backlog exceeded ~200 ms and was trimmed. Persistent = clock drift between browser and host |
| `q_full` | Queue overflowed. Should stay 0 |

**Recording is silent, but no errors.** The browser tab is closed or disconnected. This is the most common failure and it looks like a bug rather than a closed tab, because a virtual mic that nobody is feeding is indistinguishable from a quiet room.

**`rec FAIL sox: no default audio device`.** PulseAudio isn't running or ALSA isn't pointed at it. Re-run `./start.sh`, then check `pactl list sources short` shows `virtmic`.

**`Connection refused` from libpulse.** `XDG_RUNTIME_DIR` isn't set in the calling process. `setup.sh` writes `~/.config/pulse/client.conf` to pin the socket path, which fixes this for processes that don't inherit your shell environment.

**Mic button fails with `NotAllowedError`.** The page isn't in a secure context (see above), or you denied permission — re-grant it via the address bar.

## Security

Audio sent to this server becomes microphone input on the remote host, and if you're using it for dictation, that becomes **typed text — potentially into a terminal**. Treat the port as a privileged input, not a stream you're merely embarrassed to leak.

Keep port 8777 reachable only by you: an SSH tunnel, or a *private* Codespaces port. If you must expose it more broadly, set `VIRTMIC_REQUIRE_TOKEN=1`, which generates a secret in `./token` and requires `?k=<token>` on the page and the WebSocket. Rotate by deleting the file and restarting.

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `VIRTMIC_PORT` | `8777` | HTTP/WebSocket port |
| `VIRTMIC_RATE` | `16000` | Sample rate (Hz). Must match `start.sh` and the page |
| `VIRTMIC_FIFO` | `/tmp/virtmic.fifo` | Pipe backing the Pulse source |
| `VIRTMIC_PYTHON` | `python3` | Interpreter to run the server with |
| `VIRTMIC_REQUIRE_TOKEN` | unset | Set to `1` to require a shared secret |
| `VIRTMIC_HOST` | `0.0.0.0` | Bind address |

## Design notes

**Silence padding.** The feeder writes to the FIFO on a 20 ms realtime clock, substituting silence when no audio is queued. Without it the device stalls instead of behaving like an always-on microphone, and recorders hang waiting for samples.

**Draining the queue.** The browser's AudioWorklet emits 128-sample chunks (~125/sec), far more than the feeder's 50 ticks/sec. Consuming one chunk per tick starves the device and produces mostly-silent, choppy audio — subtle, because it *sounds* like a bad mic rather than a bug. The feeder drains everything pending each tick.

**Backlog trimming.** Browser and host clocks drift. An unbounded buffer converts drift into steadily growing latency, so the backlog is capped at ~200 ms, keeping the newest audio.

**Non-blocking writes.** When nothing is recording, Pulse stops draining the pipe and it fills. The feeder drops those frames rather than blocking and falling behind realtime.

## Testing

`tools/tone_test.py` stands in for the browser, streaming a 440 Hz tone through the WebSocket:

```bash
python3 tools/tone_test.py 6 &      # stream a tone for 6 seconds
rec -c 1 -r 16000 tone.wav trim 0 3
sox tone.wav -n stat                # expect "Rough frequency: 440"
```

A continuous sine at amplitude 0.366 has an RMS of 0.259. If the measured RMS is materially below that, audio is being replaced by silence somewhere in the pipe. That ratio is what caught the queue-draining bug above.

## License

MIT
