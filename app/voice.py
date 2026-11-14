"""Voice note transcription (WhatsApp voice notes arrive as audio/ogg, opus)."""
from __future__ import annotations

import logging

import requests

from . import config

log = logging.getLogger(__name__)

_EXT = {
    "audio/ogg": "ogg",
    "audio/mpeg": "mp3",
    "audio/mp4": "m4a",
    "audio/aac": "m4a",
    "audio/amr": "amr",
    "audio/webm": "webm",
}


def available() -> bool:
    return bool(config.OPENAI_API_KEY)


_fallback = False  # set when the account cannot use TRANSCRIBE_MODEL: whisper-1 is used until restart
BASE_HINT = "Konuşma Türkçe veya İngilizce. Borsa İstanbul, hisse, hatırlat, dolar, euro, altın."


def hint_for(client: dict | None) -> str:
    """A short vocabulary prompt (name, followed stocks) so the transcriber spells the client's own words right."""
    if not client:
        return BASE_HINT
    parts = [BASE_HINT]
    name = " ".join(str(client.get("name") or "").split())[:40]
    if name:
        parts.append(f"Ad: {name}.")
    try:
        from . import db

        symbols = [w["symbol"].split(".")[0] for w in db.list_watch(client["phone"])[:12]]
    except Exception:
        symbols = []
    if symbols:
        parts.append("Hisseler: " + ", ".join(symbols) + ".")
    return " ".join(parts)[:400]


def _post(model: str, audio: bytes, mime: str, ext: str, language: str | None, hint: str | None):
    data = {"model": model, "response_format": "json"}
    if language:
        data["language"] = language
    if hint:
        data["prompt"] = hint
    return requests.post(
        "https://api.openai.com/v1/audio/transcriptions",
        headers={"Authorization": f"Bearer {config.OPENAI_API_KEY}"},
        files={"file": (f"voice.{ext}", audio, mime)},
        data=data,
        timeout=90,
    )


def transcribe(audio: bytes, content_type: str, client: dict | None = None) -> str | None:
    """Return the transcript, or None if no key is configured or the audio has no speech.

    Uses TRANSCRIBE_MODEL with a vocabulary hint for this client. If the account cannot use that model, whisper-1 takes over.
    Network/API errors raise, so the queue retries the job.
    """
    global _fallback
    if not available():
        return None
    mime = (content_type or "audio/ogg").split(";")[0].strip().lower()
    ext = _EXT.get(mime, "ogg")
    language = (client or {}).get("lang") if config.TRANSCRIBE_LANG_HINT and (client or {}).get("lang") in ("tr", "en") else None
    hint = hint_for(client)
    model = "whisper-1" if _fallback else config.TRANSCRIBE_MODEL
    resp = _post(model, audio, mime, ext, language, hint)
    if resp.status_code in (400, 404) and model != "whisper-1":
        log.warning("transcription model %s refused (%s); using whisper-1 until restart", model, resp.status_code)
        _fallback = True
        resp = _post("whisper-1", audio, mime, ext, language, hint)
    resp.raise_for_status()
    text = (resp.json().get("text") or "").strip()
    return text or None
