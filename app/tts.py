"""Text to speech (OpenAI) for voice notes. WhatsApp voice notes are Ogg/Opus, which OpenAI returns as 'opus'."""
from __future__ import annotations

import requests

from . import config

MAX_CHARS = 900  # roughly a minute of speech
MAX_BYTES = 8 * 1024 * 1024


def available() -> bool:
    return bool(config.OPENAI_API_KEY)


def speak(text: str, fmt: str = "opus", lang: str = "") -> bytes | None:
    """Audio bytes (Ogg/Opus by default; fmt="mp3" for browsers) for the text, or None when there is no key or nothing to say. Errors raise."""
    text = " ".join((text or "").split())[:MAX_CHARS]
    if not available() or not text:
        return None
    body = {"model": config.TTS_MODEL, "voice": config.TTS_VOICE, "input": text, "response_format": fmt}
    style = {"tr": config.TTS_INSTRUCTIONS_TR, "en": config.TTS_INSTRUCTIONS_EN}.get(lang, "")
    if style and config.TTS_MODEL.startswith("gpt-4o"):  # only the newer voice models take a speaking-style instruction
        body["instructions"] = style[:600]
    resp = requests.post(
        "https://api.openai.com/v1/audio/speech",
        headers={"Authorization": f"Bearer {config.OPENAI_API_KEY}"},
        json=body,
        timeout=90,
    )
    resp.raise_for_status()
    audio = resp.content
    if not audio or len(audio) > MAX_BYTES:
        return None
    return audio
