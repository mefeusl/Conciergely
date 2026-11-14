"""Lite plan: 500 TL, plain chat, no tools, no media, no trial."""
from app import billing, config, db, i18n, plans, upgrade, adminlogic, assistant


def _client(plan="lite", **kw):
    c = {"phone": "+905550000001", "plan": plan, "lang": "tr", "name": "Ali", "trial_ends_at": "", "paid_months": 0,
         "setup_paid_tl": 0}
    c.update(kw)
    return c


def test_plan_order_and_price():
    assert plans.PLANS == ("lite", "basic", "plus")
    assert billing.monthly("lite") == 500
    assert billing.setup_fee("lite") == config.SETUP_LITE == 0
    assert billing.amount_due(_client(), "lite") == 250  # half price first month


def test_lite_never_gets_plus_trial_and_has_nothing():
    c = _client(trial_ends_at="2999-01-01T00:00:00+00:00")
    assert plans.plan_of(c) == "lite"
    assert not plans.has_feature(c, "trading") and not plans.has_feature(c, "mail")
    assert plans.visible_tools([{"name": "set_reminder"}, {"name": "get_quote"}], c) == []
    assert plans.gate(c, "set_reminder")


def test_basic_unchanged():
    assert plans.plan_of(_client("basic")) == "basic"
    assert plans.visible_tools([{"name": "set_reminder"}], _client("basic"))


def test_questions_for_lite():
    keys = [q["key"] for q in i18n.questions_for("lite")]
    assert keys == ["name_lang", "style", "goals"]


def test_upward_amounts_in_how_to_pay(monkeypatch):
    monkeypatch.setattr(config, "BANK_IBAN", "TR000000000000000000000001")
    monkeypatch.setattr(config, "BANK_HOLDER", "X")
    monkeypatch.setattr(db, "set_client_fields", lambda *a, **k: None)
    text = billing.how_to_pay(_client(pay_code="CG-00001"), "lite")
    assert "Lite:" in text and "Basic:" in text and "Plus:" in text
    text = billing.how_to_pay(_client("plus", pay_code="CG-00001"), "plus")
    assert "Lite:" not in text


def test_lite_system_prompt_is_short_and_toolless():
    c = _client()
    c["preferences"] = ""
    system = assistant.build_system(c)
    assert "no tools" in system and "Playbooks" not in system and len(system) < 2500


def test_upgrade_words():
    assert upgrade.LITE_WORDS and "basic" not in upgrade.WORDS
