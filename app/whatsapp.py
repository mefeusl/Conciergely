"""WhatsApp transport facade. Everything else in the app talks to this file only.

WHATSAPP_PROVIDER picks Meta (default) or Twilio. DRY_RUN=1 records messages in OUTBOX instead of
sending, which is how the tests (and offline demos) check what the bot would say.
"""
from __future__ import annotations

import logging

from . import config, db
from .errors import PermanentSendError  # re-exported for convenience
from .models import Incoming  # noqa: F401  (re-exported)

log = logging.getLogger(__name__)

OUTBOX: list[tuple[str, str]] = []
TYPING: list[str] = []  # message ids we showed 'typing...' for (DRY_RUN only)
MAX_CHUNK = 1500

__all__ = ["send_text", "send_template", "send_hello", "send_link_button", "send_voice", "typing", "download_media",
           "chunk", "PermanentSendError", "OUTBOX", "TYPING"]


def _provider():
    if config.WHATSAPP_PROVIDER == "twilio":
        from . import wa_twilio
        return wa_twilio
    from . import wa_meta
    return wa_meta


def chunk(text: str, size: int = MAX_CHUNK) -> list[str]:
    """Split long replies on line/space boundaries so each WhatsApp message stays readable."""
    text = (text or "").strip()
    parts: list[str] = []
    while len(text) > size:
        cut = text.rfind("\n", 0, size)
        if cut < size // 2:
            cut = text.rfind(" ", 0, size)
        if cut < size // 2:
            cut = size
        parts.append(text[:cut].strip())
        text = text[cut:].strip()
    if text:
        parts.append(text)
    return parts


def send_text(phone: str, body: str) -> None:
    """Send a free-form message (only allowed within 24 h of the client's last message)."""
    for part in chunk(body):
        if config.DRY_RUN:
            log.info("[DRY_RUN] text to %s (%d chars)", db.last4(phone), len(part))
            OUTBOX.append((phone, part))
        else:
            _provider().send_text(phone, part)


def send_link_button(phone: str, body: str, label: str, url: str) -> None:
    """One message with a tap button that opens `url` (within the 24 h window)."""
    if config.DRY_RUN:
        OUTBOX.append((phone, f"{body} [BUTTON {label} -> {url}]"))
        return
    _provider().send_link_button(phone, body, label, url)


def typing(message_id: str) -> None:
    """Show 'typing...' to the client while we work on their message. Never raises."""
    if not message_id:
        return
    if config.DRY_RUN:
        TYPING.append(message_id)
        return
    try:
        fn = getattr(_provider(), "send_typing", None)
        if fn:
            fn(message_id)
    except Exception:
        log.debug("typing indicator skipped", exc_info=True)


def send_template(phone: str, text: str) -> None:
    """Send the approved template with `text` as its variable (works outside the 24 h window)."""
    if config.DRY_RUN:
        OUTBOX.append((phone, "[TEMPLATE] " + text))
        return
    _provider().send_template(phone, text)


def send_voice(phone: str, audio: bytes) -> None:
    """Send Ogg/Opus audio as a voice note (within the 24 h window)."""
    if config.DRY_RUN:
        OUTBOX.append((phone, f"[VOICE {len(audio)} bytes]"))
        return
    _provider().send_voice(phone, audio)


def send_hello(phone: str) -> None:
    if config.DRY_RUN:
        OUTBOX.append((phone, "[HELLO TEMPLATE]"))
        return
    _provider().send_hello(phone)


def download_media(ref: str) -> tuple[bytes, str]:
    return _provider().download_media(ref)
