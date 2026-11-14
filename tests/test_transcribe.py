"""Transcription: better model, vocabulary hint, fallback to whisper-1."""
import pytest

from app import config, db, voice
from conftest import PHONE


class Resp:
    def __init__(self, status=200, text="merhaba"):
        self.status_code, self._text = status, text

    def json(self):
        return {"text": self._text}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"http {self.status_code}")


@pytest.fixture(autouse=True)
def setup(monkeypatch):
    monkeypatch.setattr(config, "OPENAI_API_KEY", "k")
    monkeypatch.setattr(config, "TRANSCRIBE_MODEL", "gpt-4o-transcribe")
    monkeypatch.setattr(config, "TRANSCRIBE_LANG_HINT", False)
    monkeypatch.setattr(voice, "_fallback", False)
    calls = []

    def post(model, audio, mime, ext, language, hint):
        calls.append({"model": model, "language": language, "hint": hint})
        return Resp()
    monkeypatch.setattr(voice, "_post", post)
    return calls


def test_uses_the_better_model_with_a_vocabulary_hint(client, setup):
    db.add_watch(PHONE, "THYAO.IS", 320.0, None, None)
    assert voice.transcribe(b"x", "audio/ogg", client) == "merhaba"
    call = setup[0]
    assert call["model"] == "gpt-4o-transcribe" and call["language"] is None
    assert "THYAO" in call["hint"] and "Test Client" in call["hint"] and "Borsa" in call["hint"]


def test_language_hint_is_opt_in(client, setup, monkeypatch):
    voice.transcribe(b"x", "audio/ogg", client)
    assert setup[-1]["language"] is None
    monkeypatch.setattr(config, "TRANSCRIBE_LANG_HINT", True)
    voice.transcribe(b"x", "audio/ogg", {**client, "lang": "tr"})
    assert setup[-1]["language"] == "tr"


def test_works_without_a_client_and_without_a_key(setup, monkeypatch):
    assert voice.transcribe(b"x", "audio/ogg") == "merhaba" and setup[0]["hint"] == voice.BASE_HINT
    monkeypatch.setattr(config, "OPENAI_API_KEY", "")
    assert voice.transcribe(b"x", "audio/ogg") is None


def test_falls_back_to_whisper_if_the_model_is_refused_and_remembers(setup, monkeypatch):
    seen = []

    def post(model, *a):
        seen.append(model)
        return Resp(404) if model != "whisper-1" else Resp(200, "tamam")
    monkeypatch.setattr(voice, "_post", post)
    assert voice.transcribe(b"x", "audio/ogg") == "tamam"
    assert seen == ["gpt-4o-transcribe", "whisper-1"]
    voice.transcribe(b"x", "audio/ogg")
    assert seen[2:] == ["whisper-1"]


def test_other_errors_still_raise_for_the_queue_to_retry(setup, monkeypatch):
    monkeypatch.setattr(voice, "_post", lambda *a: Resp(500))
    with pytest.raises(RuntimeError):
        voice.transcribe(b"x", "audio/ogg")
