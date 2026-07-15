# mictunnel

**Use your laptop's microphone on a remote Linux box that has no sound card — so voice input works in a Codespace, a container, or over SSH.**

## What this solves

Containers and cloud VMs — GitHub Codespaces, Docker, a bare DigitalOcean droplet — have no audio hardware. There's no `/dev/snd`, no ALSA cards, nothing. So any tool that wants to record fails immediately:

```
$ rec test.wav
rec FAIL sox: Sorry, there is no default audio device configured
```

Installing SoX doesn't help. The machine genuinely has no microphone, and your microphone is on your laptop, hundreds of miles away.

## Why we built it

To talk to **Claude Code's voice mode from a terminal running inside a GitHub Codespace.**

If you develop in a Codespace, Claude Code runs in a container in a datacenter. Typing `/voice` gets you:

```
Voice mode requires a microphone, but SoX could not open an audio capture device.
This usually means the host has no microphone (for example, a remote server).
Run Claude Code on a machine with a microphone to use voice input.
```

That advice is correct but unsatisfying: your whole dev environment lives in the Codespace, and moving it to your laptop just to dictate is a big trade for a small feature. Your browser has your microphone and is already talking to the Codespace — the only thing missing is a path between the two. mictunnel is that path.

Nothing here is Claude Code specific, though. mictunnel presents a **real ALSA/PulseAudio capture device**, so anything that records — `rec`, `arecord`, `ffmpeg`, a speech-to-text script — works unmodified, with no awareness that the microphone is a thousand miles away.

## How it works

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
git clone https://github.com/YosefDM/mictunnel.git
cd mictunnel
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

## Using it with Claude Code voice mode

Inside the Codespace (or wherever Claude Code is running):

```bash
./start.sh          # prints the page URL
```

Open the URL in your browser, click the mic, grant permission, and leave the tab open. Then, in the Claude Code terminal:

```
/voice
```

Hold space to talk. Claude Code shells out to SoX, SoX opens the ALSA default, and that now resolves to audio coming from your browser.

**If `/voice` says the host has no microphone**, the virtual device isn't up — run `./start.sh` and check that `pactl list sources short` lists `mictunnel`.

**If `/voice` starts but transcribes nothing or garbage**, the device exists but no audio is reaching it. Ninety percent of the time the browser tab is closed. See Troubleshooting.

### Optional: an `/enable-voice` slash command

The bridge has to be restarted after every container restart, which is easy to forget. You can wrap it in a Claude Code skill so `/enable-voice` brings it up and prints the URL. Save this as `~/.claude/skills/enable-voice/SKILL.md`:

````markdown
---
name: enable-voice
description: Start the mictunnel bridge so /voice works, and print the page URL.
disable-model-invocation: true
allowed-tools: Bash(bash:*), Bash(curl:*)
---

## Bridge status (already executed)

!`bash ~/mictunnel/start.sh 2>&1`
!`curl -s localhost:8777/status 2>&1`

## Your task

Report the result in a few lines. Lead with the URL from the output. Tell the
user to open it, click the mic, and keep the tab open, then run `/voice`.
If `frames_in` is above 0, a tab is already streaming and may just need a
reload. Do not re-run the script — it has already run.
````

The `` !`command` `` syntax runs the script *before* Claude reads the skill, so the bridge is already up by the time it replies.

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

**Recording is silent, but no errors.** The browser tab is closed or disconnected. This is the most common failure and it looks like a bug rather than a closed tab, because a virtual mic that nobody is feeding is indistinguishable from a quiet room. Check `frames_in` — if it isn't climbing, no tab is streaming.

**The page says "Taken over".** Another tab (or another device) claimed the microphone; only one client streams at a time. Click the mic on the tab you want to use, and it takes it back.

**It worked, then stopped after a few minutes in the background.** This should now self-heal — the page re-arms itself after a discard, and a watchdog resumes a suspended audio context. If it still happens, check whether the page shows `Idle` (it was discarded and auto-start didn't fire — the origin may not have persistent mic permission) or `Live` with a frozen level ring (the audio context is suspended and the watchdog isn't recovering). Both are worth [opening an issue](https://github.com/YosefDM/mictunnel/issues) over, with your browser version.

**`rec FAIL sox: no default audio device`.** PulseAudio isn't running or ALSA isn't pointed at it. Re-run `./start.sh`, then check `pactl list sources short` shows `mictunnel`.

**`Connection refused` from libpulse.** `XDG_RUNTIME_DIR` isn't set in the calling process. `setup.sh` writes `~/.config/pulse/client.conf` to pin the socket path, which fixes this for processes that don't inherit your shell environment.

**Mic button fails with `NotAllowedError`.** The page isn't in a secure context (see above), or you denied permission — re-grant it via the address bar.

## FAQ

### Can you use Claude Code voice mode in a GitHub Codespace?

Yes — with mictunnel. Not otherwise. Claude Code's `/voice` records with SoX on whatever machine it's running on, and a Codespace has no microphone, so `/voice` refuses to start. mictunnel gives the Codespace a real capture device fed by your browser, and `/voice` then works normally.

### Why does Claude Code say "Voice mode requires a microphone, but SoX could not open an audio capture device"?

Because the machine Claude Code is running on has no audio input hardware. This is expected on a Codespace, a Docker container, an SSH session, or any cloud VM — the message means SoX looked for a capture device and found none. Installing SoX doesn't fix it; there's genuinely no microphone attached to that machine. Your microphone is on your laptop, which is a different computer.

### Does GitHub Codespaces support microphone access?

Not natively. Codespaces forwards TCP ports, not audio devices, and there's no `/dev/snd` inside the container. Granting mic permission to a browser tab gives audio to that page's JavaScript, not to processes running in the Codespace. mictunnel is the bridge between the two.

### Can a Docker container use the host microphone?

Only if the host actually has one and you pass it in (`--device /dev/snd`). That doesn't apply to a remote container: the "host" is a machine in a datacenter with no sound card, and your microphone is on your laptop. mictunnel works for that case, and needs no privileged flags or kernel modules.

### How do I get a microphone on a remote server or VM over SSH?

SSH forwards ports, not audio devices. Run mictunnel on the server, tunnel the port (`ssh -L 8777:localhost:8777 user@host`), and open the page locally. The server gets a working capture device that any tool can record from.

### Can a VS Code extension or the built-in Simple Browser capture the microphone?

Not in VS Code for the Web (Codespaces in a browser). Webviews are cross-origin iframes rendered without `allow="microphone"`, so `getUserMedia` fails with `NotAllowedError` and no prompt ever appears — see [vscode#303293](https://github.com/microsoft/vscode/issues/303293). Opening a real browser page, which is what mictunnel does, is the standard workaround. VS Code Desktop webviews are Electron and *can* prompt, but that only helps if you're not in the browser.

### Why does the microphone stop working after a few minutes?

Because the browser discarded the tab. Chrome and Edge reclaim memory from backgrounded tabs, and a discarded tab reloads to a fresh page with the microphone released — so the host keeps recording, but records silence, and nothing reports an error. mictunnel works around this: the page plays an inaudible keep-alive tone so it's treated as playing audio, and it re-acquires the microphone automatically if it does get reloaded. You shouldn't have to touch it.

### Does it work with dictation tools other than Claude Code?

Yes. The device is a real ALSA/PulseAudio source, so `rec`, `arecord`, `ffmpeg`, Whisper scripts, and anything else that records will find it as the default microphone.

### What are the requirements?

The remote host must be Linux with PulseAudio available (any container or VM works — no kernel modules, no root beyond `apt-get`). The browser must be Chrome, Edge, Firefox, or Safari, on a page served over `https://` or `localhost`.

## Security

Audio sent to this server becomes microphone input on the remote host, and if you're using it for dictation, that becomes **typed text — potentially into a terminal**. Treat the port as a privileged input, not a stream you're merely embarrassed to leak.

Keep port 8777 reachable only by you: an SSH tunnel, or a *private* Codespaces port. If you must expose it more broadly, set `MICTUNNEL_REQUIRE_TOKEN=1`, which generates a secret in `./token` and requires `?k=<token>` on the page and the WebSocket. Rotate by deleting the file and restarting.

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `MICTUNNEL_PORT` | `8777` | HTTP/WebSocket port |
| `MICTUNNEL_RATE` | `16000` | Sample rate (Hz). Must match `start.sh` and the page |
| `MICTUNNEL_FIFO` | `/tmp/mictunnel.fifo` | Pipe backing the Pulse source |
| `MICTUNNEL_PYTHON` | `python3` | Interpreter to run the server with |
| `MICTUNNEL_REQUIRE_TOKEN` | unset | Set to `1` to require a shared secret |
| `MICTUNNEL_HOST` | `0.0.0.0` | Bind address |

## Design notes

**Silence padding.** The feeder writes to the FIFO on a 20 ms realtime clock, substituting silence when no audio is queued. Without it the device stalls instead of behaving like an always-on microphone, and recorders hang waiting for samples.

**Draining the queue.** The browser's AudioWorklet emits 128-sample chunks (~125/sec), far more than the feeder's 50 ticks/sec. Consuming one chunk per tick starves the device and produces mostly-silent, choppy audio — subtle, because it *sounds* like a bad mic rather than a bug. The feeder drains everything pending each tick.

**Backlog trimming.** Browser and host clocks drift. An unbounded buffer converts drift into steadily growing latency, so the backlog is capped at ~200 ms, keeping the newest audio.

**Non-blocking writes.** When nothing is recording, Pulse stops draining the pipe and it fills. The feeder drops those frames rather than blocking and falling behind realtime.

**Surviving tab discards.** Browsers discard backgrounded tabs to reclaim memory, and a discarded tab reloads to a fresh page with the microphone released — the host then records silence indefinitely, with no error anywhere. The page defends on three fronts: it plays an inaudible keep-alive tone so it counts as playing audio (browsers rarely discard those), it auto-starts on load when the origin already holds mic permission (`getUserMedia` needs no fresh gesture then, so a reloaded tab re-arms itself), and a watchdog resumes a suspended `AudioContext` or rebuilds the audio graph if frames stop arriving.

**One streamer at a time.** Two clients feeding the same queue interleave into noise — audible as mangled, wrong-pitch audio rather than an error. The newest connection wins, and the displaced client is closed with code `4001`, which tells it to stand down. That code is load-bearing: a displaced client that simply reconnected would kick the new one straight back, and two tabs would fight forever.

## Testing

`tools/tone_test.py` stands in for the browser, streaming a 440 Hz tone through the WebSocket:

```bash
python3 tools/tone_test.py 6 &      # stream a tone for 6 seconds
rec -c 1 -r 16000 tone.wav trim 0 3
sox tone.wav -n stat                # expect "Rough frequency: 440"
```

A continuous sine at amplitude 0.366 has an RMS of 0.259. If the measured RMS is materially below that, audio is being replaced by silence somewhere in the pipe. That ratio is what caught the queue-draining bug above.

The tone test **takes over from a connected browser tab** (one streamer at a time), which is what you want — it guarantees the measurement is your tone and nothing else. A live tab streaming into the same pipe interleaves with the tone and produces plausible-looking nonsense: a wrong dominant frequency and a depressed RMS that read like a pipeline bug rather than two sources fighting. When the test finishes, click the mic on your tab to take the microphone back.

## License

MIT
