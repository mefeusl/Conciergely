"""Meta WhatsApp Cloud API: webhook signature + parsing, sending, templates, media download."""
from __future__ import annotations

import hashlib
import hmac
import logging
import re
from urllib.parse import urlparse

import requests

from . import config, db
from .errors import PermanentSendError
from .models import Incoming

log = logging.getLogger(__name__)

TIMEOUT = 20
MAX_MEDIA_BYTES = 16 * 1024 * 1024
MEDIA_HOST_SUFFIXES = ("fbsbx.com", "fbcdn.net", "facebook.com", "whatsapp.net")


# ---------- incoming ----------

def verify_signature(raw_body: bytes, header: str) -> bool:
    """Check X-Hub-Signature-256 (HMAC-SHA256 of the raw body, keyed with the app secret)."""
    if not config.META_APP_SECRET:
        return False  # fail closed: without the secret we cannot tell real from fake
    expected = "sha256=" + hmac.new(config.META_APP_SECRET.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header or "")


def parse_payload(data: dict) -> list[Incoming]:
    """Extract user messages from a webhook body. Status updates (sent/read) are ignored."""
    out: list[Incoming] = []
    if not isinstance(data, dict) or data.get("object") != "whatsapp_business_account":
        return out
    for entry in data.get("entry") or []:
        for change in (entry or {}).get("changes") or []:
            value = (change or {}).get("value") or {}
            for m in value.get("messages") or []:
                if not isinstance(m, dict) or not m.get("id") or not m.get("from"):
                    continue
                kind = m.get("type")
                inc = Incoming(id=str(m["id"]), phone=db.norm_phone(str(m["from"])), type="other")
                if kind == "text":
                    inc.type, inc.text = "text", str((m.get("text") or {}).get("body", ""))
                elif kind == "audio":
                    audio = m.get("audio") or {}
                    inc.type, inc.media_id, inc.mime = "audio", str(audio.get("id", "")), str(audio.get("mime_type", ""))
                elif kind == "image":
                    img = m.get("image") or {}
                    inc.type, inc.media_id = "image", str(img.get("id", ""))
                    inc.mime, inc.text = str(img.get("mime_type", "")), str(img.get("caption", ""))
                elif kind == "document":
                    doc = m.get("document") or {}
                    inc.type, inc.media_id = "document", str(doc.get("id", ""))
                    inc.mime, inc.text = str(doc.get("mime_type", "")), str(doc.get("caption", ""))
                    inc.filename = str(doc.get("filename", ""))
                elif kind == "location":
                    loc = m.get("location") or {}
                    inc.type = "location"
                    inc.lat, inc.lng = loc.get("latitude"), loc.get("longitude")
                    inc.text = " - ".join(str(x) for x in (loc.get("name"), loc.get("address")) if x)
                elif kind == "interactive":
                    reply = (m.get("interactive") or {})
                    picked = reply.get("button_reply") or reply.get("list_reply") or {}
                    inc.type, inc.text = "text", str(picked.get("title", ""))
                elif kind == "button":
                    inc.type, inc.text = "text", str((m.get("button") or {}).get("text", ""))
                inc.forwarded = bool(((m.get("context") or {}).get("forwarded") or (m.get("context") or {}).get("frequently_forwarded")))
                out.append(inc)
    return out


# ---------- outgoing ----------

def _url(path: str) -> str:
    return f"https://graph.facebook.com/{config.META_API_VERSION}/{path}"


def _headers() -> dict:
    return {"Authorization": f"Bearer {config.META_ACCESS_TOKEN}"}


def _raise_for(resp: requests.Response) -> None:
    if resp.status_code < 400:
        return
    try:
        err = resp.json().get("error", {})
    except ValueError:
        err = {}
    msg = f"Meta API {resp.status_code} code={err.get('code')} {err.get('message', '')}"[:300]
    if resp.status_code == 401 or err.get("code") == 190:
        log.error("Meta access token is invalid or expired: generate a new one and update .env")
    if resp.status_code == 429 or resp.status_code >= 500:
        raise RuntimeError(msg)  # worth retrying
    raise PermanentSendError(msg)  # 4xx: retrying will not help


def _post(body: dict) -> None:
    if not config.META_ACCESS_TOKEN or not config.META_PHONE_NUMBER_ID:
        raise PermanentSendError("META_ACCESS_TOKEN / META_PHONE_NUMBER_ID are not configured")
    resp = requests.post(_url(f"{config.META_PHONE_NUMBER_ID}/messages"), headers=_headers(),
                         json=body, timeout=TIMEOUT)
    _raise_for(resp)


def _to(phone: str) -> str:
    return phone.lstrip("+")


def send_text(phone: str, body: str) -> None:
    _post({"messaging_product": "whatsapp", "to": _to(phone), "type": "text",
           "text": {"body": body, "preview_url": True}})


def template_param(text: str) -> str:
    """Template variables may not contain new lines, tabs or runs of 4+ spaces."""
    flat = re.sub(r"\s*[\r\n]+\s*", " | ", text.strip())
    flat = re.sub(r"[\t ]{2,}", " ", flat)
    return flat[:1000]


def send_template(phone: str, text: str) -> None:
    """Send the approved template (config.META_TEMPLATE_NAME) with `text` as its single variable."""
    _post({
        "messaging_product": "whatsapp", "to": _to(phone), "type": "template",
        "template": {
            "name": config.META_TEMPLATE_NAME,
            "language": {"code": config.META_TEMPLATE_LANG},
            "components": [{"type": "body", "parameters": [{"type": "text", "text": template_param(text)}]}],
        },
    })


def send_typing(message_id: str) -> None:
    """Mark the client's message as read and show 'typing...' (clears after 25 s or when we reply).

    Cosmetic, so it never raises: a failure here must not stop the real reply.
    """
    if not message_id or not config.META_ACCESS_TOKEN or not config.META_PHONE_NUMBER_ID:
        return
    try:
        requests.post(_url(f"{config.META_PHONE_NUMBER_ID}/messages"), headers=_headers(), timeout=5,
                      json={"messaging_product": "whatsapp", "status": "read", "message_id": message_id,
                            "typing_indicator": {"type": "text"}})
    except requests.RequestException as exc:
        log.debug("typing indicator failed: %s", type(exc).__name__)


def send_link_button(phone: str, body: str, label: str, url: str) -> None:
    """A message with one tap button that opens a link (works within the 24 h window)."""
    _post({"messaging_product": "whatsapp", "to": _to(phone), "type": "interactive",
           "interactive": {"type": "cta_url", "body": {"text": body[:1000]},
                           "action": {"name": "cta_url",
                                      "parameters": {"display_text": label[:20], "url": url}}}})


def send_voice(phone: str, audio: bytes) -> None:
    """Upload Ogg/Opus audio and send it as a voice note (works within the 24 h window)."""
    if not config.META_ACCESS_TOKEN or not config.META_PHONE_NUMBER_ID:
        raise PermanentSendError("META_ACCESS_TOKEN / META_PHONE_NUMBER_ID are not configured")
    up = requests.post(_url(f"{config.META_PHONE_NUMBER_ID}/media"), headers=_headers(), timeout=60,
                       data={"messaging_product": "whatsapp", "type": "audio/ogg"},
                       files={"file": ("briefing.ogg", audio, "audio/ogg")})
    _raise_for(up)
    media_id = str(up.json().get("id", ""))
    if not media_id:
        raise PermanentSendError("Meta did not return a media id for the voice note")
    _post({"messaging_product": "whatsapp", "to": _to(phone), "type": "audio", "audio": {"id": media_id}})


def send_hello(phone: str) -> None:
    """Meta's built-in 'hello_world' template: works on a number that has never written to you."""
    _post({"messaging_product": "whatsapp", "to": _to(phone), "type": "template",
           "template": {"name": "hello_world", "language": {"code": "en_US"}}})


def download_media(media_id: str) -> tuple[bytes, str]:
    """Download a voice note. Returns (bytes, mime type)."""
    if not re.fullmatch(r"\d{1,40}", media_id or ""):
        raise PermanentSendError("invalid media id")
    meta = requests.get(_url(media_id), headers=_headers(), timeout=TIMEOUT)
    _raise_for(meta)
    info = meta.json()
    url = str(info.get("url", ""))
    host = (urlparse(url).hostname or "").lower()
    if urlparse(url).scheme != "https" or not any(host == s or host.endswith("." + s) for s in MEDIA_HOST_SUFFIXES):
        raise PermanentSendError("media URL host not allowed")
    resp = requests.get(url, headers=_headers(), timeout=60, stream=True)
    _raise_for(resp)
    data = b""
    for chunk in resp.iter_content(65536):
        data += chunk
        if len(data) > MAX_MEDIA_BYTES:
            raise PermanentSendError("media too large")
    return data, str(info.get("mime_type") or resp.headers.get("Content-Type", ""))
