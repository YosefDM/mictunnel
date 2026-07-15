"""Stand in for the browser: stream a 440 Hz tone over the WebSocket.

Sends 128-sample chunks, matching the browser's AudioWorklet render quantum.
The chunk size matters: a feeder that consumes one chunk per tick starves on
small chunks and silently substitutes silence, which sounds like a bad mic
rather than a bug.

Usage:  python3 tools/tone_test.py [seconds]
Then:   rec -c 1 -r 16000 tone.wav trim 0 3 && sox tone.wav -n stat
"""

import asyncio
import math
import os
import pathlib
import struct
import sys

import aiohttp

RATE = int(os.environ.get("MICTUNNEL_RATE", "16000"))
PORT = os.environ.get("MICTUNNEL_PORT", "8777")
FRAME = 128
FREQ = 440
AMPLITUDE = 12000  # 0.366 full scale -> RMS 0.259 for a clean sine


async def main(seconds: float) -> None:
    url = f"http://localhost:{PORT}/ws"
    token_file = pathlib.Path(__file__).resolve().parent.parent / "token"
    if token_file.exists():
        url += f"?k={token_file.read_text().strip()}"

    async with aiohttp.ClientSession() as session:
        async with session.ws_connect(url) as ws:
            phase, step = 0.0, 2 * math.pi * FREQ / RATE
            for _ in range(int(seconds * RATE / FRAME)):
                samples = []
                for _ in range(FRAME):
                    samples.append(int(math.sin(phase) * AMPLITUDE))
                    phase += step
                await ws.send_bytes(struct.pack(f"<{FRAME}h", *samples))
                await asyncio.sleep(FRAME / RATE)


if __name__ == "__main__":
    asyncio.run(main(float(sys.argv[1]) if len(sys.argv) > 1 else 4))
