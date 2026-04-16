from __future__ import annotations

import hashlib
import hmac

from pulse.core.channel import FeishuChannelAdapter, verify_feishu_signature


def test_feishu_parse_incoming_supports_text_content_json() -> None:
    adapter = FeishuChannelAdapter()
    message = adapter.parse_incoming(
        {
            "event": {
                "sender": {"sender_id": {"open_id": "ou_123"}},
                "message": {
                    "message_id": "om_1",
                    "chat_id": "oc_1",
                    "message_type": "text",
                    "content": '{"text":"你好 Pulse"}',
                },
            }
        }
    )
    assert message is not None
    assert message.channel == "feishu"
    assert message.user_id == "ou_123"
    assert message.text == "你好 Pulse"


def test_verify_feishu_signature_matches_hmac() -> None:
    secret = "demo-secret"
    timestamp = "1700000000"
    nonce = "abc123"
    body = '{"event":"ping"}'
    payload = f"{timestamp}{nonce}{body}".encode("utf-8")
    sign = hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()

    assert (
        verify_feishu_signature(
            secret=secret,
            timestamp=timestamp,
            nonce=nonce,
            body=body,
            signature=sign,
        )
        is True
    )
    assert (
        verify_feishu_signature(
            secret=secret,
            timestamp=timestamp,
            nonce=nonce,
            body=body,
            signature="bad-sign",
        )
        is False
    )
