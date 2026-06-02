# Hermes VK Bridge

A VKontakte (VK) bridge for [Hermes Agent](https://github.com/NousResearch/hermes-agent).

Hermes currently has no first-party VKontakte gateway adapter. This project
provides a polling-only bridge that reads unread VK community messages, calls
Hermes CLI one-shot sessions, and replies via `messages.send`.

## Features

- VK polling mode via `messages.getConversations(filter=unread)` — no public
  callback URL or tunnel required.
- Hybrid routing:
  - simple chat uses a fast `clarify` toolset;
  - coding, files, search/current facts, integrations, screenshots, and media
    use a full Hermes toolset with `terminal`, `file`, `web`, `browser`,
    `vision`, etc.
- Separate daily Hermes sessions for quick and full lanes:
  `vk_<peer_id>_<YYYYMMDD>_quick` and `vk_<peer_id>_<YYYYMMDD>_full`.
  This prevents a clarify-only session from accidentally reusing a restricted
  tool schema for later coding/tool tasks.
- Optional `--yolo` mode for Hermes CLI calls (disabled by default for safety).
- VK attachments are downloaded locally and passed to Hermes as file paths.
- Final answers containing `MEDIA:/path/to/file` are uploaded back to VK as
  photos or docs.
- `/trace on` / `/trace off` for progress messages in VK chat.
- Watchdog script to keep one bridge process alive and remove stale callback
  tunnel processes.
- No third-party Python dependencies; uses Python stdlib only.

## Requirements

- Python 3.10+
- Hermes Agent CLI available as `hermes`
- VK community token with message permissions

## Quick start

```bash
git clone https://github.com/YOUR_USER/hermes-vk-bridge.git
cd hermes-vk-bridge
cp .env.example ~/.hermes/scripts/vk_bridge.env
$EDITOR ~/.hermes/scripts/vk_bridge.env
python3 scripts/hermes_vk_bridge.py
```

In VK, send `/status` to the community chat.

## Environment

The bridge reads `VK_GROUP_TOKEN` from either environment variables or
`~/.hermes/scripts/vk_bridge.env` by default. You can override the env-file path:

```bash
VK_ENV_PATH=/path/to/vk_bridge.env python3 scripts/hermes_vk_bridge.py
```

Important variables:

- `VK_GROUP_TOKEN` — required VK community token.
- `VK_ALLOWED_USERS` — required by default; comma-separated numeric VK user IDs allowed to use the bot.
- `VK_ALLOW_ALL_USERS=1` — explicit opt-in for public/demo bots. Without this and without `VK_ALLOWED_USERS`, the standalone bridge ignores messages.
- `HERMES_BIN` — Hermes executable, default `hermes`.
- `VK_HERMES_TIMEOUT_SEC` — default `420`.
- `VK_HERMES_QUICK_TOOLSETS` — default `clarify`.
- `VK_HERMES_FULL_TOOLSETS` — default full Hermes tool stack.
- `VK_HERMES_AUTO_YOLO` — default disabled (`0`). Set to `1` only for trusted users/environments.

## Running with watchdog

```bash
chmod +x scripts/hermes_vk_ensure.sh
VK_BRIDGE_SCRIPT=$PWD/scripts/hermes_vk_bridge.py scripts/hermes_vk_ensure.sh
```

Cron example:

```cron
*/5 * * * * VK_BRIDGE_SCRIPT=/opt/hermes-vk-bridge/scripts/hermes_vk_bridge.py /opt/hermes-vk-bridge/scripts/hermes_vk_ensure.sh
```

## Commands in VK

- `/help` — show short help.
- `/status` — show routing/toolset/session status.
- `/new` — reset quick and full daily contexts.
- `/trace on` — enable detailed progress messages for the peer.
- `/trace off` — disable detailed progress messages.

## Standalone bridge vs native plugin

`scripts/hermes_vk_bridge.py` is the stable standalone bridge.

`plugins/vk-platform/adapter.py` is an experimental native Hermes Gateway
platform adapter. It routes VK messages through the normal Gateway pipeline, but
standalone mode is currently the most battle-tested path.

## Security notes

- Do not commit `vk_bridge.env` or VK tokens.
- The standalone bridge is default-deny: set `VK_ALLOWED_USERS` for trusted users or explicitly `VK_ALLOW_ALL_USERS=1` for public/demo bots.
- Keep `VK_HERMES_AUTO_YOLO=0` unless every allowed VK user is trusted to trigger local terminal/file operations.
- Use Hermes platform allowlists (`VK_ALLOWED_USERS` / `VK_ALLOW_ALL_USERS`) if you adapt the native plugin for production.
- Treat any GitHub/VK token pasted into chat as compromised and revoke it after use.

## License

MIT
