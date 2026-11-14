"""Smart model routing: cheap model for short plain chat, main model for anything that needs care."""
import pytest

from app import assistant, config, db, routing
from conftest import ApiError, FakeAnthropic, PHONE, response, text_block
from helpers import say

SMART, FAST = "claude-sonnet-5-5", "claude-haiku-5-5"


@pytest.fixture(autouse=True)
def on(monkeypatch):
    monkeypatch.setattr(config, "MODEL_ROUTING", True)
    monkeypatch.setattr(config, "CLAUDE_MODEL", SMART)
    monkeypatch.setattr(config, "MODEL_FAST", FAST)
    routing.reset()
    yield
    routing.reset()
    assistant.set_client(None)


def pick(text, last="", attachments=False, extra=None):
    return routing.choose(last, text, attachments, extra)


@pytest.mark.parametrize("text", ["selam", "Teşekkürler!", "tamam", "good morning", "yarın 9'da Ahmet'i ara",
                                  "THYAO ne durumda", "remind me to call at 5", "uçuş bul", "weather in Paris",
                                  "bana şu sayfaya bak https://x.com", "alert koy", "2 tane", "evet", "x" * 101])
def test_ordinary_messages_go_to_the_fast_model(text):
    assert pick(text) == FAST


@pytest.mark.parametrize("text", [
    "THYAO grafik çiz", "show me the chart", "hisseleri tara", "run a scan", "30 günlük tarama yap", "30 day scan",
    "backtest it", "THYAO için senaryo çıkar", "scenario for AAPL", "risk analizi", "compare these two",
    "bu PDF'i özetle", "x " * 250,
])
def test_heavy_requests_go_to_the_main_model(text):
    assert pick(text) == SMART


def test_a_pdf_goes_to_the_main_model_but_a_photo_stays_fast():
    pdf = [{"type": "document", "source": {}}]
    photo = [{"type": "image", "source": {}}]
    assert routing.choose("", "selam", True, None, pdf) == SMART
    assert routing.choose("", "selam", True, None, photo) == FAST
    assert pick("selam", attachments=True) == FAST
    assert pick("selam", extra="how to treat a photo") == FAST


def test_escalate_only_for_heavy_tools():
    assert routing.escalate(FAST, ["list_watchlist"]) == FAST
    assert routing.escalate(FAST, ["list_watchlist", "scan_results"]) == SMART
    assert routing.escalate(SMART, ["scan_results"]) == SMART


def test_switches():
    assert pick("selam") == FAST
    config.MODEL_ROUTING = False
    assert pick("selam") == SMART
    config.MODEL_ROUTING = True
    config.MODEL_FAST = ""
    assert pick("selam") == SMART
    config.MODEL_FAST = SMART
    assert pick("selam") == SMART
    config.MODEL_FAST = FAST
    routing.disable()
    assert pick("selam") == SMART
    routing.reset()
    assert pick("selam") == FAST


# ---------- inside the real conversation loop ----------

def test_cheap_model_answers_a_greeting_with_the_same_tools(client):
    fake = FakeAnthropic(response(text_block("Merhaba!")))
    assistant.set_client(fake)
    assert say("selam") == ["Merhaba!"]
    assert fake.calls[0]["model"] == FAST and fake.calls[0]["tools"]


def test_heavy_tool_moves_the_rest_of_the_reply_to_the_main_model(client):
    from conftest import tool_block
    fake = FakeAnthropic(response(tool_block("t", "scan_results"), stop="tool_use"), response(text_block("Bitti.")))
    assistant.set_client(fake)
    say("bugün ne var")
    assert [c["model"] for c in fake.calls] == [FAST, SMART]


def test_a_serious_question_uses_the_main_model(client):
    fake = FakeAnthropic(response(text_block("Bakıyorum.")))
    assistant.set_client(fake)
    say("THYAO için senaryo çıkar")
    assert fake.calls[0]["model"] == SMART


def test_one_whole_tool_loop_stays_on_one_model(client):
    from conftest import tool_block
    fake = FakeAnthropic(response(tool_block("t", "list_watchlist"), stop="tool_use"), response(text_block("Boş.")))
    assistant.set_client(fake)
    say("selam")  # cheap, and the tool is a light one: both calls use the same model
    assert [c["model"] for c in fake.calls] == [FAST, FAST]


def test_a_failing_cheap_model_falls_back_to_the_main_model(client):
    fake = FakeAnthropic(ApiError(500, "overloaded"), response(text_block("Merhaba!")))
    assistant.set_client(fake)
    assert say("selam") == ["Merhaba!"]
    assert [c["model"] for c in fake.calls] == [FAST, SMART]
    fake._responses.append(response(text_block("Yine merhaba")))
    say("selam")
    assert fake.calls[-1]["model"] == FAST  # a one-off failure does not switch routing off


def test_a_missing_cheap_model_switches_routing_off_until_restart(client):
    fake = FakeAnthropic(ApiError(404, "model: claude-haiku-5-5 not found"), response(text_block("Merhaba!")),
                         response(text_block("Yine merhaba")))
    assistant.set_client(fake)
    say("selam")
    say("selam")
    assert [c["model"] for c in fake.calls] == [FAST, SMART, SMART]


def test_the_main_model_failing_still_raises_for_the_queue_to_retry(client):
    fake = FakeAnthropic(ApiError(500, "down"))
    assistant.set_client(fake)
    with pytest.raises(ApiError):
        assistant.reply(db.get_client(PHONE), "THYAO grafik çiz")
    assert len(fake.calls) == 1  # no second attempt on the same model


def test_write_uses_the_cheap_model_then_falls_back():
    fake = FakeAnthropic(response(text_block("özet")))
    assistant.set_client(fake)
    assert assistant.write("sys", "prompt") == "özet" and fake.calls[0]["model"] == FAST
    assert "tools" not in fake.calls[0]
    fake = FakeAnthropic(ApiError(500, "x"), response(text_block("ikinci")))
    assistant.set_client(fake)
    assert assistant.write("sys", "prompt") == "ikinci" and [c["model"] for c in fake.calls] == [FAST, SMART]
    fake = FakeAnthropic(ApiError(500, "x"))
    assistant.set_client(fake)
    config.MODEL_ROUTING = False
    with pytest.raises(ApiError):
        assistant.write("sys", "prompt")
    assert [c["model"] for c in fake.calls] == [SMART]


# ---------- a new day is a fresh start ----------

def test_system_prompt_tells_the_model_when_the_last_message_was_long_ago(client):
    assert "hours ago" not in assistant.build_system(client, None, None)
    assert "hours ago" not in assistant.build_system(client, None, 2)
    text = assistant.build_system(client, None, 20)
    assert "about 20 hours ago" in text and "fresh start" in text and "Never open a reply by correcting" in text


def test_reply_passes_the_gap_since_the_previous_message(client):
    from datetime import timedelta
    fake = FakeAnthropic(response(text_block("Selam!")))
    assistant.set_client(fake)
    db.add_message(PHONE, "user", "eski görev")
    with db.conn() as c:
        c.execute("UPDATE messages SET created_at=?", (db.to_iso(db.now_utc() - timedelta(hours=18)),))
    say("yo")
    assert "about 18 hours ago" in fake.calls[0]["system"]


def test_no_gap_note_for_a_first_message(client):
    fake = FakeAnthropic(response(text_block("Selam!")))
    assistant.set_client(fake)
    say("yo")
    assert "hours ago" not in fake.calls[0]["system"]
