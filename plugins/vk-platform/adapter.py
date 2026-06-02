"""VK polling platform adapter for Hermes Gateway.

This adapter makes VK behave like Telegram/Discord in Hermes: inbound messages
are normalized to MessageEvent and passed to BasePlatformAdapter.handle_message,
so the normal gateway pipeline handles sessions, tools, progress, /stop, /new,
approvals, memory, media extraction, and final delivery.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, Optional

from gateway.config import Platform, PlatformConfig
from gateway.platforms.base import BasePlatformAdapter, MessageEvent, MessageType, SendResult

logger = logging.getLogger(__name__)

VK_API_VERSION = "5.199"
MAX_VK_MSG = 3500
STATE_PATH = Path.home() / ".hermes/state/vk_gateway_adapter_state.json"
LEGACY_ENV_PATH = Path.home() / ".hermes/scripts/vk_bridge.env"


def _load_legacy_env() -> None:
    """Load ~/.hermes/scripts/vk_bridge.env if VK vars are not already in env."""
    if not LEGACY_ENV_PATH.exists():
        return
    try:
        for raw in LEGACY_ENV_PATH.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value
    except Exception as exc:
        logger.warning("[VK] failed to load legacy env %s: %s", LEGACY_ENV_PATH, exc)


def _vk_api_sync(method: str, payload: Dict[str, Any], token: str) -> Any:
    data = urllib.parse.urlencode({**payload, "access_token": token, "v": VK_API_VERSION}).encode()
    req = urllib.request.Request(
        "https://api.vk.com/method/" + method,
        data=data,
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as response:
        body = response.read().decode("utf-8", "replace")
    parsed = json.loads(body)
    if "error" in parsed:
        raise RuntimeError(f"VK API {method}: {parsed['error']}")
    return parsed.get("response")


def _split_text(text: str) -> list[str]:
    text = (text or "").strip()
    if not text:
        return ["(пустой ответ)"]
    if len(text) <= MAX_VK_MSG:
        return [text]
    parts: list[str] = []
    rest = text
    while len(rest) > MAX_VK_MSG:
        cut = rest.rfind("\n", 0, MAX_VK_MSG)
        if cut < MAX_VK_MSG // 2:
            cut = rest.rfind(" ", 0, MAX_VK_MSG)
        if cut < MAX_VK_MSG // 2:
            cut = MAX_VK_MSG
        parts.append(rest[:cut].strip())
        rest = rest[cut:].strip()
    if rest:
        parts.append(rest)
    return parts


def check_requirements() -> bool:
    _load_legacy_env()
    return bool(os.getenv("VK_GROUP_TOKEN"))


def validate_config(config: PlatformConfig) -> bool:
    _load_legacy_env()
    return bool(config.token or config.extra.get("token") or os.getenv("VK_GROUP_TOKEN"))


def is_connected(config: PlatformConfig) -> bool:
    return validate_config(config)


class VKAdapter(BasePlatformAdapter):
    """Polling VK adapter that routes messages through Hermes Gateway."""

    MAX_MESSAGE_LENGTH = MAX_VK_MSG
    SUPPORTS_MESSAGE_EDITING = True

    def __init__(self, config: PlatformConfig):
        _load_legacy_env()
        super().__init__(config, Platform("vk"))
        self.token = config.token or config.extra.get("token") or os.getenv("VK_GROUP_TOKEN", "")
        self.poll_interval = float(config.extra.get("poll_interval", os.getenv("VK_POLL_INTERVAL", "2.5")))
        self._poll_task: Optional[asyncio.Task] = None
        self._seen_ids: set[int] = set()
        self._load_state()

    def _load_state(self) -> None:
        try:
            data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
            self._seen_ids = set(int(x) for x in data.get("seen_ids", [])[-5000:])
        except Exception:
            self._seen_ids = set()

    def _save_state(self) -> None:
        try:
            STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
            data = {"seen_ids": list(sorted(self._seen_ids))[-5000:], "ts": int(time.time())}
            tmp = STATE_PATH.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            tmp.replace(STATE_PATH)
        except Exception as exc:
            logger.debug("[VK] state save failed: %s", exc)

    async def _vk_api(self, method: str, payload: Dict[str, Any]) -> Any:
        return await asyncio.to_thread(_vk_api_sync, method, payload, self.token)

    async def connect(self) -> bool:
        if not self.token:
            self._set_fatal_error("missing_token", "VK_GROUP_TOKEN is not configured", retryable=False)
            return False
        if not self._acquire_platform_lock("vk_gateway", "default", "VK gateway adapter"):
            return False
        self._mark_connected()
        self._poll_task = asyncio.create_task(self._poll_loop(), name="vk-poll-loop")
        logger.info("[VK] adapter connected (poll_interval=%.1fs)", self.poll_interval)
        return True

    async def disconnect(self) -> None:
        self._running = False
        if self._poll_task:
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass
        self._release_platform_lock()
        self._mark_disconnected()

    async def _poll_loop(self) -> None:
        backoff = self.poll_interval
        while self._running:
            try:
                await self._poll_once()
                backoff = self.poll_interval
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("[VK] poll error: %s", exc)
                await asyncio.sleep(min(backoff, 30.0))
                backoff = min(backoff * 1.7, 30.0)
                continue
            await asyncio.sleep(self.poll_interval)

    async def _poll_once(self) -> None:
        resp = await self._vk_api("messages.getConversations", {"count": "20", "filter": "unread"})
        for item in (resp or {}).get("items", []):
            msg = item.get("last_message") or {}
            if msg.get("out") == 1:
                continue
            msg_id = int(msg.get("id") or 0)
            peer_id = int(msg.get("peer_id") or 0)
            from_id = int(msg.get("from_id") or peer_id or 0)
            text = (msg.get("text") or "").strip()
            if msg_id <= 0 or peer_id <= 0:
                continue
            if msg_id in self._seen_ids:
                continue
            self._seen_ids.add(msg_id)
            self._save_state()

            chat_type = "group" if peer_id >= 2_000_000_000 else "dm"
            source = self.build_source(
                chat_id=str(peer_id),
                user_id=str(from_id),
                user_name=str(from_id),
                chat_name=str(peer_id),
                chat_type=chat_type,
            )
            event = MessageEvent(
                text=text,
                message_type=MessageType.COMMAND if text.startswith("/") else MessageType.TEXT,
                source=source,
                raw_message=msg,
                message_id=str(msg_id),
            )
            logger.info("[VK] inbound peer=%s from=%s msg_id=%s text=%r", peer_id, from_id, msg_id, text[:80])
            await self.handle_message(event)
            try:
                await self._vk_api("messages.markAsRead", {"peer_id": str(peer_id)})
            except Exception as exc:
                logger.debug("[VK] markAsRead failed peer=%s: %s", peer_id, exc)

    async def send(self, chat_id: str, content: str, reply_to: Optional[str] = None, metadata: Optional[Dict[str, Any]] = None) -> SendResult:
        del reply_to, metadata
        try:
            first_message_id: Optional[str] = None
            raw = None
            base = random.randint(1, 2_000_000_000)
            for i, part in enumerate(_split_text(content)):
                raw = await self._vk_api(
                    "messages.send",
                    {
                        "peer_id": str(chat_id),
                        "random_id": str((base + i) % 2_147_483_647),
                        "message": part,
                    },
                )
                if first_message_id is None:
                    first_message_id = str(raw)
            return SendResult(success=True, message_id=first_message_id, raw_response=raw)
        except Exception as exc:
            logger.warning("[VK] send failed chat=%s: %s", chat_id, exc)
            return SendResult(success=False, error=str(exc), retryable=True)

    async def edit_message(self, chat_id: str, message_id: str, content: str, *, finalize: bool = False) -> SendResult:
        del finalize
        try:
            # VK cannot edit a multi-part progress bubble cleanly; keep the first
            # MAX_VK_MSG chars. Final answers still go through send() chunking.
            text = (content or "")[:MAX_VK_MSG]
            raw = await self._vk_api(
                "messages.edit",
                {"peer_id": str(chat_id), "message_id": str(message_id), "message": text},
            )
            return SendResult(success=bool(raw), message_id=str(message_id), raw_response=raw)
        except Exception as exc:
            logger.debug("[VK] edit failed chat=%s msg=%s: %s", chat_id, message_id, exc)
            return SendResult(success=False, error=str(exc), retryable=True)

    async def send_typing(self, chat_id: str, metadata: Optional[Dict[str, Any]] = None) -> None:
        del metadata
        try:
            await self._vk_api("messages.setActivity", {"peer_id": str(chat_id), "type": "typing"})
        except Exception:
            pass

    async def get_chat_info(self, chat_id: str) -> Dict[str, Any]:
        return {"name": str(chat_id), "type": "dm" if int(chat_id) < 2_000_000_000 else "group", "chat_id": str(chat_id)}


def register(ctx) -> None:
    ctx.register_platform(
        name="vk",
        label="VKontakte",
        adapter_factory=lambda cfg: VKAdapter(cfg),
        check_fn=check_requirements,
        validate_config=validate_config,
        is_connected=is_connected,
        required_env=["VK_GROUP_TOKEN"],
        allowed_users_env="VK_ALLOWED_USERS",
        allow_all_env="VK_ALLOW_ALL_USERS",
        max_message_length=MAX_VK_MSG,
        emoji="VK",
        platform_hint=(
            "You are chatting via VKontakte (VK). VK messages support plain text "
            "and simple links; markdown rendering is limited. Keep formatting clean, "
            "avoid large tables, and prefer concise bullet lists."
        ),
    )
