"""Referrals and the reply to unknown numbers.

  * A number that is not a client gets ONE short reply per 24 h (what this is, the website, "paste a referral code").
  * A message containing a valid code (REF-4821) from a number that was never a client registers it automatically
    (Basic plan, the usual free Plus trial) and links it to the inviter. No code, no registration.
  * The inviter earns only when the friend makes a first real payment: a discount credit (ladder in config.REF_LADDER).
    Credits stack (capped at 100 % per payment) and carry over. The friend gets REF_FRIEND_PCT off the first month.
  * Discounts apply to the monthly price only, never below 0 (billing.amount_due calls discount_pct).
  * Anti-abuse: numbers that were ever clients (even erased ones, kept as a one-way hash) cannot register again,
    no self-referral, REF_SIGNUPS_PER_DAY automatic sign-ups a day.
"""
from __future__ import annotations

import logging
import re
from datetime import timedelta
from urllib.parse import quote

from . import config, db, jobs, plans

log = logging.getLogger(__name__)

CODE_RE = re.compile(r"\bREF[-\s]?(\d{4,6})\b", re.IGNORECASE)
INVITE_WORDS = {"davet", "invite", "davet kodum", "davet kodu", "referral", "referans", "referans kodum", "my code", "arkadasimi davet et"}
OFF_BIT, INTRO_BIT, SETUP_BIT, PAID_BIT, TRIAL_BIT = 128, 1, 2, 4, 8  # clients.ref_notes: which mentions were made; OFF = 'dur'
REPLY_HOURS = 24
UNKNOWN_PER_HOUR = 120  # a global brake on replies to strangers (they cost nothing but spam is spam)

TEXT = {
    "tr": {
        "unknown": ("Merhaba! Ben {bot}, WhatsApp üzerinden çalışan ücretli bir kişisel asistan uygulamasıyım. Daha fazla bilgi: {site}\n\n"
                    "Bir davet kodunuz varsa (örnek: REF-1234) aşağıya yazın, sizi hemen kaydedeyim."),
        "bad_code": "Bu davet kodu geçerli görünmüyor. Kodu kontrol edip tekrar yazabilirsiniz. Bilgi: {site}",
        "full": "Şu an yeni kayıt kapasitemiz dolu, birkaç gün sonra tekrar yazar mısınız? Bilgi: {site}",
        "invite": ("Davet kodunuz: {code}\nArkadaşınız bu bağlantıya dokunup yazarsa kod otomatik gelir:\n{link}\n\n"
                   "Arkadaşınız ilk ödemesini yapınca bir sonraki ayınızda indirim kazanırsınız ve arkadaşınız ilk aya %{friend} indirim alır. "
                   "Seviyeniz: {level} (ödeme yapan arkadaş: {n}). Bir sonraki arkadaşınız için kazanç: %{next}.{credit}"),
        "credit_line": " Bekleyen indiriminiz: %{pct}.",
        "reward": ("Güzel haber: bir arkadaşınız ilk ödemesini yaptı! Bir sonraki ödemenizde %{pct} indirim kazandınız. "
                   "Ödeme yapan arkadaşınız: {n}. Bir sonrakinde kazanç %{next}. Detay için 'davet' yazın."),
        "mention": "Not: arkadaşlarınızı davet ederseniz her ödeme yapan arkadaşınız için indirim kazanırsınız. Kodunuz için 'davet' yazın.",
    },
    "en": {
        "unknown": ("Hello! I'm {bot}, a paid personal assistant app that works on WhatsApp. Learn more: {site}\n\n"
                    "If you have a referral code (for example REF-1234), paste it below and I'll sign you up right away."),
        "bad_code": "That referral code does not look valid. Please check it and write it again. More info: {site}",
        "full": "We are not taking new sign-ups at this moment. Could you write again in a few days? More info: {site}",
        "invite": ("Your referral code: {code}\nWhen a friend taps this link and writes, the code comes along:\n{link}\n\n"
                   "When your friend makes their first payment you earn a discount on your next month, and your friend gets {friend}% off their first month. "
                   "Your level: {level} (friends who paid: {n}). Reward for your next friend: {next}%.{credit}"),
        "credit_line": " Your pending discount: {pct}%.",
        "reward": ("Good news: a friend of yours made their first payment! You earned {pct}% off your next payment. "
                   "Friends who paid: {n}. The next one earns {next}%. Write 'invite' for details."),
        "mention": "By the way: invite friends and earn a discount for every friend who pays. Write 'invite' for your code.",
    },
}


def _lang(client: dict | None) -> str:
    return "en" if (client or {}).get("lang") == "en" else "tr"


def _site() -> str:
    return config.CONTACT_WEBSITE.strip() or "example.com"


# ---------- the ladder ----------

def ladder() -> list[tuple[int, int]]:
    """[(from friend number, percent)] ascending, parsed from config.REF_LADDER; falls back to the default if broken."""
    out: list[tuple[int, int]] = []
    for part in config.REF_LADDER.split(","):
        try:
            n, pct = part.split(":")
            n, pct = int(n), int(pct)
        except ValueError:
            continue
        if n >= 1 and 0 < pct <= 100:
            out.append((n, pct))
    return sorted(out) or [(1, 25), (3, 35), (6, 50)]


def pct_for(friend_number: int) -> int:
    pct = 0
    for start, value in ladder():
        if friend_number >= start:
            pct = value
    return pct


def paid_friends(phone: str) -> int:
    with db.conn() as c:
        return int(c.execute("SELECT COUNT(*) AS n FROM referrals WHERE referrer=? AND rewarded_at!=''", (phone,)).fetchone()["n"])


# ---------- codes ----------

def code_of(client: dict) -> str:
    if client.get("ref_code"):
        return client["ref_code"]
    import secrets
    with db.conn() as c:
        taken = {r["ref_code"] for r in c.execute("SELECT ref_code FROM clients WHERE ref_code!=''")}
    digits = 4
    code = ""
    for attempt in range(200):
        if attempt == 100:
            digits = 5  # the four-digit space is nearly full
        code = "REF-%0*d" % (digits, secrets.randbelow(10 ** digits))
        if code not in taken:
            break
    db.set_client_fields(client["phone"], ref_code=code)
    client["ref_code"] = code
    return code


def find_code(text: str) -> str:
    m = CODE_RE.search(text or "")
    return f"REF-{m.group(1)}" if m else ""


def owner_of(code: str) -> dict | None:
    if not code:
        return None
    with db.conn() as c:
        row = c.execute("SELECT * FROM clients WHERE ref_code=? AND active=1", (code,)).fetchone()
    return dict(row) if row else None


def link_for(client: dict) -> str:
    number = "".join(ch for ch in config.CONTACT_WHATSAPP if ch.isdigit())
    return f"https://wa.me/{number}?text=" + quote(f"Merhaba {code_of(client)}") if number else code_of(client)


# ---------- strangers ----------

def signups_today() -> int:
    day = db.now_utc().strftime("%Y-%m-%d")
    with db.conn() as c:
        return int(c.execute("SELECT COUNT(*) AS n FROM referrals WHERE created_at>=?", (day,)).fetchone()["n"])


def _was_client(phone: str) -> bool:
    with db.conn() as c:
        return c.execute("SELECT 1 FROM ever_clients WHERE phash=?", (db.phash(phone),)).fetchone() is not None


def try_signup(phone: str, text: str) -> tuple[dict | None, str]:
    """A stranger wrote `text`. Returns (new client, 'ok') or (None, reason): no_code, bad_code, full, off."""
    if not config.REFERRALS_ON:
        return None, "off"
    code = find_code(text)
    if not code:
        return None, "no_code"
    owner = owner_of(code)
    if not owner or owner["phone"] == db.norm_phone(phone) or _was_client(phone):
        return None, "bad_code"
    if signups_today() >= config.REF_SIGNUPS_PER_DAY:
        return None, "full"
    from . import adminlogic  # local: adminlogic imports billing which imports this module
    ok, _ = adminlogic.add_client(phone, "", "basic")
    if not ok:
        return None, "bad_code"
    phone = db.norm_phone(phone)
    db.set_client_fields(phone, referred_by=owner["phone"], lang="en" if re.search(r"\b(hello|hi|hey)\b", text, re.I) else "tr")
    try:
        with db.conn() as c:
            c.execute("INSERT INTO referrals(referrer, friend, created_at) VALUES(?,?,?)",
                      (owner["phone"], phone, db.now_utc_iso()))
    except Exception:  # the friend row exists already (a double delivery); the client is created all the same
        log.info("referral row already present")
    return db.get_client(phone), "ok"


def stranger_reply(phone: str, status: str) -> bool:
    """Queue the canned reply (once per REPLY_HOURS per number). Returns True if one was queued."""
    if not config.REFERRALS_ON or status == "ok":
        return False
    now = db.now_utc()
    h = db.phash(phone)
    key = {"bad_code": "bad_code", "full": "full"}.get(status, "unknown")
    with db.conn() as c:
        row = c.execute("SELECT last_reply_at FROM unknown_contacts WHERE phash=?", (h,)).fetchone()
        last = db.from_iso(row["last_reply_at"]) if row else None
        if last and now - last < timedelta(hours=REPLY_HOURS) and key == "unknown":
            return False
        if last and now - last < timedelta(minutes=10):  # even a wrong code gets at most one answer per 10 minutes
            return False
        c.execute("DELETE FROM unknown_contacts WHERE last_reply_at<?", (db.to_iso(now - timedelta(days=7)),))  # keeps the table small
        recent = c.execute("SELECT COUNT(*) AS n FROM unknown_contacts WHERE last_reply_at>=?",
                           (db.to_iso(now - timedelta(hours=1)),)).fetchone()["n"]
        if recent >= UNKNOWN_PER_HOUR:
            log.warning("stranger replies are capped this hour")
            return False
        c.execute("INSERT INTO unknown_contacts(phash, last_reply_at) VALUES(?,?) "
                  "ON CONFLICT(phash) DO UPDATE SET last_reply_at=excluded.last_reply_at", (h, db.to_iso(now)))
    both = "\n\n— — —\n\n".join(TEXT[l][key].format(bot=config.BOT_NAME, site=_site()) for l in ("tr", "en"))
    return jobs.enqueue("send", db.norm_phone(phone), {"text": both, "direct": True},
                        dedup_key=f"stranger:{h[:16]}:{now.strftime('%Y%m%d%H%M')}") is not None


# ---------- discounts ----------

def pending_credits(phone: str) -> list[dict]:
    with db.conn() as c:
        rows = c.execute("SELECT * FROM referral_credits WHERE phone=? AND used_at='' ORDER BY id", (phone,)).fetchall()
    return [dict(r) for r in rows]


def discount(client: dict) -> tuple[int, list[int]]:
    """(percent off the next month, ids of the credits that make it up). Friend bonus + stacked credits, max 100."""
    pct, ids = 0, []
    if client.get("referred_by") and not int(client.get("paid_months") or 0):
        pct += config.REF_FRIEND_PCT
    for credit in pending_credits(client["phone"]):
        if pct >= 100:
            break
        pct += credit["pct"]
        ids.append(credit["id"])
    return min(pct, 100), ids


def discount_pct(client: dict) -> int:
    return discount(client)[0]


def consume(ids: list[int]) -> None:
    now = db.now_utc_iso()
    with db.conn() as c:
        for i in ids:
            c.execute("UPDATE referral_credits SET used_at=? WHERE id=? AND used_at=''", (now, i))


def on_payment(before: dict, amount: int) -> None:
    """Called after a payment was applied to `before` (the client as it was). First real payment of a referred friend pays the inviter."""
    if amount <= 0 or int(before.get("paid_months") or 0) > 0 or not before.get("referred_by"):
        return
    referrer = db.get_client(before["referred_by"])
    if not referrer:
        return
    with db.conn() as c:
        done = c.execute("UPDATE referrals SET rewarded_at=? WHERE friend=? AND referrer=? AND rewarded_at=''",
                         (db.now_utc_iso(), before["phone"], referrer["phone"])).rowcount
        if not done:
            return
        n = int(c.execute("SELECT COUNT(*) AS n FROM referrals WHERE referrer=? AND rewarded_at!=''", (referrer["phone"],)).fetchone()["n"])
        pct = pct_for(n)
        c.execute("INSERT INTO referral_credits(phone, pct, friend, created_at) VALUES(?,?,?,?)",
                  (referrer["phone"], pct, before["phone"], db.now_utc_iso()))
    lang = _lang(referrer)
    text = TEXT[lang]["reward"].format(pct=pct, n=n, next=pct_for(n + 1))
    jobs.enqueue("send", referrer["phone"], {"text": text, "proactive": True}, dedup_key=f"refreward:{before['phone']}")


# ---------- the client's side ----------

def level(n: int) -> int:
    return sum(1 for start, _ in ladder() if n >= start) or 1


def invite_text(client: dict) -> str:
    lang = _lang(client)
    n = paid_friends(client["phone"])
    pending = sum(c["pct"] for c in pending_credits(client["phone"]))
    return TEXT[lang]["invite"].format(
        code=code_of(client), link=link_for(client), friend=config.REF_FRIEND_PCT, level=level(n), n=n,
        next=pct_for(n + 1), credit=TEXT[lang]["credit_line"].format(pct=min(pending, 100)) if pending else "")


def handle(client: dict, cmd: str) -> str | None:
    if cmd in INVITE_WORDS and config.REFERRALS_ON:
        return invite_text(client)
    return None


def mention(client: dict, bit: int) -> str:
    """A short pointer to the referral program for a natural moment; once per moment, never after 'dur'. Returns '' or '\\n\\n...'."""
    if not config.REFERRALS_ON:
        return ""
    fresh = db.get_client(client["phone"]) or client
    notes = int(fresh.get("ref_notes") or 0)
    if notes & (OFF_BIT | bit):
        return ""
    db.set_client_fields(client["phone"], ref_notes=notes | bit)
    return "\n\n" + TEXT[_lang(client)]["mention"]


def silence(phone: str) -> None:
    """The client wrote 'dur': no more referral mentions."""
    client = db.get_client(phone)
    if client:
        db.set_client_fields(phone, ref_notes=int(client.get("ref_notes") or 0) | OFF_BIT)


def admin_rows() -> list[dict]:
    """Per inviter: code, friends, friends who paid, pending discount (for /admin)."""
    out = []
    with db.conn() as c:
        rows = c.execute("SELECT r.referrer, COUNT(*) AS friends, SUM(r.rewarded_at!='') AS paid FROM referrals r GROUP BY r.referrer").fetchall()
    for r in rows:
        who = db.get_client(r["referrer"]) or {}
        out.append({"name": who.get("name", ""), "phone": r["referrer"], "code": who.get("ref_code", ""),
                    "friends": r["friends"], "paid": r["paid"] or 0,
                    "pending": min(100, sum(c["pct"] for c in pending_credits(r["referrer"])))})
    return sorted(out, key=lambda x: (-x["paid"], -x["friends"]))
