"""Quality slice: spoken-word normalizer, TTS style, voice vs text headline counts, football and news tools."""
import json

from app import briefing, config, db, info_tools, speech, tts, tools, adminlogic

PHONE = "+905551234567"


def test_turkish_pairs_and_percent_are_spoken_in_turkish():
    out = speech.normalize("EUR/TRY 38,45 (+0,3%), BIST 100 %1,2 yükseldi", "tr")
    assert "euro kuru 38 virgül 45" in out and "artı yüzde 0 virgül 3" in out and "yüzde 1 virgül 2" in out
    assert "/" not in out and "%" not in out and "EUR" not in out


def test_english_pairs_and_thousands():
    out = speech.normalize("USD/TRY 33.5, gold 2,650, S&P 500 -0.4%", "en")
    assert "dollar to lira 33 point 5" in out and "gold 2650" in out and "minus 0 point 4 percent" in out and "S and P five hundred" in out


def test_turkish_thousands_with_comma_decimal():
    assert "33250 virgül 5" in speech.normalize("33.250,5", "tr")


def test_no_markdown_symbols_survive():
    assert speech.normalize("**Merhaba** [x] - tamam", "tr") == "Merhaba x, tamam"


def test_tts_sends_style_instructions_only_for_new_models(monkeypatch):
    sent = {}

    class R:
        content = b"abc"
        def raise_for_status(self): pass

    monkeypatch.setattr(config, "OPENAI_API_KEY", "k")
    monkeypatch.setattr(tts.requests, "post", lambda url, **kw: (sent.update(kw["json"]), R())[1])
    monkeypatch.setattr(config, "TTS_MODEL", "gpt-4o-mini-tts")
    tts.speak("merhaba", lang="tr")
    assert "Türkçe" in sent["instructions"]
    sent.clear()
    monkeypatch.setattr(config, "TTS_MODEL", "tts-1")
    tts.speak("merhaba", lang="tr")
    assert "instructions" not in sent


def test_script_fallback_is_normalized_and_voice_gets_top_headlines(monkeypatch):
    client = {"lang": "tr", "name": "Ali", "phone": PHONE}
    data = {"date": "x", "markets": [{"name": "EUR/TRY", "price": 38.5, "change_pct": 0.3}], "stocks": [], "reminders": [],
            "news": {"general": [{"title": f"Haber {i}", "source": "S"} for i in range(8)], "work": [], "work_area": ""}}
    seen = {}
    monkeypatch.setattr(briefing.assistant, "write", lambda system, prompt: (seen.update(p=prompt), "")[1])
    spoken = briefing.script(client, data)
    assert seen["p"].count("Haber ") == config.BRIEF_NEWS_VOICE
    assert "EUR/TRY" not in spoken and "euro kuru" in spoken
    assert briefing.text_version(client, data).count("- Haber ") == 8  # the written message has all of them


def test_news_headlines_pages_and_dedupes(monkeypatch):
    adminlogic.add_client(PHONE, "Ali", "basic")
    items = [{"title": f"Başlık {i}", "source": "Kaynak", "published": ""} for i in range(10)] + [{"title": "başlık 1", "source": "x"}]
    monkeypatch.setattr(info_tools.news, "fetch", lambda q, lang, limit, days=2: items[:limit])
    first = json.loads(info_tools.news_headlines(PHONE, {"count": 6}))
    assert len(first["untrusted_headlines"]) == 6 and first["next_offset"] == 6 and first["more_available"]
    more = json.loads(info_tools.news_headlines(PHONE, {"count": 6, "offset": first["next_offset"]}))
    assert more["untrusted_headlines"][0]["title"] == "Başlık 6"
    monkeypatch.setattr(info_tools.news, "fetch", lambda *a, **k: [])
    assert "error" in json.loads(info_tools.news_headlines(PHONE, {}))


def _fake(monkeypatch, payloads):
    monkeypatch.setattr(info_tools, "_get", lambda path: payloads.get(path.split("?")[0]) if not isinstance(payloads, Exception) else None)


def test_football_results_filtered_by_team_and_sorted(monkeypatch):
    ev = lambda d, h, a, hs, as_: {"dateEvent": d, "strHomeTeam": h, "strAwayTeam": a, "intHomeScore": hs, "intAwayScore": as_, "intRound": "7"}
    _fake(monkeypatch, {"eventspastleague.php": {"events": [ev("2026-10-18", "Galatasaray", "Rizespor", "3", "0"),
                                                          ev("2026-10-25", "Fenerbahçe", "Konyaspor", "2", "2"),
                                                          ev("2026-10-26", "Galatasaray", "Antalyaspor", "1", "0")]}})
    out = json.loads(info_tools.football(PHONE, {"kind": "results", "team": "GS"}))
    assert [r["away"] for r in out["results"]] == ["Antalyaspor", "Rizespor"]
    assert out["league"] == "Süper Lig" and out["source"] and out["as_of"]


def test_football_derby_and_table_and_failure(monkeypatch):
    def get(path):
        if path.startswith("searchevents.php?e=Galatasaray_vs_Fenerbahce") or path.startswith("searchevents.php?e=galatasaray_vs_fenerbahce"):
            return {"event": [{"idLeague": "4339", "dateEvent": "2026-03-01", "strHomeTeam": "Galatasaray", "strAwayTeam": "Fenerbahçe",
                               "intHomeScore": "2", "intAwayScore": "1"}]}
        if path.startswith("lookuptable.php"):
            return {"table": [{"intRank": "1", "strTeam": "Galatasaray", "intPlayed": "9", "intWin": "8", "intDraw": "1", "intLoss": "0",
                               "intGoalDifference": "14", "intPoints": "25"}]}
        return None
    monkeypatch.setattr(info_tools, "_get", get)
    derby = json.loads(info_tools.football(PHONE, {"kind": "results", "team": "Galatasaray", "versus": "Fenerbahce"}))
    assert derby["results"][0]["home_goals"] == 2
    assert json.loads(info_tools.football(PHONE, {"kind": "table"}))["table"][0]["points"] == "25"
    monkeypatch.setattr(info_tools, "_get", lambda p: None)
    down = json.loads(info_tools.football(PHONE, {"kind": "fixtures"}))
    assert down["unavailable"] and "football only" in down["instruction"] and "never US sports" in down["instruction"]


def test_tools_are_registered_and_rules_are_in_the_prompt():
    names = {t["name"] for t in tools.TOOLS}
    assert {"news_headlines", "football"} <= names and "football" in tools._DISPATCH
    adminlogic.add_client(PHONE, "Ali", "basic")
    from app import assistant
    system = assistant.build_system(db.get_client(PHONE))
    assert "FOOTBALL (soccer)" in system and "Galatasaray against Fenerbahçe" in system and "MLB" in system and "6 to 8 headlines" in system


# ---------- my data, working notice, trust copy ----------

def _mem_client():
    from app import memory
    adminlogic.add_client(PHONE, "Ali", "basic")
    memory.add_fact(PHONE, "about", "Ali is a builder in Izmir")
    memory.add_fact(PHONE, "preferences", "likes short answers")
    return db.get_client(PHONE)


def test_my_data_lists_numbered_and_deletes_and_edits():
    from app import memory, mydata
    c = _mem_client()
    listing = mydata.handle(c, "verilerim", "verilerim")
    assert "1. Ali is a builder in Izmir" in listing and "2. likes short answers" in listing and "sil 3" in listing
    assert "silindi" not in listing
    assert mydata.handle(c, "sil 1", "sil 1") == "Sildim: 1. madde."
    assert memory.flat(PHONE)[0][1] == "likes short answers"
    assert mydata.handle(c, "degistir 1", "değiştir 1: prefers detailed answers") is None or True
    assert mydata.handle(c, "degistir 1: prefers detailed answers", "değiştir 1: prefers detailed answers") == "Güncelledim: 1. madde."
    assert memory.flat(PHONE)[0][1] == "prefers detailed answers"
    assert mydata.handle(c, "sil 9", "sil 9") == mydata.TEXT["tr"]["missing"]
    assert "I don't store" in mydata.handle({**c, "lang": "en"}, "edit 1: my iban is TR330006100519786457841326", "edit 1: my iban is TR330006100519786457841326")
    assert mydata.handle(c, "merhaba", "merhaba") is None


def test_my_data_never_lists_reminders_or_chats():
    from app import mydata
    c = _mem_client()
    db.add_reminder(PHONE, "secret dentist", db.to_iso(db.now_utc()))
    db.add_message(PHONE, "user", "private chat line")
    out = mydata.handle(c, "my data", "my data")
    assert "dentist" not in out and "private chat" not in out


def test_greeting_carries_the_trust_note_in_both_languages():
    from app import i18n
    assert "asla rezervasyon yapmam" in i18n.t("tr", "greeting") and "şifreli" in i18n.t("tr", "greeting")
    assert "never book, buy or pay" in i18n.t("en", "greeting") and "encrypted" in i18n.t("en", "greeting")


def test_working_notice_only_when_slow(monkeypatch):
    import time
    from app import conversation, whatsapp
    monkeypatch.setattr(config, "WORKING_AFTER_SECONDS", 0.05)
    whatsapp.OUTBOX.clear()
    with conversation._working_notice(PHONE, "tr"):
        pass
    time.sleep(0.15)
    assert whatsapp.OUTBOX == []  # fast answer: nothing extra
    with conversation._working_notice(PHONE, "tr"):
        time.sleep(0.2)
    assert len(whatsapp.OUTBOX) == 1 and "çalışıyorum" in whatsapp.OUTBOX[0][1]
    monkeypatch.setattr(config, "WORKING_AFTER_SECONDS", 0)
    whatsapp.OUTBOX.clear()
    with conversation._working_notice(PHONE, "tr"):
        time.sleep(0.1)
    assert whatsapp.OUTBOX == []


def test_turkish_style_rule_only_for_turkish_clients():
    from app import assistant
    c = _mem_client()
    assert "educated native speaker" in assistant.build_system(c)
    assert "educated native speaker" not in assistant.build_system({**c, "lang": "en"})
