"""Bridge browser microphone audio into a PulseAudio pipe-source.

The browser captures 16 kHz mono PCM and streams it over a WebSocket. A feeder
thread writes that audio into the FIFO backing module-pipe-source at realtime
rate, substituting silence whenever no client is streaming -- so consumers like
`rec` see a continuously running capture device instead of a stalled one.
"""

import os
import queue
import secrets
import threading
import time
from pathlib import Path

from aiohttp import WSMsgType, web

FIFO = os.environ.get("VIRTMIC_FIFO", "/tmp/virtmic.fifo")
PORT = int(os.environ.get("VIRTMIC_PORT", "8777"))
RATE = int(os.environ.get("VIRTMIC_RATE", "16000"))
CHANNELS = 1
FRAME_MS = 20
FRAME_SAMPLES = RATE * FRAME_MS // 1000
FRAME_BYTES = FRAME_SAMPLES * 2 * CHANNELS
SILENCE = b"\x00" * FRAME_BYTES
MAX_BACKLOG_BYTES = FRAME_BYTES * 10  # ~200 ms

HERE = Path(__file__).parent

# No token by default: the intended setup is a port reachable only by you (an
# SSH tunnel, or a private Codespaces forwarded port, where the platform already
# authenticates you). Set VIRTMIC_REQUIRE_TOKEN=1 whenever the port is exposed
# more broadly -- audio arriving here becomes dictated text on your machine, so
# an open port is an injection risk, not just a privacy one.
REQUIRE_TOKEN = os.environ.get("VIRTMIC_REQUIRE_TOKEN") == "1"
TOKEN_FILE = HERE / "token"

if REQUIRE_TOKEN:
    if not TOKEN_FILE.exists():
        TOKEN_FILE.write_text(secrets.token_urlsafe(24))
        TOKEN_FILE.chmod(0o600)
    TOKEN = TOKEN_FILE.read_text().strip()


def authorized(request: web.Request) -> bool:
    if not REQUIRE_TOKEN:
        return True
    return secrets.compare_digest(request.query.get("k", ""), TOKEN)

audio_q: queue.Queue = queue.Queue(maxsize=500)
stats = {
    "frames_in": 0,
    "frames_out": 0,
    "underruns": 0,
    "dropped": 0,
    "trimmed": 0,
    "q_full": 0,
}


def feeder() -> None:
    """Write audio to the FIFO at realtime rate, padding with silence."""
    fd = os.open(FIFO, os.O_WRONLY | os.O_NONBLOCK)
    next_tick = time.monotonic()
    buf = b""

    while True:
        # Drain everything pending: the browser sends ~125 small chunks/sec
        # (128-sample render quantums), far more than one per tick.
        while True:
            try:
                buf += audio_q.get_nowait()
            except queue.Empty:
                break

        # Cap backlog: browser and container clocks drift, and an unbounded
        # buffer would turn drift into ever-growing latency. Keep the newest.
        if len(buf) > MAX_BACKLOG_BYTES:
            buf = buf[-MAX_BACKLOG_BYTES:]
            stats["trimmed"] += 1

        if len(buf) >= FRAME_BYTES:
            frame, buf = buf[:FRAME_BYTES], buf[FRAME_BYTES:]
        else:
            frame = SILENCE
            stats["underruns"] += 1

        try:
            os.write(fd, frame)
            stats["frames_out"] += 1
        except BlockingIOError:
            # Pulse isn't draining the pipe (nobody recording). Drop the frame
            # rather than block the feeder and fall behind realtime.
            stats["dropped"] += 1

        next_tick += FRAME_MS / 1000
        sleep = next_tick - time.monotonic()
        if sleep > 0:
            time.sleep(sleep)
        else:
            next_tick = time.monotonic()


async def ws_handler(request: web.Request) -> web.WebSocketResponse:
    if not authorized(request):
        raise web.HTTPForbidden(text="bad token")

    ws = web.WebSocketResponse(max_msg_size=0)
    await ws.prepare(request)
    print("[virtmic] browser connected", flush=True)

    async for msg in ws:
        if msg.type == WSMsgType.BINARY:
            stats["frames_in"] += 1
            try:
                audio_q.put_nowait(msg.data)
            except queue.Full:
                stats["q_full"] += 1  # prefer fresh audio over a backlog
        elif msg.type == WSMsgType.ERROR:
            print(f"[virtmic] ws error: {ws.exception()}", flush=True)

    print("[virtmic] browser disconnected", flush=True)
    return ws


async def index(request: web.Request) -> web.StreamResponse:
    if not authorized(request):
        raise web.HTTPForbidden(text="bad token")
    return web.FileResponse(HERE / "index.html")


async def status(request: web.Request) -> web.Response:
    """Diagnostics. See README for how to read these counters."""
    # Loopback callers (your own shell on the host) skip the token.
    if request.remote not in ("127.0.0.1", "::1") and not authorized(request):
        raise web.HTTPForbidden(text="bad token")
    return web.json_response(stats)


def main() -> None:
    threading.Thread(target=feeder, daemon=True).start()
    app = web.Application()
    app.add_routes(
        [
            web.get("/", index),
            web.get("/ws", ws_handler),
            web.get("/status", status),
        ]
    )
    web.run_app(app, host=os.environ.get("VIRTMIC_HOST", "0.0.0.0"), port=PORT)


if __name__ == "__main__":
    main()
