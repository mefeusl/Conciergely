"""Shared reminders: a client can have a reminder ALSO sent to another client (a partner, a parent, a colleague).

Consent first: nothing reaches the other person until THEY write 'kabul' / 'accept' to a request. Only people who are
already clients can receive (we never message strangers on someone's say-so; they can be invited with 'davet').
The other person can end it any time ('paylaşımı bitir'). Sharing is one-way: it never shows the member's own data
to the owner. A reminder keeps going to the member on each repeat while the link stays accepted.
"""
from __future__ import annotations

import logging

from . import db, jobs

log = logging.getLogger(__name__)

MAX_MEMBERS = 5
ACCEPT = {"kabul", "kabul ediyorum", "accept", "yes share", "evet kabul"}
DECLINE = {"reddet", "istemiyorum", "decline", "no thanks", "hayir kabul etmiyorum"}
END = {"paylasimi bitir", "paylasimlari bitir", "stop sharing", "end sharing"}
LIST = {"paylasimlar", "paylasimlarim", "shares", "my shares", "sharing"}

TEXT = {
    "tr": {"ask": "{who} sizinle hatırlatmaları paylaşmak istiyor: onun seçtiği hatırlatmalar size de gelir. Kabul için 'kabul', istemezseniz 'reddet' yazın. Dilediğiniz an 'paylaşımı bitir' yazabilirsiniz.",
           "accepted": "Tamam, {who} ile hatırlatma paylaşımı açık.", "declined": "Tamam, paylaşım isteği reddedildi.",
           "ended": "Paylaşımlar sona erdi.", "none": "Şu an açık bir paylaşım yok.",
           "member_ok": "Bu hatırlatma {who} tarafından sizinle paylaşıldı: {text}",
           "list_in": "Sizinle paylaşanlar: {names}", "list_out": "Sizin paylaştıklarınız: {names}",
           "sent": "{who} kişisine paylaşım isteği gönderdim; kabul edince hatırlatma ona da gider.",
           "cannot": "Bu numaraya paylaşım isteği gönderemedim. Kişinin bu hizmeti kullanıyor olması gerekir; 'davet' yazarak davet edebilirsiniz.",
           "full": "En fazla {n} kişiyle paylaşabilirsiniz."},
    "en": {"ask": "{who} would like to share reminders with you: the ones they choose will reach you too. Reply 'accept' to agree or 'decline' if not. You can write 'stop sharing' any time.",
           "accepted": "OK, reminder sharing with {who} is on.", "declined": "OK, the sharing request was declined.",
           "ended": "All sharing has ended.", "none": "There is no active sharing right now.",
           "member_ok": "{who} shared this reminder with you: {text}",
           "list_in": "People sharing with you: {names}", "list_out": "People you share with: {names}",
           "sent": "I sent {who} a sharing request; once they accept, the reminder reaches them too.",
           "cannot": "I couldn't send a sharing request to that number. They need to be using this service; write 'invite' to invite them.",
           "full": "You can share with at most {n} people."},
}


def _lang(client: dict | None) -> str:
    return "en" if (client or {}).get("lang") == "en" else "tr"


def _who(client: dict) -> str:
    return (client.get("name") or "").split(" ")[0] or "Someone"


def link(owner: str, member: str) -> dict | None:
    with db.conn() as c:
        row = c.execute("SELECT * FROM share_links WHERE owner=? AND member=?", (owner, member)).fetchone()
    return dict(row) if row else None


def is_accepted(owner: str, member: str) -> bool:
    row = link(owner, member)
    return bool(row and row["status"] == "accepted")


def request(owner: dict, member_phone: str) -> str:
    """Ask `member_phone` to accept sharing. Returns 'sent', 'accepted' (already), 'declined', 'cannot' or 'full'."""
    member_phone = db.norm_phone(member_phone)
    member = db.get_client(member_phone) if db.valid_phone(member_phone) else None
    if not member or not member["active"] or member["frozen"] or member_phone == owner["phone"] or not member["onboarding_done"]:
        return "cannot"
    existing = link(owner["phone"], member_phone)
    if existing:
        return {"accepted": "accepted", "declined": "declined"}.get(existing["status"], "sent")
    with db.conn() as c:
        count = c.execute("SELECT COUNT(*) AS n FROM share_links WHERE owner=?", (owner["phone"],)).fetchone()["n"]
        if count >= MAX_MEMBERS:
            return "full"
        c.execute("INSERT INTO share_links(owner, member, created_at) VALUES(?,?,?)", (owner["phone"], member_phone, db.now_utc_iso()))
    jobs.enqueue("send", member_phone, {"text": TEXT[_lang(member)]["ask"].format(who=_who(owner)), "proactive": True},
                 dedup_key=f"shareask:{owner['phone']}:{member_phone}")
    return "sent"


def deliver_copy(reminder: dict) -> None:
    """A main reminder with shared_with just fired: send the copy if (and only if) the member accepted."""
    owner_phone, member_phone = reminder["phone"], reminder.get("shared_with") or ""
    if not member_phone or not is_accepted(owner_phone, member_phone):
        return
    owner, member = db.get_client(owner_phone), db.get_client(member_phone)
    if not owner or not member or not member["active"] or member["frozen"]:
        return
    text = TEXT[_lang(member)]["member_ok"].format(who=_who(owner), text=reminder["text"])
    jobs.enqueue("send", member_phone, {"text": text, "proactive": True, "respect_quiet": True},
                 dedup_key=f"sharecopy:{reminder['id']}")


def handle(client: dict, cmd: str) -> str | None:
    phone, t = client["phone"], TEXT[_lang(client)]
    if cmd in ACCEPT or cmd in DECLINE:
        with db.conn() as c:
            rows = c.execute("SELECT * FROM share_links WHERE member=? AND status='pending' ORDER BY id DESC", (phone,)).fetchall()
            if not rows:
                return None  # a plain 'kabul' in normal chat
            status = "accepted" if cmd in ACCEPT else "declined"
            c.execute("UPDATE share_links SET status=? WHERE member=? AND status='pending'", (status, phone))
        names = ", ".join(_who(db.get_client(r["owner"]) or {}) for r in rows)
        return t["accepted"].format(who=names) if status == "accepted" else t["declined"]
    if cmd in END:
        with db.conn() as c:
            n = c.execute("DELETE FROM share_links WHERE owner=? OR member=?", (phone, phone)).rowcount
            c.execute("UPDATE reminders SET shared_with='' WHERE phone=?", (phone,))
        return t["ended"] if n else t["none"]
    if cmd in LIST:
        with db.conn() as c:
            incoming = [r["owner"] for r in c.execute("SELECT owner FROM share_links WHERE member=? AND status='accepted'", (phone,))]
            outgoing = [r["member"] for r in c.execute("SELECT member FROM share_links WHERE owner=? AND status='accepted'", (phone,))]
        if not incoming and not outgoing:
            return t["none"]
        parts = []
        if incoming:
            parts.append(t["list_in"].format(names=", ".join(_who(db.get_client(p) or {}) for p in incoming)))
        if outgoing:
            parts.append(t["list_out"].format(names=", ".join(_who(db.get_client(p) or {}) for p in outgoing)))
        return "\n".join(parts)
    return None
