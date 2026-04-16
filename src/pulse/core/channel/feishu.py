from __future__ import annotations

import hashlib
import hmac
import json
import os
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Any

from .base import BaseChannelAdapter, IncomingMessage, OutgoingMessage


def verify_feishu_signature(
    *,
    secret: str,
    timestamp: str,
    nonce: str,
    body: str,
    signature: str,
) -> bool:
    if not secret or not signature:
        return False
    payload = f"{timestamp}{nonce}{body}".encode("utf-8")
    digest = hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()
    return hmac.compare_digest(digest, signature.strip())


class FeishuChannelAdapter(BaseChannelAdapter):
    name = "feishu"

    def parse_incoming(self, payload: Any) -> IncomingMessage | None:
        if not isinstance(payload, dict):
            return None
        event = payload.get("event")
        if not isinstance(event, dict):
            return None
        message = event.get("message")
        sender = event.get("sender")
        if not isinstance(message, dict) or not isinstance(sender, dict):
            return None

        sender_id = (
            sender.get("sender_id", {}).get("open_id")
            if isinstance(sender.get("sender_id"), dict)
            else None
        )
        user_id = str(sender_id or sender.get("sender_id") or "").strip()
        if not user_id:
            return None

        content_raw = message.get("content")
        text = ""
        if isinstance(content_raw, str):
            try:
                content_json = json.loads(content_raw)
            except Exception:
                content_json = None
            if isinstance(content_json, dict):
                text = str(content_json.get("text") or "").strip()
            if not text:
                text = content_raw.strip()
        elif isinstance(content_raw, dict):
            text = str(content_raw.get("text") or "").strip()

        if not text:
            return None

        return IncomingMessage(
            channel=self.name,
            user_id=user_id,
            text=text,
            metadata={
                "chat_id": message.get("chat_id"),
                "message_id": message.get("message_id"),
                "message_type": message.get("message_type"),
            },
            received_at=datetime.now(timezone.utc),
        )

    def send(self, message: OutgoingMessage) -> None:
        webhook = os.getenv("FEISHU_WEBHOOK_URL", "").strip()
        if not webhook:
            return
        payload = {"msg_type": "text", "content": {"text": message.text}}
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            webhook,
            data=body,
            headers={"Content-Type": "application/json; charset=utf-8"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=10.0):
                return
        except urllib.error.URLError:
            return
