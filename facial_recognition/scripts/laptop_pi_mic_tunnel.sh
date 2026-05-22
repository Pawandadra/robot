#!/usr/bin/env bash
# Load PipeWire/Pulse tunnel from Pi webcam mic and set it as default capture source.
# Configure via ~/.config/robot-pi-mic.env (see robot-pi-mic.env.example).

set -euo pipefail

ENV_FILE="${ROBOT_PI_MIC_ENV_FILE:-$HOME/.config/robot-pi-mic.env}"
if [[ -f "$ENV_FILE" ]]; then
  set -a
  # shellcheck source=/dev/null
  source "$ENV_FILE"
  set +a
else
  echo "Tip: copy scripts/robot-pi-mic.env.example to $ENV_FILE" >&2
fi

PI_HOST="${ROBOT_PI_HOST:-192.168.76.99}"
PI_PORT="${ROBOT_PI_PULSE_PORT:-4713}"
PI_SOURCE="${ROBOT_PI_MIC_SOURCE:-alsa_input.usb-FINGERS_FINGERS_1080_Hi-Res_Webcam_20200803-02.mono-fallback}"
LOCAL_NAME="${ROBOT_PI_MIC_LOCAL_NAME:-robot_pi_webcam_mic}"

if [[ -z "$PI_SOURCE" ]]; then
  echo "Set ROBOT_PI_MIC_SOURCE in $ENV_FILE (from Pi: pactl list sources short)" >&2
  exit 1
fi

tcp_ok() {
  timeout 3 bash -c "exec 3<>/dev/tcp/${PI_HOST}/${PI_PORT}" 2>/dev/null
}

unload_tunnels() {
  pactl list modules short 2>/dev/null | while read -r mid name _rest; do
    [[ "$name" == "module-tunnel-source" ]] || continue
    pactl unload-module "$mid" 2>/dev/null || true
  done
}

echo "Pi mic tunnel: tcp://${PI_HOST}:${PI_PORT} source=${PI_SOURCE}"

if ! tcp_ok; then
  echo "ERROR: Cannot reach ${PI_HOST}:${PI_PORT} (Pulse TCP on Pi)." >&2
  echo "  On Pi: ss -tlnp | grep 4713   (must show pipewire-pulse LISTEN)" >&2
  echo "  On Pi: /etc/pipewire/pipewire-pulse.conf.d/99-native-protocol-tcp.conf" >&2
  echo "       auth-ip-acl must include this laptop's subnet (e.g. 192.168.76.0/24)." >&2
  echo "  On Pi: pactl list sources short   (copy exact webcam name into ROBOT_PI_MIC_SOURCE)" >&2
  exit 1
fi

unload_tunnels
sleep 0.3

before=$(pactl list sources short 2>/dev/null | wc -l)

mid=""
if ! mid=$(pactl load-module module-tunnel-source \
  "server=tcp:${PI_HOST}:${PI_PORT}" \
  "source=${PI_SOURCE}" \
  "source_name=${LOCAL_NAME}" 2>&1); then
  mid=$(pactl load-module module-tunnel-source \
    "server=tcp:${PI_HOST}:${PI_PORT}" \
    "source=${PI_SOURCE}" 2>&1) || true
fi

# pactl prints module index on success (digits only)
if ! [[ "${mid:-}" =~ ^[0-9]+$ ]]; then
  echo "ERROR: pactl load-module failed: ${mid:-unknown}" >&2
  exit 1
fi

chosen=""
for _ in 1 2 3 4 5 6 7 8 9 10; do
  sleep 0.4
  if pactl list sources short 2>/dev/null | awk '{print $2}' | grep -qxF "$LOCAL_NAME"; then
    chosen="$LOCAL_NAME"
    break
  fi
  ts=$(pactl list sources short 2>/dev/null | awk '/tunnel/ {print $2; exit}')
  if [[ -n "${ts:-}" ]]; then
    chosen="$ts"
    break
  fi
done

after=$(pactl list sources short 2>/dev/null | wc -l)
if [[ -z "$chosen" ]]; then
  echo "ERROR: Tunnel module loaded (id $mid) but no capture source appeared." >&2
  echo "  Sources before/after: $before -> $after" >&2
  echo "  On Pi run: pactl list sources short" >&2
  echo "  Wrong source name? Current: ${PI_SOURCE}" >&2
  echo "  Unload stale modules: pactl unload-module $mid" >&2
  pactl list sources short >&2
  exit 1
fi

pactl set-default-source "$chosen"
pactl set-source-mute "$chosen" 0 2>/dev/null || true
echo "OK: default source = $chosen (Pi ${PI_HOST}:${PI_PORT})"
pactl list sources short | grep -E "($LOCAL_NAME|tunnel|${LOCAL_NAME})" || pactl list sources short
