"""Channel ingress abstractions and adapters."""

from .base import BaseChannelAdapter, IncomingMessage, OutgoingMessage
from .cli import CliChannelAdapter
from .feishu import FeishuChannelAdapter, verify_feishu_signature

__all__ = [
    "BaseChannelAdapter",
    "IncomingMessage",
    "OutgoingMessage",
    "CliChannelAdapter",
    "FeishuChannelAdapter",
    "verify_feishu_signature",
]
