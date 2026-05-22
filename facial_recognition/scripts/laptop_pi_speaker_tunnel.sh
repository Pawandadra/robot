#!/usr/bin/env bash
# Route laptop default *playback* (Piper/paplay) to the Pi's speakers/HDMI jack.
# Uses same env file as the mic tunnel (~/.config/robot-pi-mic.env by default).

set -euo pipefail

ENV_FILE="${ROBOT_PI_MIC_ENV_FILE:-$HOME/.config/robot-pi-mic.env}"
if [[ -f "$ENV_FILE" ]]; then
  set -a
  # shellcheck source=/dev/null
  source "$ENV_FILE"
  set +a
fi

if [[ -z "${ROBOT_PI_SINK:-}" ]]; then
  echo "ROBOT_PI_SINK not set in $ENV_FILE — skipping Pi speaker tunnel."
  echo "On the Pi run: pactl list sinks short   and set ROBOT_PI_SINK to the default output (2nd column)."
  exit 0
fi

PI_HOST="${ROBOT_PI_HOST:-192.168.1.50}"
PI_PORT="${ROBOT_PI_PULSE_PORT:-4713}"
LOCAL_NAME="${ROBOT_PI_SPEAKER_LOCAL_NAME:-robot_pi_speaker}"
# Laptop-side tunnel sink volume (often need >100% for Pi playback); also un-mutes.
ROBOT_PI_SPEAKER_VOLUME_PERCENT="${ROBOT_PI_SPEAKER_VOLUME_PERCENT:-150}"

unload_tunnel_sinks() {
  pactl list modules short 2>/dev/null | while read -r mid name _rest; do
    [[ "$name" == "module-tunnel-sink" ]] || continue
    pactl unload-module "$mid" 2>/dev/null || true
  done
}

unload_tunnel_sinks
sleep 0.3

if ! pactl load-module module-tunnel-sink \
  "server=tcp:${PI_HOST}:${PI_PORT}" \
  "sink=${ROBOT_PI_SINK}" \
  "sink_name=${LOCAL_NAME}"; then
  pactl load-module module-tunnel-sink \
    "server=tcp:${PI_HOST}:${PI_PORT}" \
    "sink=${ROBOT_PI_SINK}" || exit 1
fi

sleep 0.5

chosen=""
if pactl list sinks short | awk '{print $2}' | grep -qxF "$LOCAL_NAME"; then
  chosen="$LOCAL_NAME"
else
  ts=$(pactl list sinks short | awk '/tunnel/ {print $2; exit}')
  if [[ -n "${ts:-}" ]]; then
    chosen="$ts"
  fi
fi

if [[ -z "$chosen" ]]; then
  echo "Tunnel loaded but could not find sink to set as default; run: pactl list sinks short" >&2
  exit 1
fi

pactl set-default-sink "$chosen"
pactl set-sink-mute "$chosen" 0 || true
pactl set-sink-volume "$chosen" "${ROBOT_PI_SPEAKER_VOLUME_PERCENT}%" || true

echo "Default sink: $chosen @ ${ROBOT_PI_SPEAKER_VOLUME_PERCENT}% (Pi $PI_HOST:$PI_PORT)"
