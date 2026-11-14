"""Unknown numbers, referral codes, the reward ladder and discount-aware prices."""
import pytest

from app import adminlogic, billing, config, db, jobs, referrals
from app.intake import intake
from app.models import Incoming

OWNER, FRIEND, STRANGER = "+905551110001", "+905552220002", "+905553330003"


@pytest.fixture(autouse=True)
def _cfg(monkeypatch):
    monkeypatch.setattr(config, "CONTACT_WHATSAPP", "+90 555 000 11 22")
    monkeypatch.setattr(config, "CONTACT_WEBSITE", "https://example.com")
    monkeypatch.setattr(config, "BANK_IBAN", "TR330006100519786457841326")
    monkeypatch.setattr(config, "BANK_HOLDER", "Test")


def _inviter():
    adminlogic.add_client(OWNER, "Ayse", "basic")
    return db.get_client(OWNER)


def _say(phone, text, n=[0]):
    n[0] += 1
    return intake([Incoming(id=f"m{n[0]}", phone=phone, text=text)])


def _sends(phone):
    with db.conn() as c:
        return [dict(r) for r in c.execute("SELECT * FROM jobs WHERE phone=? AND kind='send'", (phone,))]


def test_stranger_gets_one_reply_per_day():
    assert _say(STRANGER, "merhaba") == 0
    assert _say(STRANGER, "selam") == 0
    sent = _sends(STRANGER)
    assert len(sent) == 1 and "REF-" in sent[0]["payload"] and "example.com" in sent[0]["payload"]
    assert db.get_client(STRANGER) is None  # never registered without a code


def test_valid_code_registers_basic_with_trial_and_link():
    inviter = _inviter()
    code = referrals.code_of(inviter)
    assert _say(FRIEND, f"Merhaba {code}") == 1
    friend = db.get_client(FRIEND)
    assert friend["plan"] == "basic" and friend["referred_by"] == OWNER and friend["active"]
    assert friend["trial_ends_at"]  # the free Plus trial started with this first message


def test_bad_code_and_self_referral_and_ex_client():
    inviter = _inviter()
    code = referrals.code_of(inviter)
    assert _say(STRANGER, "REF-9999") == 0 and db.get_client(STRANGER) is None
    assert _say(OWNER, f"x {code}") == 1  # an existing client's message is a normal message
    adminlogic.add_client(FRIEND, "Eski", "basic")
    db.erase_client(FRIEND)
    assert _say(FRIEND, code) == 0 and db.get_client(FRIEND) is None  # was a client once


def test_daily_cap(monkeypatch):
    inviter = _inviter()
    monkeypatch.setattr(config, "REF_SIGNUPS_PER_DAY", 2)
    code = referrals.code_of(inviter)
    for i in range(4):
        _say(f"+90555999000{i}", code)
    assert referrals.signups_today() == 2


def test_ladder():
    assert [referrals.pct_for(n) for n in (1, 2, 3, 5, 6, 9)] == [25, 25, 35, 35, 50, 50]
    assert referrals.level(0) == 1 and referrals.level(3) == 2 and referrals.level(6) == 3


def test_friend_gets_10_percent_then_inviter_credit_after_first_payment():
    inviter = _inviter()
    _say(FRIEND, referrals.code_of(inviter))
    friend = db.get_client(FRIEND)
    # Basic first month = 500 half price + 1000 setup; 10 % off the month only
    assert billing.amount_due(friend, "basic") == 1000 + 450
    assert not referrals.pending_credits(OWNER)
    billing.apply_payment(FRIEND, "basic", source="manual")
    credits = referrals.pending_credits(OWNER)
    assert [c["pct"] for c in credits] == [25]
    assert any("refreward" in (j["dedup_key"] or "") for j in _sends(OWNER))
    # second month for the friend: no discount any more
    assert billing.amount_due(db.get_client(FRIEND), "basic") == 500
    # inviter's own next payment uses the credit: 25 % off the month price
    before = billing.amount_due(db.get_client(OWNER), "basic")
    assert before == 1000 + 375
    billing.apply_payment(OWNER, "basic", source="manual")
    assert not referrals.pending_credits(OWNER)


def test_second_payment_gives_no_second_reward():
    inviter = _inviter()
    _say(FRIEND, referrals.code_of(inviter))
    billing.apply_payment(FRIEND, "basic", source="manual")
    billing.apply_payment(FRIEND, "basic", source="manual")
    assert len(referrals.pending_credits(OWNER)) == 1


def test_credits_stack_capped_and_never_below_zero():
    inviter = _inviter()
    for p in (25, 35, 50):
        with db.conn() as c:
            c.execute("INSERT INTO referral_credits(phone, pct, created_at) VALUES(?,?,?)", (OWNER, p, db.now_utc_iso()))
    pct, ids = referrals.discount(db.get_client(OWNER))
    assert pct == 100 and len(ids) == 3
    assert billing.amount_due(db.get_client(OWNER), "basic") == 1000  # setup only, month free
    for p in (50, 50):
        with db.conn() as c:
            c.execute("INSERT INTO referral_credits(phone, pct, created_at) VALUES(?,?,?)", (OWNER, p, db.now_utc_iso()))
    pct, ids = referrals.discount(db.get_client(OWNER))
    assert pct == 100 and len(ids) == 3  # the rest carries over


def test_invite_command_text():
    inviter = _inviter()
    text = referrals.handle(inviter, "davet")
    assert referrals.code_of(inviter) in text and "wa.me/905550001122" in text
    assert referrals.handle(inviter, "merhaba") is None


def test_mentions_once_and_not_after_stop():
    inviter = _inviter()
    first = referrals.mention(inviter, referrals.PAID_BIT)
    assert first and referrals.mention(inviter, referrals.PAID_BIT) == ""
    referrals.silence(OWNER)
    assert referrals.mention(db.get_client(OWNER), referrals.SETUP_BIT) == ""


def test_erase_removes_credits_but_not_ever_client():
    inviter = _inviter()
    with db.conn() as c:
        c.execute("INSERT INTO referral_credits(phone, pct, created_at) VALUES(?,?,?)", (OWNER, 25, db.now_utc_iso()))
    db.erase_client(OWNER)
    assert not referrals.pending_credits(OWNER)
    assert referrals._was_client(OWNER)


def test_receipt_matches_discounted_amount_for_referred_friend():
    from app import receipt
    inviter = _inviter()
    _say(FRIEND, referrals.code_of(inviter))
    friend = db.get_client(FRIEND)
    now = db.now_utc()
    code = billing.ensure_code(friend)
    ex = receipt.Extracted(is_receipt=True, amount=1450, currency="TRY", date=now.astimezone(billing._tz()).strftime("%Y-%m-%d"),
                           iban=config.BANK_IBAN, description=code, reference="REF123456")
    verdict = receipt.verify(friend, ex, now, "img1")
    assert verdict.status == "applied" and verdict.plan == "basic"
    ex.amount = 1500  # the undiscounted price no longer matches
    assert receipt.verify(friend, ex, now, "img2").status == "review"
