#!/usr/bin/env bash
# Start the virtual microphone. Idempotent -- re-run after any host restart,
# since PulseAudio, the FIFO, and the server are all runtime state.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PORT="${VIRTMIC_PORT:-8777}"
FIFO="${VIRTMIC_FIFO:-/tmp/virtmic.fifo}"
RATE="${VIRTMIC_RATE:-16000}"
PY="${VIRTMIC_PYTHON:-python3}"

export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
if [ ! -d "$XDG_RUNTIME_DIR" ]; then
    sudo mkdir -p "$XDG_RUNTIME_DIR"
    sudo chown "$(id -u):$(id -g)" "$XDG_RUNTIME_DIR"
fi

if ! command -v pulseaudio > /dev/null; then
    echo "pulseaudio not found -- run ./setup.sh first" >&2
    exit 1
fi

# --exit-idle-time=-1 keeps the daemon alive with no clients connected.
pulseaudio --check || pulseaudio --start --exit-idle-time=-1 --disallow-exit
sleep 1

if ! pactl list sources short | grep -q "\bvirtmic\b"; then
    rm -f "$FIFO"
    pactl load-module module-pipe-source \
        source_name=virtmic file="$FIFO" \
        format=s16le rate="$RATE" channels=1 > /dev/null
    pactl set-default-source virtmic
fi

# Kill by listening port rather than command pattern: the server may have been
# started via a relative path (so an absolute-path pattern misses it and the new
# process dies on "address already in use"), while a bare "server.py" pattern is
# broad enough to match unrelated shells.
fuser -k "${PORT}/tcp" 2> /dev/null || true
sleep 1

nohup "$PY" "$HERE/server.py" > "$HERE/server.log" 2>&1 &
sleep 2

if ! curl -sf "localhost:${PORT}/status" > /dev/null; then
    echo "ERROR: server did not start. Last log lines:" >&2
    tail -5 "$HERE/server.log" >&2
    exit 1
fi

echo "virtmic is up. Open the page in your browser and click the mic:"
if [ -n "${CODESPACE_NAME:-}" ]; then
    echo "  https://${CODESPACE_NAME}-${PORT}.${GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN}"
    echo "  (port ${PORT} must be forwarded -- keep it private)"
else
    echo "  http://localhost:${PORT}"
    echo "  On a remote host, tunnel first:  ssh -L ${PORT}:localhost:${PORT} <user>@<host>"
fi
