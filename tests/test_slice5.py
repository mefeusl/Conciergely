"""Slice 5: shared reminders with consent."""
import json

from app import adminlogic, db, jobs, scheduler, sharing, tools
from app.tools import set_reminder

OWNER, MEMBER, OTHER = "+905551110001", "+905552220002", "+905553330003"


def _setup():
    for phone, name in ((OWNER, "Ali Veli"), (MEMBER, "Ayşe Yılmaz")):
        adminlogic.add_client(phone, name, "basic")
        db.set_client_fields(phone, onboarding_done=1)


def _soon():
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo
    return (datetime.now(ZoneInfo("Europe/Istanbul")) + timedelta(days=3)).strftime("%Y-%m-%d 10:00")


def _sends(phone):
    with db.conn() as c:
        return [json.loads(r["payload"])["text"] for r in c.execute("SELECT payload FROM jobs WHERE kind='send' AND phone=?", (phone,))]


def _fire_now():
    with db.conn() as c:
        c.execute("UPDATE reminders SET due_at=? WHERE kind='main'", (db.to_iso(db.now_utc()),))
    scheduler.tick_reminders()


def test_request_then_accept_then_the_copy_arrives():
    _setup()
    out = json.loads(set_reminder(OWNER, {"text": "Akşam yemeği 20:00", "when_local": _soon(), "share_with": MEMBER}))
    assert out["ok"] and out["shared"] == "waiting_for_their_acceptance"
    assert any("paylaşmak istiyor" in t for t in _sends(MEMBER))
    _fire_now()
    assert not any("Akşam yemeği" in t for t in _sends(MEMBER))  # not accepted: nothing delivered
    # a second reminder after the member accepts
    assert "açık" in sharing.handle(db.get_client(MEMBER), "kabul")
    set_reminder(OWNER, {"text": "Konser bileti", "when_local": _soon(), "share_with": MEMBER})
    _fire_now()
    got = [t for t in _sends(MEMBER) if "Konser" in t]
    assert len(got) == 1 and "Ali" in got[0]
    assert not any("Konser" in t and "paylaştı" in t for t in _sends(OWNER))


def test_declined_blocks_and_second_request_is_not_spam():
    _setup()
    set_reminder(OWNER, {"text": "x yz", "when_local": _soon(), "share_with": MEMBER})
    set_reminder(OWNER, {"text": "x yz 2", "when_local": _soon(), "share_with": MEMBER})
    assert sum("paylaşmak istiyor" in t for t in _sends(MEMBER)) == 1
    assert sharing.handle(db.get_client(MEMBER), "reddet") == sharing.TEXT["tr"]["declined"]
    out = json.loads(set_reminder(OWNER, {"text": "x yz 3", "when_local": _soon(), "share_with": MEMBER}))
    assert out["error"] == "sharing_declined"


def test_strangers_and_self_cannot_receive():
    _setup()
    for who in (OTHER, OWNER, "abc", ""):
        r = json.loads(set_reminder(OWNER, {"text": "x yz", "when_local": _soon(), "share_with": who})) if who else None
        if r:
            assert r["error"] == "sharing_cannot"
    assert _sends(OTHER) == []
    assert db.get_client(OTHER) is None


def test_plain_kabul_without_a_pending_request_is_normal_chat():
    _setup()
    assert sharing.handle(db.get_client(MEMBER), "kabul") is None


def test_end_sharing_stops_copies_and_lists():
    _setup()
    set_reminder(OWNER, {"text": "Bir iki", "when_local": _soon(), "share_with": MEMBER})
    sharing.handle(db.get_client(MEMBER), "accept")
    assert "Ali" in sharing.handle(db.get_client(MEMBER), "paylasimlar")
    assert "Ayşe" in sharing.handle(db.get_client(OWNER), "paylasimlar")
    assert sharing.handle(db.get_client(MEMBER), "paylasimi bitir") == sharing.TEXT["tr"]["ended"]
    _fire_now()
    assert not any("Bir iki" in t for t in _sends(MEMBER))


def test_repeating_shared_reminder_keeps_sharing():
    _setup()
    set_reminder(OWNER, {"text": "Aidat", "when_local": _soon(), "share_with": MEMBER, "repeat": "monthly"})
    sharing.handle(db.get_client(MEMBER), "kabul")
    _fire_now()
    nxt = [r for r in db.list_pending_reminders(OWNER) if r["kind"] == "main"]
    assert len(nxt) == 1 and nxt[0]["shared_with"] == MEMBER


def test_member_limit_and_erase_clean_up():
    _setup()
    for i in range(sharing.MAX_MEMBERS):
        p = f"+90555777000{i}"
        adminlogic.add_client(p, f"M{i}", "basic")
        db.set_client_fields(p, onboarding_done=1)
        assert sharing.request(db.get_client(OWNER), p) == "sent"
    assert sharing.request(db.get_client(OWNER), MEMBER) == "full"
    db.erase_client(OWNER)
    with db.conn() as c:
        assert c.execute("SELECT COUNT(*) AS n FROM share_links").fetchone()["n"] == 0


def test_tool_and_prompt_are_wired():
    from app import assistant
    props = [t for t in tools.TOOLS if t["name"] == "set_reminder"][0]["input_schema"]["properties"]
    assert "share_with" in props
    _setup()
    assert "Shared reminders" in assistant.build_system(db.get_client(OWNER))
