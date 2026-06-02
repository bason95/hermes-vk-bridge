# Hermes VK Bridge

[English](#english) | [Русский](#русский)

---

## English

A VKontakte (VK) bridge for [Hermes Agent](https://github.com/NousResearch/hermes-agent).

Hermes currently has no first-party VKontakte gateway adapter. This project provides a polling-only bridge that reads unread VK community messages, calls Hermes CLI one-shot sessions, and replies via `messages.send`.

### Features

- VK polling mode via `messages.getConversations(filter=unread)` — no public callback URL or tunnel required.
- Hybrid routing:
  - simple chat uses a fast `clarify` toolset;
  - coding, files, search/current facts, integrations, screenshots, and media use a full Hermes toolset with `terminal`, `file`, `web`, `browser`, `vision`, etc.
- Separate daily Hermes sessions for quick and full lanes:
  `vk_<peer_id>_<YYYYMMDD>_quick` and `vk_<peer_id>_<YYYYMMDD>_full`.
- Optional `--yolo` mode for Hermes CLI calls (disabled by default for safety).
- VK attachments are downloaded locally and passed to Hermes as file paths.
- Final answers containing `MEDIA:/path/to/file` are uploaded back to VK as photos or docs.
- `/trace on` / `/trace off` for progress messages in VK chat.
- Optional invite-code onboarding: `/approve <code>`.
- Watchdog script to keep one bridge process alive and remove stale callback tunnel processes.
- No third-party Python dependencies; uses Python stdlib only.

### Requirements

- Python 3.10+
- Hermes Agent CLI available as `hermes`
- VK community token with message permissions
- A VK community with messages enabled

### Connection / setup guide

#### 1. Prepare a VK community

1. Open VK and create or select a community.
2. Enable community messages:
   - Community management → Messages → Community messages → Enabled.
3. Create a community access token:
   - Community management → Settings / API settings → Access tokens.
   - Create a token with message permissions.
4. Copy the token. It will be used as `VK_GROUP_TOKEN`.

This bridge uses polling, so you do **not** need Callback API, ngrok, localhost.run, or any public webhook URL.

#### 2. Clone the repository

```bash
git clone https://github.com/bason95/hermes-vk-bridge.git
cd hermes-vk-bridge
```

#### 3. Create the env file

```bash
mkdir -p ~/.hermes/scripts
cp .env.example ~/.hermes/scripts/vk_bridge.env
$EDITOR ~/.hermes/scripts/vk_bridge.env
```

Minimum safe config:

```env
VK_GROUP_TOKEN=vk1.a.your_group_token
VK_ALLOWED_USERS=123456789
VK_ALLOW_ALL_USERS=0
VK_HERMES_AUTO_YOLO=0
```

Invite-code config:

```env
VK_APPROVAL_CODE=change-me-long-random-code
```

Then an unknown VK user can send:

```text
/approve change-me-long-random-code
```

After successful approval, the user ID is stored in the bridge state file and stays approved across restarts.

#### 4. Run manually

```bash
python3 scripts/hermes_vk_bridge.py
```

In VK, send `/status` to the community chat.

#### 5. Run with watchdog

```bash
chmod +x scripts/hermes_vk_ensure.sh
VK_BRIDGE_SCRIPT=$PWD/scripts/hermes_vk_bridge.py scripts/hermes_vk_ensure.sh
```

Cron example:

```cron
*/5 * * * * VK_BRIDGE_SCRIPT=/opt/hermes-vk-bridge/scripts/hermes_vk_bridge.py /opt/hermes-vk-bridge/scripts/hermes_vk_ensure.sh
```

#### 6. Optional systemd service

Copy the repo to `~/hermes-vk-bridge`, then:

```bash
mkdir -p ~/.config/systemd/user
cp systemd/hermes-vk-bridge.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now hermes-vk-bridge.service
systemctl --user status hermes-vk-bridge.service
```

### Environment variables

- `VK_GROUP_TOKEN` — required VK community token.
- `VK_ALLOWED_USERS` — comma-separated numeric VK user IDs allowed to use the bot.
- `VK_APPROVAL_CODE` — optional invite code; unknown users can send `/approve <code>` in VK and become approved persistently.
- `VK_ALLOW_ALL_USERS=1` — explicit opt-in for public/demo bots. Without this, without `VK_ALLOWED_USERS`, and without successful approval code, the standalone bridge ignores messages.
- `HERMES_BIN` — Hermes executable, default `hermes`.
- `VK_HERMES_TIMEOUT_SEC` — default `420`.
- `VK_HERMES_QUICK_TOOLSETS` — default `clarify`.
- `VK_HERMES_FULL_TOOLSETS` — default full Hermes tool stack.
- `VK_HERMES_AUTO_YOLO` — default disabled (`0`). Set to `1` only for trusted users/environments.
- `VK_ENV_PATH` — optional path to env file, default `~/.hermes/scripts/vk_bridge.env`.
- `VK_STATE_PATH` — optional path to state JSON.
- `VK_LOG_PATH` — optional path to bridge log.
- `VK_INBOX_DIR` — optional directory for downloaded VK attachments.

### Commands in VK

- `/help` — show short help.
- `/status` — show routing/toolset/session status.
- `/new` — reset quick and full daily contexts.
- `/trace on` — enable detailed progress messages for the peer.
- `/trace off` — disable detailed progress messages.
- `/approve <code>` — approve the current VK user if the code matches `VK_APPROVAL_CODE`.

### Standalone bridge vs native plugin

`scripts/hermes_vk_bridge.py` is the stable standalone bridge.

`plugins/vk-platform/adapter.py` is an experimental native Hermes Gateway platform adapter. It routes VK messages through the normal Gateway pipeline, but standalone mode is currently the most battle-tested path.

### Security notes

- Do not commit `vk_bridge.env` or VK tokens.
- The standalone bridge is default-deny: set `VK_ALLOWED_USERS` for trusted users, set a private `VK_APPROVAL_CODE` for invite-code onboarding, or explicitly `VK_ALLOW_ALL_USERS=1` for public/demo bots.
- Keep `VK_HERMES_AUTO_YOLO=0` unless every allowed VK user is trusted to trigger local terminal/file operations.
- Treat any GitHub/VK token pasted into chat as compromised and revoke it after use.

---

## Русский

Мост ВКонтакте (VK) для [Hermes Agent](https://github.com/NousResearch/hermes-agent).

В Hermes пока нет официального VKontakte gateway adapter. Этот проект даёт polling-only мост: он читает непрочитанные сообщения сообщества VK, запускает Hermes CLI в one-shot режиме и отвечает через `messages.send`.

### Возможности

- Polling через `messages.getConversations(filter=unread)` — не нужен публичный webhook, Callback API, ngrok или localhost.run.
- Гибридная маршрутизация:
  - простой чат идёт через быстрый `clarify` toolset;
  - кодинг, файлы, актуальный поиск, интеграции, скриншоты и медиа идут через full toolset с `terminal`, `file`, `web`, `browser`, `vision` и т.д.
- Раздельные дневные Hermes-сессии:
  `vk_<peer_id>_<YYYYMMDD>_quick` и `vk_<peer_id>_<YYYYMMDD>_full`.
- Опциональный `--yolo` режим для Hermes CLI (по умолчанию выключен для безопасности).
- Вложения VK скачиваются локально и передаются Hermes как пути к файлам.
- Ответы с `MEDIA:/path/to/file` загружаются обратно в VK как фото или документы.
- `/trace on` / `/trace off` для прогресса обработки прямо в VK-чате.
- Подключение пользователей по коду: `/approve <код>`.
- Watchdog-скрипт следит, чтобы был запущен один процесс моста.
- Без внешних Python-зависимостей — только стандартная библиотека.

### Требования

- Python 3.10+
- Hermes Agent CLI доступен как `hermes`
- Токен сообщества VK с правами на сообщения
- Сообщество VK с включёнными сообщениями

### Инструкция подключения

#### 1. Подготовить сообщество VK

1. Создай или выбери сообщество VK.
2. Включи сообщения сообщества:
   - Управление сообществом → Сообщения → Сообщения сообщества → Включены.
3. Создай токен сообщества:
   - Управление сообществом → Настройки / Работа с API → Ключи доступа.
   - Создай токен с правами на сообщения.
4. Скопируй токен. Он понадобится как `VK_GROUP_TOKEN`.

Мост работает через polling, поэтому **не нужны** Callback API, ngrok, localhost.run или публичный URL.

#### 2. Склонировать репозиторий

```bash
git clone https://github.com/bason95/hermes-vk-bridge.git
cd hermes-vk-bridge
```

#### 3. Создать env-файл

```bash
mkdir -p ~/.hermes/scripts
cp .env.example ~/.hermes/scripts/vk_bridge.env
$EDITOR ~/.hermes/scripts/vk_bridge.env
```

Минимальная безопасная конфигурация:

```env
VK_GROUP_TOKEN=vk1.a.your_group_token
VK_ALLOWED_USERS=123456789
VK_ALLOW_ALL_USERS=0
VK_HERMES_AUTO_YOLO=0
```

Подключение пользователей по коду:

```env
VK_APPROVAL_CODE=change-me-long-random-code
```

После этого неизвестный пользователь может отправить в VK:

```text
/approve change-me-long-random-code
```

После успешного approve его VK ID сохранится в state-файле и останется одобренным после перезапуска.

#### 4. Запустить вручную

```bash
python3 scripts/hermes_vk_bridge.py
```

В VK отправь `/status` в диалог с сообществом.

#### 5. Запустить через watchdog

```bash
chmod +x scripts/hermes_vk_ensure.sh
VK_BRIDGE_SCRIPT=$PWD/scripts/hermes_vk_bridge.py scripts/hermes_vk_ensure.sh
```

Пример cron:

```cron
*/5 * * * * VK_BRIDGE_SCRIPT=/opt/hermes-vk-bridge/scripts/hermes_vk_bridge.py /opt/hermes-vk-bridge/scripts/hermes_vk_ensure.sh
```

#### 6. Опционально: systemd service

Если репозиторий лежит в `~/hermes-vk-bridge`:

```bash
mkdir -p ~/.config/systemd/user
cp systemd/hermes-vk-bridge.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now hermes-vk-bridge.service
systemctl --user status hermes-vk-bridge.service
```

### Переменные окружения

- `VK_GROUP_TOKEN` — обязательный токен сообщества VK.
- `VK_ALLOWED_USERS` — список разрешённых VK user ID через запятую.
- `VK_APPROVAL_CODE` — опциональный код приглашения; неизвестные пользователи могут отправить `/approve <код>` и получить постоянный доступ.
- `VK_ALLOW_ALL_USERS=1` — явное включение публичного режима. Без него, без `VK_ALLOWED_USERS` и без успешного approve мост игнорирует сообщения.
- `HERMES_BIN` — путь к Hermes CLI, по умолчанию `hermes`.
- `VK_HERMES_TIMEOUT_SEC` — таймаут Hermes-запроса, по умолчанию `420`.
- `VK_HERMES_QUICK_TOOLSETS` — quick toolset, по умолчанию `clarify`.
- `VK_HERMES_FULL_TOOLSETS` — full toolset для задач с инструментами.
- `VK_HERMES_AUTO_YOLO` — по умолчанию выключен (`0`). Ставь `1` только для доверенной среды.
- `VK_ENV_PATH` — путь к env-файлу, по умолчанию `~/.hermes/scripts/vk_bridge.env`.
- `VK_STATE_PATH` — путь к JSON state-файлу.
- `VK_LOG_PATH` — путь к логу моста.
- `VK_INBOX_DIR` — директория для скачанных вложений VK.

### Команды в VK

- `/help` — краткая справка.
- `/status` — статус маршрутизации/toolsets/сессий.
- `/new` — сбросить quick и full дневной контекст.
- `/trace on` — включить подробный прогресс.
- `/trace off` — выключить подробный прогресс.
- `/approve <код>` — одобрить текущего VK-пользователя, если код совпадает с `VK_APPROVAL_CODE`.

### Standalone bridge vs native plugin

`scripts/hermes_vk_bridge.py` — стабильный standalone-мост.

`plugins/vk-platform/adapter.py` — экспериментальный нативный Hermes Gateway platform adapter. Он маршрутизирует VK-сообщения через обычный Gateway pipeline, но standalone-режим сейчас лучше проверен.

### Безопасность

- Не коммить `vk_bridge.env` и VK-токены.
- Standalone bridge по умолчанию закрыт: используй `VK_ALLOWED_USERS`, приватный `VK_APPROVAL_CODE` или явно `VK_ALLOW_ALL_USERS=1` для публичного демо.
- Держи `VK_HERMES_AUTO_YOLO=0`, если не все пользователи полностью доверенные.
- Любой GitHub/VK токен, отправленный в чат, считай скомпрометированным и отзывай после использования.

## License / Лицензия

MIT
