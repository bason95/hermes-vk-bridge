#!/usr/bin/env bash
set -euo pipefail

mkdir -p "$HOME/.hermes/scripts" "$HOME/.hermes/logs" "$HOME/.hermes/state"
if [[ ! -f "$HOME/.hermes/scripts/vk_bridge.env" ]]; then
  cp .env.example "$HOME/.hermes/scripts/vk_bridge.env"
  echo "Created $HOME/.hermes/scripts/vk_bridge.env. Edit VK_GROUP_TOKEN before starting."
fi
chmod +x scripts/hermes_vk_ensure.sh scripts/hermes_vk_bridge.py

echo "Install complete. Run:"
echo "  python3 $PWD/scripts/hermes_vk_bridge.py"
