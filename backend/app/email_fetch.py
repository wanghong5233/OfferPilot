from __future__ import annotations

import imaplib
import os
from dataclasses import dataclass
from datetime import datetime
from email import message_from_bytes
from email.header import decode_header
from email.message import Message
from email.utils import parsedate_to_datetime, parseaddr


@dataclass
class FetchedEmail:
    sender: str
    subject: str
    body: str
    received_at: datetime | None
    uid: str


def _imap_config() -> tuple[str, int, str, str]:
    host = os.getenv("IMAP_HOST", "").strip()
    user = os.getenv("IMAP_USER", "").strip()
    password = os.getenv("IMAP_PASSWORD", "").strip()
    port_raw = os.getenv("IMAP_PORT", "993").strip()
    if not host or not user or not password:
        raise RuntimeError("IMAP config missing: set IMAP_HOST/IMAP_USER/IMAP_PASSWORD")
    try:
        port = int(port_raw)
    except ValueError:
        port = 993
    return host, port, user, password


def _decode_header_value(raw: str | None) -> str:
    if not raw:
        return ""
    chunks = decode_header(raw)
    parts: list[str] = []
    for value, encoding in chunks:
        if isinstance(value, bytes):
            enc = encoding or "utf-8"
            try:
                parts.append(value.decode(enc, errors="ignore"))
            except Exception:
                parts.append(value.decode("utf-8", errors="ignore"))
        else:
            parts.append(value)
    return "".join(parts).strip()


def _extract_text_body(msg: Message) -> str:
    if msg.is_multipart():
        texts: list[str] = []
        for part in msg.walk():
            if part.get_content_maintype() == "multipart":
                continue
            content_type = part.get_content_type()
            if content_type != "text/plain":
                continue
            payload = part.get_payload(decode=True)
            if payload is None:
                continue
            charset = part.get_content_charset() or "utf-8"
            try:
                texts.append(payload.decode(charset, errors="ignore"))
            except Exception:
                texts.append(payload.decode("utf-8", errors="ignore"))
        return "\n".join(texts).strip()

    payload = msg.get_payload(decode=True)
    if payload is None:
        return ""
    charset = msg.get_content_charset() or "utf-8"
    try:
        return payload.decode(charset, errors="ignore").strip()
    except Exception:
        return payload.decode("utf-8", errors="ignore").strip()


def fetch_unread_emails(*, max_items: int = 10, mark_seen: bool = False) -> list[FetchedEmail]:
    host, port, user, password = _imap_config()
    safe_max = max(1, min(max_items, 50))
    mailbox = None
    try:
        mailbox = imaplib.IMAP4_SSL(host, port)
        mailbox.login(user, password)
        status, _ = mailbox.select("INBOX")
        if status != "OK":
            raise RuntimeError("Failed to select INBOX")

        status, data = mailbox.search(None, "UNSEEN")
        if status != "OK":
            raise RuntimeError("Failed to search unread emails")

        ids = data[0].split() if data and data[0] else []
        if not ids:
            return []
        ids = ids[-safe_max:]

        results: list[FetchedEmail] = []
        for raw_id in ids:
            email_uid = raw_id.decode("utf-8", errors="ignore")
            status, fetched = mailbox.fetch(raw_id, "(RFC822)")
            if status != "OK" or not fetched or not isinstance(fetched[0], tuple):
                continue
            raw_bytes = fetched[0][1]
            if not isinstance(raw_bytes, (bytes, bytearray)):
                continue
            msg = message_from_bytes(raw_bytes)

            raw_sender = _decode_header_value(msg.get("From"))
            sender_addr = parseaddr(raw_sender)[1] or raw_sender
            subject = _decode_header_value(msg.get("Subject"))
            body = _extract_text_body(msg)
            received_at = None
            if msg.get("Date"):
                try:
                    received_at = parsedate_to_datetime(msg.get("Date"))
                    if received_at and received_at.tzinfo is not None:
                        received_at = received_at.astimezone().replace(tzinfo=None)
                except Exception:
                    received_at = None

            if mark_seen:
                try:
                    mailbox.store(raw_id, "+FLAGS", "\\Seen")
                except Exception:
                    pass

            results.append(
                FetchedEmail(
                    sender=sender_addr.strip() or "unknown@example.com",
                    subject=subject or "(no subject)",
                    body=body or "",
                    received_at=received_at,
                    uid=email_uid,
                )
            )
        return results
    except imaplib.IMAP4.error as exc:
        raise RuntimeError(f"IMAP error: {exc}") from exc
    finally:
        if mailbox is not None:
            try:
                mailbox.close()
            except Exception:
                pass
            try:
                mailbox.logout()
            except Exception:
                pass
