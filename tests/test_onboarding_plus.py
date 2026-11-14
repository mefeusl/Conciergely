"""The extra onboarding question for Plus clients: when should the morning briefing arrive?"""
import pytest

from app import assistant, config, db, memory, quiet
from conftest import FakeAnthropic, PHONE
from helpers import say


@pytest.fixture(autouse=True)
def reset_assistant():
    yield
    assistant.set_client(None)


@pytest.fixture
def plus_new():
    db.upsert_client(PHONE, name="Mehmet")
    db.set_client_fields(PHONE, plan="plus")


def walk_to_briefing_question():
    say("merhaba")
    for answer in ["Mehmet", "kısa", "piyasa", "yok"]:
        say(answer)
    [q6] = say("geç")  # the stock question, skipped
    return q6


def test_plus_client_is_told_six_questions_and_gets_the_briefing_question(plus_new):
    [first] = say("merhaba")
    assert "7 kısa soru" in first and "1/7" in first
    for answer in ["Mehmet", "kısa", "piyasa", "yok"]:
        say(answer)
    [q6] = say("geç")
    assert "6/7" in q6 and "sesli" in q6 and "08:30" in q6
    assert db.get_client(PHONE)["onboarding_done"] == 0


def test_basic_client_still_gets_five_and_no_briefing_question():
    db.upsert_client(PHONE, name="Mehmet")
    [first] = say("merhaba")
    assert "5 kısa soru" in first and "1/5" in first
    for answer in ["Mehmet", "kısa", "piyasa", "yok"]:
        say(answer)
    [last] = say("geç")
    assert "tanışmış olduk" in last and "brifing" not in last and "08:30" not in last
    assert db.get_client(PHONE)["onboarding_done"] == 1 and db.get_client(PHONE)["briefing_min"] == -1


@pytest.mark.parametrize("answer, minutes", [
    ("07:45", 7 * 60 + 45), ("her sabah 8.30 olsun", 8 * 60 + 30), ("7am", 420), ("6:00", 360),
    ("evet", 8 * 60 + 30), ("Olur", 8 * 60 + 30), ("yes", 8 * 60 + 30), ("09:15 lütfen", 9 * 60 + 15),
])
def test_time_answers_turn_the_briefing_on(plus_new, answer, minutes):
    walk_to_briefing_question()
    [reply] = say(answer)
    c = db.get_client(PHONE)
    assert c["briefing_min"] == minutes and c["onboarding_done"] == 0
    assert quiet.fmt(minutes) in reply and "7/7" in reply and "alanda çalışıyorsunuz" in reply
    assert quiet.fmt(minutes) in memory.render(PHONE)
    [done] = say("geç")
    assert "tanışmış olduk" in done and db.get_client(PHONE)["onboarding_done"] == 1


@pytest.mark.parametrize("answer", ["yok", "hayır", "no", "istemiyorum", "geç", "skip", "none"])
def test_no_answers_leave_it_off(plus_new, answer):
    walk_to_briefing_question()
    [reply] = say(answer)
    assert db.get_client(PHONE)["briefing_min"] == -1 and db.get_client(PHONE)["onboarding_done"] == 0
    assert "kapalı" in reply and "7/7" in reply


def test_an_unreadable_answer_asks_again_instead_of_moving_on(plus_new):
    walk_to_briefing_question()
    [reply] = say("sabahları erken")
    assert "08:30" in reply and db.get_client(PHONE)["onboarding_done"] == 0 and db.get_client(PHONE)["onboarding_step"] == 6
    [ok] = say("07:00")
    assert db.get_client(PHONE)["briefing_min"] == 420 and "7/7" in ok


def test_impossible_times_are_not_accepted(plus_new):
    walk_to_briefing_question()
    for bad in ["25:00", "12:75"]:
        [reply] = say(bad)
        assert db.get_client(PHONE)["onboarding_done"] == 0 and "08:30" in reply


def test_skip_setup_for_a_plus_client_leaves_the_briefing_off(plus_new):
    [done] = say("kurulumu atla")
    c = db.get_client(PHONE)
    assert "tanışmış olduk" in done and c["onboarding_done"] == 1 and c["briefing_min"] == -1


def test_english_plus_client(plus_new):
    say("hi")
    say("Sam, English")
    for answer in ["short", "markets", "none", "skip"]:
        say(answer)
    db.set_client_fields(PHONE, onboarding_step=6)
    [reply] = say("7:30am")
    assert db.get_client(PHONE)["briefing_min"] == 450 and "07:30" in reply and "briefing arrives" in reply
    assert "7/7" in reply and "what area do you work in" in reply


def test_default_time_can_be_configured(plus_new, monkeypatch):
    monkeypatch.setattr(config, "BRIEFING_DEFAULT_MINUTES", 9 * 60)
    walk_to_briefing_question()
    say("yes")
    assert db.get_client(PHONE)["briefing_min"] == 540


def test_upgrading_mid_setup_adds_the_question(plus_new):
    db.set_client_fields(PHONE, plan="basic")
    say("merhaba")
    db.set_client_fields(PHONE, plan="plus", onboarding_step=5)
    [q] = say("geç")
    assert "6/7" in q


@pytest.mark.parametrize("text, expected", [
    ("08:30", 510), ("7am", 420), ("7pm", 1140), ("8.30", 510), ("12am", 0), ("12pm", 720), ("sabah 8", 480),
    ("25:00", None), ("24:00", None), ("12:75", None), ("nothing here", None), ("", None), ("0:00", 0), ("23:59", 1439),
])
def test_parse_one(text, expected):
    assert quiet.parse_one(text) == expected


def finish_with_work(answer):
    walk_to_briefing_question()
    say("08:00")
    return say(answer)


def test_field_of_work_is_saved_for_the_briefing_news(plus_new):
    [done] = finish_with_work("İnşaat ve gayrimenkul")
    c = db.get_client(PHONE)
    assert "tanışmış olduk" in done and c["onboarding_done"] == 1 and c["work_area"] == "İnşaat ve gayrimenkul"
    assert "İnşaat ve gayrimenkul" in memory.render(PHONE)


@pytest.mark.parametrize("answer", ["geç", "yok", "skip"])
def test_field_of_work_can_be_skipped(plus_new, answer):
    [done] = finish_with_work(answer)
    assert "tanışmış olduk" in done and db.get_client(PHONE)["work_area"] == ""


def test_secrets_are_not_taken_as_a_field_of_work(plus_new):
    [done] = finish_with_work("kart numaram 4111 1111 1111 1111")
    assert "kaydetmiyorum" in done and db.get_client(PHONE)["work_area"] == "" and "4111" not in memory.render(PHONE)


def test_a_very_long_field_of_work_is_cut(plus_new):
    finish_with_work("x" * 500)
    assert len(db.get_client(PHONE)["work_area"]) <= 80
