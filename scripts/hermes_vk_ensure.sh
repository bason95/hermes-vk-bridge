#!/usr/bin/env bash
set -euo pipefail

HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
SCRIPT="${VK_BRIDGE_SCRIPT:-$HOME/hermes-vk-bridge/scripts/hermes_vk_bridge.py}"
LOG="${VK_ENSURE_LOG:-$HERMES_HOME/logs/vk_polling_ensure.log}"
HEARTBEAT="${VK_HEARTBEAT_PATH:-$HERMES_HOME/state/vk_polling_heartbeat}"
LOCK="${VK_ENSURE_LOCK:-/tmp/vk_polling_ensure.lock}"
PYTHON="${PYTHON:-/usr/bin/python3}"

mkdir -p "$HERMES_HOME/logs" "$HERMES_HOME/state"
exec 9>"$LOCK"
flock -n 9 || exit 0

ts() { date '+%F %T'; }

# Polling-only mode: remove callback/tunnel stack to avoid URL drift and duplicate delivery.
pkill -f 'vk_tunnel_callback_manager.py' 2>/dev/null || true
pkill -f 'ssh .*localhost.run.*:9120' 2>/dev/null || true

# Match only the real bridge process, not this watchdog shell or pgrep itself.
mapfile -t PIDS < <(pgrep -f "^(/usr/bin/)?python3 ${SCRIPT}$" || true)
COUNT=${#PIDS[@]}

if (( COUNT > 1 )); then
  KEEP=$(ps -o pid=,etimes= -p "${PIDS[@]}" | sort -k2,2nr | awk 'NR==1{print $1}')
  for pid in "${PIDS[@]}"; do
    if [[ "$pid" != "$KEEP" ]]; then
      kill "$pid" 2>/dev/null || true
    fi
  done
  echo "[$(ts)] dedup polling: kept=${KEEP} killed=$((COUNT-1))" >> "$LOG"
  mapfile -t PIDS < <(pgrep -f "^(/usr/bin/)?python3 ${SCRIPT}$" || true)
  COUNT=${#PIDS[@]}
fi

STALE=0
if [[ -f "$HEARTBEAT" ]]; then
  AGE=$(( $(date +%s) - $(stat -c %Y "$HEARTBEAT") ))
  if (( AGE > 900 )); then
    STALE=1
    echo "[$(ts)] stale heartbeat age=${AGE}s; restarting polling bridge" >> "$LOG"
  fi
elif (( COUNT > 0 )); then
  STALE=1
  echo "[$(ts)] missing heartbeat; restarting polling bridge" >> "$LOG"
fi

if (( STALE == 1 )); then
  for pid in "${PIDS[@]:-}"; do
    kill "$pid" 2>/dev/null || true
  done
  sleep 1
  mapfile -t PIDS < <(pgrep -f "^(/usr/bin/)?python3 ${SCRIPT}$" || true)
  COUNT=${#PIDS[@]}
fi

if (( COUNT == 0 )); then
  nohup "$PYTHON" "$SCRIPT" >/dev/null 2>&1 &
  echo "[$(ts)] started polling bridge pid=$!" >> "$LOG"
fi
