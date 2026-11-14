import json
import os
import stat

import pytest

from app import db, memory

P = "+905551234567"


def test_add_and_render():
    assert memory.add_fact(P, "people", "Wife is Ayşe")["status"] == "added"
    assert memory.add_fact(P, "work", "CEO at Acme")["status"] == "added"
    out = memory.render(P)
    assert "[people] Wife is Ayşe" in out and "[work] CEO at Acme" in out


def test_empty_memory_renders_placeholder():
    assert "nothing saved" in memory.render(P)


def test_exact_and_covered_duplicates_are_skipped():
    memory.add_fact(P, "about", "Lives in Istanbul")
    assert memory.add_fact(P, "about", "lives in istanbul!")["status"] == "duplicate"
    memory.add_fact(P, "about", "Lives in Istanbul, Kadıköy")
    assert memory.add_fact(P, "about", "Lives in Istanbul")["status"] == "duplicate"
    assert [i["text"] for i in memory.load(P)["sections"]["about"]] == ["Lives in Istanbul, Kadıköy"]


def test_more_specific_fact_replaces_the_old_one():
    memory.add_fact(P, "preferences", "Likes short replies")
    memory.add_fact(P, "preferences", "Likes short replies, no emoji")
    assert len(memory.load(P)["sections"]["preferences"]) == 1


def test_replaces_removes_outdated_fact():
    memory.add_fact(P, "work", "Works at Acme")
    memory.add_fact(P, "work", "Works at Globex", replaces="Acme")
    assert [i["text"] for i in memory.load(P)["sections"]["work"]] == ["Works at Globex"]


def test_section_is_capped_oldest_dropped():
    for n in range(40):
        memory.add_fact(P, "notes", f"Distinct note number {n:02d} alpha")
    items = [i["text"] for i in memory.load(P)["sections"]["notes"]]
    assert len(items) == memory.MAX_PER_SECTION
    assert items[-1].endswith("39 alpha") and not any("number 00" in t for t in items)


@pytest.mark.parametrize("text", [
    "My card is 4111 1111 1111 1111",
    "IBAN TR33 0006 1005 1978 6457 8413 26",
    "my password is hunter2",
    "banka şifre 1234",
    "the OTP code is 123456",
])
def test_secrets_are_refused(text):
    assert memory.add_fact(P, "notes", text)["status"] == "refused"
    assert memory.count(P) == 0


def test_invalid_section_and_short_text():
    assert memory.add_fact(P, "nonsense", "something valid")["status"] == "invalid"
    assert memory.add_fact(P, "notes", "ab")["status"] == "invalid"


def test_forget_by_query():
    memory.add_fact(P, "people", "Brother is Mehmet")
    memory.add_fact(P, "people", "Sister is Zeynep")
    assert memory.forget(P, "mehmet") == 1
    assert memory.forget(P, "ab") == 0  # too short to be safe
    assert memory.count(P) == 1


def test_forget_all_deletes_the_file():
    memory.add_fact(P, "notes", "Something to remember")
    assert memory.path_for(P).exists()
    assert memory.forget_all(P) is True
    assert not memory.path_for(P).exists()
    assert memory.forget_all(P) is False


def test_file_is_private_and_valid_json():
    memory.add_fact(P, "notes", "Something to remember")
    p = memory.path_for(P)
    assert stat.S_IMODE(os.stat(p).st_mode) == 0o600
    assert json.loads(p.read_text(encoding="utf-8"))["version"] == 1


def test_path_cannot_escape_the_memory_folder():
    p = memory.path_for("../../905551234567")
    assert p.parent == memory.path_for(P).parent and p.name == "905551234567.json"
    with pytest.raises(ValueError):
        memory.path_for("../../etc/passwd")  # no digits at all: refused
    with pytest.raises(ValueError):
        memory.path_for("")


def test_turkish_capitalisation_does_not_defeat_dedup():
    memory.add_fact(P, "about", "İstanbul'da yaşıyor")
    assert memory.add_fact(P, "about", "ISTANBUL'DA YASIYOR")["status"] == "duplicate"


def test_corrupt_file_is_treated_as_empty():
    memory.add_fact(P, "notes", "Something to remember")
    memory.path_for(P).write_text("{not json", encoding="utf-8")
    assert memory.count(P) == 0
    assert memory.add_fact(P, "notes", "Fresh start works")["status"] == "added"


def test_render_respects_size_cap_and_keeps_newest():
    for n in range(20):
        memory.add_fact(P, "notes", f"Fact {n:02d} " + "x" * 200)
    out = memory.render(P, max_chars=800)
    assert len(out) <= 800 and "Fact 19" in out and "Fact 00" not in out


def test_erase_client_removes_everything():
    db.upsert_client(P, name="X")
    db.add_message(P, "user", "hi")
    db.add_reminder(P, "r", "2030-01-01T00:00:00")
    db.add_watch(P, "THYAO.IS", 320, None, None)
    memory.add_fact(P, "notes", "Something to remember")
    db.erase_client(P)
    assert db.get_client(P) is None and db.list_watch(P) == [] and db.list_pending_reminders(P) == []
    assert not memory.path_for(P).exists()
