import hashlib
import hmac
import json
from types import SimpleNamespace

import pytest

from app import config, wa_meta, wa_twilio, whatsapp
from app.errors import PermanentSendError

SECRET = "test-app-secret"


def sign(raw: bytes, secret: str = SECRET) -> str:
    return "sha256=" + hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()


def body(*messages, statuses=None):
    value = {"messaging_product": "whatsapp", "messages": list(messages)}
    if statuses:
        value = {"statuses": statuses}
    return {"object": "whatsapp_business_account", "entry": [{"id": "1", "changes": [{"field": "messages", "value": value}]}]}


# ---------- signature ----------

def test_valid_signature_accepted():
    raw = b'{"a": 1}'
    assert wa_meta.verify_signature(raw, sign(raw)) is True


def test_tampered_body_or_wrong_secret_rejected():
    raw = b'{"a": 1}'
    assert wa_meta.verify_signature(b'{"a": 2}', sign(raw)) is False
    assert wa_meta.verify_signature(raw, sign(raw, "other-secret")) is False
    assert wa_meta.verify_signature(raw, "") is False
    assert wa_meta.verify_signature(raw, "sha256=") is False


def test_missing_app_secret_fails_closed(monkeypatch):
    monkeypatch.setattr(config, "META_APP_SECRET", "")
    raw = b"{}"
    assert wa_meta.verify_signature(raw, sign(raw, "")) is False


# ---------- parsing ----------

def test_parse_text_message_and_phone_normalisation():
    data = body({"from": "905551234567", "id": "wamid.1", "timestamp": "1", "type": "text", "text": {"body": "merhaba"}})
    [m] = wa_meta.parse_payload(data)
    assert (m.id, m.phone, m.type, m.text) == ("wamid.1", "+905551234567", "text", "merhaba")


def test_parse_voice_note():
    data = body({"from": "905551234567", "id": "wamid.2", "type": "audio",
                 "audio": {"id": "9988", "mime_type": "audio/ogg; codecs=opus"}})
    [m] = wa_meta.parse_payload(data)
    assert (m.type, m.media_id, m.mime) == ("audio", "9988", "audio/ogg; codecs=opus")


def test_parse_button_and_unknown_types():
    data = body(
        {"from": "905551234567", "id": "w3", "type": "interactive",
         "interactive": {"button_reply": {"id": "b", "title": "Evet"}}},
        {"from": "905551234567", "id": "w4", "type": "sticker", "sticker": {"id": "1"}},
    )
    a, b = wa_meta.parse_payload(data)
    assert (a.type, a.text) == ("text", "Evet") and b.type == "other"


def test_status_updates_and_garbage_are_ignored():
    assert wa_meta.parse_payload(body(statuses=[{"id": "x", "status": "read"}])) == []
    for junk in (None, [], "x", {}, {"object": "page"}, {"object": "whatsapp_business_account", "entry": [None]},
                 body({"from": "", "id": "x", "type": "text"}), body("notadict")):
        assert wa_meta.parse_payload(junk) == []


# ---------- sending ----------

class Recorder:
    def __init__(self, status=200, payload=None):
        self.calls, self.status, self.payload = [], status, payload or {}

    def post(self, url, headers=None, json=None, timeout=None):
        self.calls.append((url, headers, json))
        return SimpleNamespace(status_code=self.status, json=lambda: self.payload)


def test_send_text_request_shape(monkeypatch):
    rec = Recorder()
    monkeypatch.setattr(wa_meta.requests, "post", rec.post)
    wa_meta.send_text("+905551234567", "selam")
    url, headers, payload = rec.calls[0]
    assert url == "https://graph.facebook.com/v23.0/123456789/messages"
    assert headers == {"Authorization": "Bearer test-access-token"}
    assert payload["to"] == "905551234567" and payload["type"] == "text" and payload["text"]["body"] == "selam"


def test_client_errors_are_permanent_server_errors_are_retried(monkeypatch):
    monkeypatch.setattr(wa_meta.requests, "post", Recorder(400, {"error": {"code": 131047, "message": "window"}}).post)
    with pytest.raises(PermanentSendError, match="131047"):
        wa_meta.send_text("+905551234567", "x")
    monkeypatch.setattr(wa_meta.requests, "post", Recorder(401, {"error": {"code": 190, "message": "expired"}}).post)
    with pytest.raises(PermanentSendError):
        wa_meta.send_text("+905551234567", "x")
    for status in (429, 500, 503):
        monkeypatch.setattr(wa_meta.requests, "post", Recorder(status).post)
        with pytest.raises(RuntimeError) as exc:
            wa_meta.send_text("+905551234567", "x")
        assert not isinstance(exc.value, PermanentSendError)


def test_unconfigured_token_is_permanent(monkeypatch):
    monkeypatch.setattr(config, "META_ACCESS_TOKEN", "")
    with pytest.raises(PermanentSendError):
        wa_meta.send_text("+905551234567", "x")


def test_template_message_shape_and_parameter_cleaning(monkeypatch):
    rec = Recorder()
    monkeypatch.setattr(wa_meta.requests, "post", rec.post)
    monkeypatch.setattr(config, "META_TEMPLATE_NAME", "alert_v1")
    monkeypatch.setattr(config, "META_TEMPLATE_LANG", "tr")
    wa_meta.send_template("+905551234567", "Satır bir\n\nSatır   iki\t\tüç")
    tpl = rec.calls[0][2]["template"]
    assert tpl["name"] == "alert_v1" and tpl["language"] == {"code": "tr"}
    param = tpl["components"][0]["parameters"][0]["text"]
    assert "\n" not in param and "\t" not in param and "    " not in param
    assert param.startswith("Satır bir")


def test_hello_world_template(monkeypatch):
    rec = Recorder()
    monkeypatch.setattr(wa_meta.requests, "post", rec.post)
    wa_meta.send_hello("+905551234567")
    assert rec.calls[0][2]["template"] == {"name": "hello_world", "language": {"code": "en_US"}}


# ---------- media download safety ----------

def test_media_id_must_be_digits(monkeypatch):
    for bad in ("", "../x", "12 34", "abc", "1/../../2"):
        with pytest.raises(PermanentSendError):
            wa_meta.download_media(bad)


def test_media_url_host_is_checked(monkeypatch):
    def fake_get(url, headers=None, timeout=None, stream=False):
        return SimpleNamespace(status_code=200, json=lambda: {"url": "https://evil.example.com/a", "mime_type": "audio/ogg"})
    monkeypatch.setattr(wa_meta.requests, "get", fake_get)
    with pytest.raises(PermanentSendError, match="host"):
        wa_meta.download_media("12345")


def test_media_download_happy_path(monkeypatch):
    def fake_get(url, headers=None, timeout=None, stream=False):
        if url.endswith("/12345"):
            return SimpleNamespace(status_code=200, json=lambda: {
                "url": "https://lookaside.fbsbx.com/whatsapp_business/attachments/?mid=1", "mime_type": "audio/ogg"})
        return SimpleNamespace(status_code=200, headers={}, iter_content=lambda n: iter([b"abc", b"def"]))
    monkeypatch.setattr(wa_meta.requests, "get", fake_get)
    assert wa_meta.download_media("12345") == (b"abcdef", "audio/ogg")


# ---------- facade ----------

def test_long_messages_are_split():
    parts = whatsapp.chunk(("kelime " * 600).strip(), size=1500)
    assert len(parts) >= 3 and all(len(p) <= 1500 for p in parts)
    assert " ".join(parts).split() == ("kelime " * 600).split()
    assert whatsapp.chunk("kısa") == ["kısa"] and whatsapp.chunk("   ") == []


# ---------- twilio form parsing ----------

def test_twilio_text_and_voice():
    [t] = wa_twilio.parse_form({"MessageSid": "SM1", "From": "whatsapp:+905551234567", "Body": "selam", "NumMedia": "0"})
    assert (t.id, t.phone, t.type, t.text) == ("SM1", "+905551234567", "text", "selam")
    [v] = wa_twilio.parse_form({"MessageSid": "SM2", "From": "whatsapp:+905551234567", "NumMedia": "1",
                                "MediaUrl0": "https://api.twilio.com/m/1", "MediaContentType0": "audio/ogg"})
    assert (v.type, v.media_id) == ("audio", "https://api.twilio.com/m/1")
    assert wa_twilio.parse_form({}) == []
    with pytest.raises(PermanentSendError):
        wa_twilio.download_media("https://evil.example.com/x")


def test_parse_photo_and_document_messages():
    data = body(
        {"from": "905551234567", "id": "w5", "type": "image",
         "image": {"id": "111", "mime_type": "image/jpeg", "caption": "ne yazıyor?"}},
        {"from": "905551234567", "id": "w6", "type": "document",
         "document": {"id": "222", "mime_type": "application/pdf", "filename": "sözleşme.pdf", "caption": "özet"}},
    )
    photo, doc = wa_meta.parse_payload(data)
    assert (photo.type, photo.media_id, photo.mime, photo.text) == ("image", "111", "image/jpeg", "ne yazıyor?")
    assert (doc.type, doc.media_id, doc.filename, doc.text) == ("document", "222", "sözleşme.pdf", "özet")
