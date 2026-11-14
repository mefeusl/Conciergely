"""First-time setup: greets a new client and asks the few short, optional setup questions (see i18n.QUESTIONS), one at a time.

State lives in the clients table: onboarding_step 0 = not started, 1..N = waiting for that answer,
onboarding_done = 1 when finished (or skipped). Answers go into the client's memory map; the stock
answer is also turned into watchlist entries by Claude (with the same tools used in normal chat).
"""
from __future__ import annotations

from . import assistant, config, db, i18n, lifecycle, memory, plans, quiet, referrals, trial, voicechat

STOCK_INSTRUCTION = (
    "The client is answering the setup question about which stocks they follow and at which price limits to "
    "alert them. For every stock they name, call watch_stock (above = upper limit, below = lower limit). If they "
    "named a stock without limits, call watch_stock with no limits. If they follow nothing, call no tools. Reply "
    "with ONE or TWO short sentences confirming what you set up, and nothing else."
)


YES_WORDS = {"evet", "yes", "olur", "tamam", "ok", "okay", "isterim", "istiyorum", "please", "sure"}


def _finish(phone: str, total: int) -> None:
    db.set_client_fields(phone, onboarding_done=1, onboarding_step=total + 1)
    lifecycle.queue_wow(phone)  # the first-day message arrives a few seconds after the final setup reply


def _briefing_answer(text: str) -> tuple[int | None, bool]:
    """(minutes, understood): minutes is None when the client does not want a briefing."""
    cmd = i18n.clean(text)
    minutes = quiet.parse_one(text)
    if minutes is not None:
        return minutes, True
    if cmd in YES_WORDS:
        return config.BRIEFING_DEFAULT_MINUTES, True
    if quiet.says_none(text) or cmd in i18n.SKIP:
        return None, True
    return None, False


def handle(client: dict, text: str) -> str:
    """Process one message from a client who has not finished setup. Returns the reply to send."""
    phone, lang, step = client["phone"], client["lang"], client["onboarding_step"]
    cmd = i18n.clean(text)
    plan = plans.plan_of(client)
    total = i18n.total_for(plan)

    if cmd in i18n.SKIP_ALL:
        _finish(phone, total)
        return i18n.t(lang, "done")

    if step <= 0:  # first contact: greet and ask question 1
        db.set_client_fields(phone, onboarding_step=1)
        intro = i18n.t(lang, "greeting", total=total)
        if client.get("trial_ends_at") and plans.raw_plan(client) != "plus" and plans.trial_active(client):
            intro += "\n\n" + trial.started_text(client)
        offer = voicechat.offer_text(client)
        return intro + ("\n\n" + offer if offer else "") + "\n\n" + i18n.question(lang, 1, plan)

    step = min(step, total)
    q = i18n.questions_for(plan)[step - 1]
    notes: list[str] = []

    if q["key"] == "briefing":  # Plus only: when should the morning voice note arrive?
        minutes, understood = _briefing_answer(text)
        if not understood:
            return i18n.t(lang, "briefing_unparsed")
        db.set_client_fields(phone, briefing_min=-1 if minutes is None else minutes)
        if minutes is None:
            notes.append(i18n.t(lang, "briefing_off"))
        else:
            notes.append(i18n.t(lang, "briefing_set", time=quiet.fmt(minutes)))
            memory.add_fact(phone, q["section"], f"{q['label']}: {quiet.fmt(minutes)}")
    elif q["key"] == "work":  # Plus only: the field of work decides which news the briefing adds
        if cmd not in i18n.SKIP and not quiet.says_none(text):
            area = " ".join(text.split())[:80]
            result = memory.add_fact(phone, q["section"], f"{q['label']}: {area}")
            if result["status"] == "refused":
                notes.append(i18n.t(lang, "sensitive_skip"))
            else:
                db.set_client_fields(phone, work_area=area)
    elif cmd not in i18n.SKIP:
        answer = " ".join(text.split())[:250]
        if q["key"] == "name_lang":
            lang = i18n.lang_from_answer(text, lang)
            db.set_client_fields(phone, lang=lang)
            client = {**client, "lang": lang}
        if q["key"] == "quiet":
            hours = quiet.parse(text)
            if hours:
                db.set_client_fields(phone, quiet_start=hours[0], quiet_end=hours[1])
            elif not quiet.says_none(text):
                notes.append(i18n.t(lang, "quiet_unparsed"))
        if q["key"] == "stocks":
            notes.append(assistant.reply(client, text, extra=STOCK_INSTRUCTION))
        result = memory.add_fact(phone, q["section"], f"{q['label']}: {answer}")
        if result["status"] == "refused":
            notes.append(i18n.t(lang, "sensitive_skip"))

    nxt = step + 1
    if nxt > total:
        _finish(phone, total)
        notes.append(i18n.t(lang, "done") + referrals.mention(client, referrals.SETUP_BIT))
    else:
        db.set_client_fields(phone, onboarding_step=nxt)
        notes.append(i18n.question(lang, nxt, plan))
    return "\n\n".join(n for n in notes if n)
