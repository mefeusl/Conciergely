"""Optional Twilio WhatsApp provider (sandbox testing). Meta is the default."""
from __future__ import annotations

from urllib.parse import urlparse

import requests

from . import config, db
from .errors import PermanentSendError
from .models import Incoming

_client = None
MEDIA_HOST_SUFFIXES = ("twilio.com", "twil.io")
MAX_MEDIA_BYTES = 16 * 1024 * 1024


def _twilio():
    global _client
    if _client is None:
        from twilio.rest import Client  # imported lazily so the app starts without Twilio installed
        _client = Client(config.TWILIO_ACCOUNT_SID, config.TWILIO_AUTH_TOKEN)
    return _client


def validate(url: str, params: dict, signature: str) -> bool:
    from twilio.request_validator import RequestValidator
    return RequestValidator(config.TWILIO_AUTH_TOKEN).validate(url, params, signature or "")


def parse_form(params: dict) -> list[Incoming]:
    sid, sender = params.get("MessageSid", ""), params.get("From", "")
    if not sid or not sender:
        return []
    inc = Incoming(id=sid, phone=db.norm_phone(sender), type="text", text=params.get("Body", ""))
    if params.get("Latitude") and params.get("Longitude"):
        inc.type = "location"
        try:
            inc.lat, inc.lng = float(params["Latitude"]), float(params["Longitude"])
        except ValueError:
            inc.lat = inc.lng = None
        inc.text = " - ".join(x for x in (params.get("Label", ""), params.get("Address", "")) if x)
        return [inc]
    if int(params.get("NumMedia", "0") or 0) > 0:
        mime = params.get("MediaContentType0", "")
        if mime.startswith("audio/"):
            inc.type = "audio"
        elif mime.startswith("image/"):
            inc.type = "image"
        elif mime in ("application/pdf", "text/plain", "text/csv"):
            inc.type = "document"
        else:
            inc.type = "other"
        inc.media_id, inc.mime = params.get("MediaUrl0", ""), mime
    return [inc]


def send_text(phone: str, body: str) -> None:
    _twilio().messages.create(from_=config.TWILIO_WHATSAPP_FROM, to=f"whatsapp:{phone}", body=body)


def send_link_button(phone: str, body: str, label: str, url: str) -> None:
    send_text(phone, f"{body}\n{url}")  # Twilio has no tap-button message; a plain link works


def send_template(phone: str, text: str) -> None:
    raise PermanentSendError("message templates are only supported with the Meta provider")


def send_voice(phone: str, audio: bytes) -> None:
    raise PermanentSendError("voice notes are only supported with the Meta provider")


def send_hello(phone: str) -> None:
    raise PermanentSendError("the hello_world template is only available with the Meta provider")


def download_media(url: str) -> tuple[bytes, str]:
    parsed = urlparse(url or "")
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or not any(host == s or host.endswith("." + s) for s in MEDIA_HOST_SUFFIXES):
        raise PermanentSendError("media URL host not allowed")
    resp = requests.get(url, auth=(config.TWILIO_ACCOUNT_SID, config.TWILIO_AUTH_TOKEN), timeout=60)
    resp.raise_for_status()
    if len(resp.content) > MAX_MEDIA_BYTES:
        raise PermanentSendError("media too large")
    return resp.content, resp.headers.get("Content-Type", "")
