"""Voice setup: personal link, limits, the shared setup logic, the page and its headers."""
import re
from datetime import timedelta

import pytest

from app import config, db, onboarding, sitepage, tts, voice, voicechat
from conftest import PHONE


@pytest.fixture(autouse=True)
def on(monkeypatch):
    monkeypatch.setattr(config, "VOICE_SETUP", True)
    monkeypatch.setattr(config, "OPENAI_API_KEY", "test-key")
    monkeypatch.setattr(config, "PUBLIC_BASE_URL", "https://bot.example.com")
    monkeypatch.setattr(voice, "transcribe", lambda audio, ct, client=None: "Mehmet, English")
    monkeypatch.setattr(tts, "speak", lambda text, fmt="opus": b"mp3" + fmt.encode())


@pytest.fixture
def newbie():
    db.upsert_client(PHONE, name="")
    db.set_client_fields(PHONE, onboarding_step=1, lang="en")
    return db.get_client(PHONE)


def token_of(url):
    return url.rsplit("/", 1)[1]


def test_link_is_personal_and_only_the_hash_is_stored(newbie):
    url = voicechat.new_link(PHONE)
    assert url.startswith("https://bot.example.com/talk/")
    token = token_of(url)
    with db.conn() as c:
        rows = [dict(r) for r in c.execute("SELECT * FROM voice_links")]
    assert len(rows) == 1 and token not in str(rows) and rows[0]["token_hash"] == voicechat._hash(token)
    assert voicechat.client_for(token)["phone"] == PHONE
    assert voicechat.client_for("nope") is None and voicechat.client_for("") is None


def test_a_new_link_replaces_the_old_one(newbie):
    first = token_of(voicechat.new_link(PHONE))
    second = token_of(voicechat.new_link(PHONE))
    assert voicechat.client_for(first) is None and voicechat.client_for(second)


def test_link_expires(newbie):
    token = token_of(voicechat.new_link(PHONE))
    past = db.to_iso(db.now_utc() - timedelta(minutes=1))
    with db.conn() as c:
        c.execute("UPDATE voice_links SET expires_at=?", (past,))
    assert voicechat.client_for(token) is None
    assert voicechat.turn(token, None, "", "hi") == {"ok": False, "error": "gone"}


@pytest.mark.parametrize("name,value", [("VOICE_SETUP", False), ("OPENAI_API_KEY", ""), ("PUBLIC_BASE_URL", "http://x.com")])
def test_off_without_flag_key_or_https(newbie, monkeypatch, name, value):
    assert voicechat.enabled()
    monkeypatch.setattr(config, name, value)
    assert not voicechat.enabled() and voicechat.offer_text(newbie) == ""


def test_a_spoken_turn_runs_the_normal_setup_and_speaks_back(newbie):
    token = token_of(voicechat.new_link(PHONE))
    out = voicechat.turn(token, b"audio", "audio/webm", "")
    assert out["ok"] and out["heard"] == "Mehmet, English" and out["audio"] and not out["done"]
    after = db.get_client(PHONE)
    assert after["onboarding_step"] == 2 and after["lang"] == "en"
    assert "2/" in out["reply"]


def test_typed_text_works_without_audio(newbie):
    token = token_of(voicechat.new_link(PHONE))
    out = voicechat.turn(token, None, "", "  Ayse   English ")
    assert out["ok"] and out["heard"] == "Ayse English" and db.get_client(PHONE)["onboarding_step"] == 2


def test_opening_the_conversation_speaks_the_current_question_and_counts_a_turn(newbie):
    token = token_of(voicechat.new_link(PHONE))
    out = voicechat.turn(token, None, "", "   ")
    assert out["ok"] and out["reply"].startswith("1/") and out["audio"] and not out["done"]
    with db.conn() as c:
        assert c.execute("SELECT turns FROM voice_links").fetchone()["turns"] == 1
    assert db.get_client(PHONE)["onboarding_step"] == 1


def test_opening_is_limited_too(newbie, monkeypatch):
    monkeypatch.setattr(config, "VOICE_MAX_TURNS", 1)
    token = token_of(voicechat.new_link(PHONE))
    assert voicechat.turn(token, None, "", "")["ok"]
    assert voicechat.turn(token, None, "", "") == {"ok": False, "error": "limit"}


def test_page_is_a_hands_free_conversation(newbie):
    html = voicechat.page(newbie).html
    for needle in ("getByteTimeDomainData", "onended", "MediaRecorder", "cfg.listening", "stopRec"):
        assert needle in html
    assert "audio/mpeg" in html


def test_turn_limit_per_link(newbie, monkeypatch):
    monkeypatch.setattr(config, "VOICE_MAX_TURNS", 2)
    token = token_of(voicechat.new_link(PHONE))
    assert voicechat.turn(token, b"a", "audio/webm", "")["ok"]
    assert voicechat.turn(token, b"a", "audio/webm", "")["ok"]
    assert voicechat.turn(token, b"a", "audio/webm", "") == {"ok": False, "error": "limit"}


def test_oversized_or_empty_audio_is_refused(newbie, monkeypatch):
    monkeypatch.setattr(config, "VOICE_MAX_AUDIO", 10)
    token = token_of(voicechat.new_link(PHONE))
    assert voicechat.turn(token, b"x" * 11, "audio/webm", "")["error"] == "error"
    assert voicechat.turn(token, b"", "audio/webm", "")["error"] == "error"


def test_transcription_failure_is_a_clean_error_and_setup_does_not_move(newbie, monkeypatch):
    def boom(a, c, client=None):
        raise RuntimeError("network")
    monkeypatch.setattr(voice, "transcribe", boom)
    token = token_of(voicechat.new_link(PHONE))
    assert voicechat.turn(token, b"a", "audio/webm", "")["error"] == "error"
    assert db.get_client(PHONE)["onboarding_step"] == 1


def test_a_tts_failure_still_returns_the_text(newbie, monkeypatch):
    def boom(text, fmt="opus"):
        raise RuntimeError("down")
    monkeypatch.setattr(tts, "speak", boom)
    token = token_of(voicechat.new_link(PHONE))
    out = voicechat.turn(token, None, "", "Ayse English")
    assert out["ok"] and out["audio"] is None and out["reply"]


def test_finishing_setup_reports_done(newbie):
    token = token_of(voicechat.new_link(PHONE))
    out = voicechat.turn(token, None, "", "skip setup")
    assert out["ok"] and out["done"]
    again = voicechat.turn(token, None, "", "hello")
    assert again["done"] and again["audio"] is None


def test_greeting_offers_the_link_only_when_voice_is_on(monkeypatch):
    db.upsert_client(PHONE, name="")
    c = db.get_client(PHONE)
    text = onboarding.handle(c, "hi")
    assert "/talk/" in text
    db.set_client_fields(PHONE, onboarding_step=0)
    monkeypatch.setattr(config, "VOICE_SETUP", False)
    assert "/talk/" not in onboarding.handle(db.get_client(PHONE), "hi")


def test_page_has_a_tight_csp_and_no_inline_handlers(newbie):
    page = voicechat.page(newbie)
    assert "connect-src 'self'" in page.csp and "media-src data:" in page.csp and "default-src 'none'" in page.csp
    assert not re.search(r"\son\w+=|style=", page.html)
    scripts = re.findall(r"<script(?![^>]*json)[^>]*>(.*?)</script>", page.html, re.S)
    assert len(scripts) == 1
    import base64, hashlib
    digest = "'sha256-" + base64.b64encode(hashlib.sha256(scripts[0].encode()).digest()).decode() + "'"
    assert digest in page.csp


def test_page_data_is_escaped(newbie, monkeypatch):
    monkeypatch.setattr(config, "BOT_NAME", "<b>x</b>")
    page = voicechat.page(newbie)
    assert "<b>x</b>" not in page.html and "&lt;b&gt;" in page.html


def test_talk_pages_get_private_headers_and_the_microphone():
    h = sitepage.security_headers("/talk/abc", True)
    assert "microphone=(self)" in h["Permissions-Policy"] and h["Cache-Control"] == "no-store"
    assert "noindex" in h["X-Robots-Tag"] and h["Referrer-Policy"] == "no-referrer"
    assert "microphone=()" in sitepage.security_headers("/", True)["Permissions-Policy"]
    assert "Disallow: /talk/" in sitepage.robots_txt()


def test_erasing_a_client_removes_the_link(newbie):
    token = token_of(voicechat.new_link(PHONE))
    db.erase_client(PHONE)
    assert voicechat.client_for(token) is None
    with db.conn() as c:
        assert c.execute("SELECT COUNT(*) n FROM voice_links").fetchone()["n"] == 0
