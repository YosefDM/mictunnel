#!/usr/bin/env bash
# One-time setup: install system packages and point ALSA at PulseAudio.
# Re-running is harmless.
set -euo pipefail

SUDO=""
[ "$(id -u)" -ne 0 ] && SUDO="sudo"

echo "==> Installing packages (sox, pulseaudio, ALSA pulse plugin)"
$SUDO apt-get update -qq
$SUDO apt-get install -y sox pulseaudio pulseaudio-utils libasound2-plugins

# Route ALSA's default capture device to the PulseAudio source. Without this,
# tools like `rec` find no device at all.
if [ -e "$HOME/.asoundrc" ] && ! grep -q "virtmic" "$HOME/.asoundrc"; then
    echo "==> Backing up existing ~/.asoundrc to ~/.asoundrc.bak"
    cp "$HOME/.asoundrc" "$HOME/.asoundrc.bak"
fi

echo "==> Writing ~/.asoundrc"
cat > "$HOME/.asoundrc" <<'EOF'
# virtmic: send ALSA's default capture to the PulseAudio `virtmic` source.
pcm.!default {
    type asym
    playback.pcm { type pulse }
    capture.pcm  { type pulse device virtmic }
}

ctl.!default { type pulse }
EOF

# libpulse locates the daemon via XDG_RUNTIME_DIR. Processes that don't have it
# set (a bare `rec`, or an app launched outside your shell) would otherwise fail
# with "Connection refused", so pin the socket path explicitly.
echo "==> Writing ~/.config/pulse/client.conf"
mkdir -p "$HOME/.config/pulse"
cat > "$HOME/.config/pulse/client.conf" <<EOF
default-server = unix:/run/user/$(id -u)/pulse/native
autospawn = no
EOF

echo "==> Installing Python dependency (aiohttp)"
PY="${VIRTMIC_PYTHON:-python3}"
"$PY" -m pip install --quiet --user aiohttp || \
    echo "    pip install failed -- install aiohttp yourself, or set VIRTMIC_PYTHON to a venv python"

echo
echo "Setup complete. Now run: ./start.sh"
