"""Small shared data types."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Incoming:
    """One user message, normalised across WhatsApp providers."""
    id: str
    phone: str
    type: str = "text"  # "text" | "audio" | "image" | "document" | "location" | "other"
    text: str = ""  # message text, or the caption of a photo / document
    media_id: str = ""  # Meta media id, or the media URL for Twilio
    mime: str = ""
    filename: str = ""
    lat: float | None = None  # shared location pin (type "location")
    lng: float | None = None
    forwarded: bool = False  # the client forwarded this message from someone else
