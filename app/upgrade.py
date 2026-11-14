"""'PLUS': a Basic client (or one on the free trial) asks to upgrade. The owner sees the request in /admin."""
from __future__ import annotations

from . import billing, config, db, ops, plans, settings

WORDS = {"plus", "plus istiyorum", "plus paket", "plus paketi", "plusa gec", "plus'a gec", "upgrade", "upgrade to plus",
         "yukselt", "paketi yukselt", "plus olmak istiyorum", "i want plus"}
LITE_WORDS = {"basic", "basic istiyorum", "basic paket", "basica gec", "basic'e gec", "i want basic", "upgrade to basic"}

TEXT = {
    "tr": {"thanks": "Harika! Plus isteğinizi aldım, size en kısa sürede dönüş yapılacak. Plus'ta sabah sesli brifing, hisse senaryoları (bir hissenin geçmişini binlerce şekilde oynatıp olası sonuçları gösterir; tahmin değil, stres testi), "
                     "grafik taraması ve e-posta asistanı sizi bekliyor.",
           "have": "Zaten Plus paketindesiniz, her şey açık.",
           "trial": "Plus denemeniz sürüyor, her şey açık. Kalmak isterseniz isteğinizi not aldım, size dönüş yapılacak."},
    "en": {"thanks": "Great! I have your Plus request and you will hear back soon. Plus gives you the morning voice briefing, "
                     "stock scenarios (it replays a stock's own past thousands of ways to show what could happen, a stress test and not a prediction), chart scans and the email assistant.",
           "have": "You are already on Plus, everything is on.",
           "trial": "Your Plus trial is running, everything is on. I noted that you want to stay and you will hear back."},
}


def handle(client: dict, cmd: str) -> str | None:
    if cmd not in WORDS and not (cmd in LITE_WORDS and plans.raw_plan(client) == "lite"):
        return None
    lang = "en" if client["lang"] == "en" else "tr"
    if plans.raw_plan(client) == "plus":
        return TEXT[lang]["have"]
    with db.conn() as c:
        open_request = c.execute("SELECT 1 FROM upgrade_requests WHERE phone=? AND handled=0", (client["phone"],)).fetchone()
        if not open_request:
            c.execute("INSERT INTO upgrade_requests(phone, at) VALUES(?,?)", (client["phone"], db.now_utc_iso()))
    if not open_request:
        ops.event("upgrade", f"{client.get('name') or 'A client'} ({client['phone']}) asked for Plus",
                  dedup_key=f"upgrade:{client['phone']}:{db.now_utc_iso()[:10]}")
    text = TEXT[lang]["trial" if plans.trial_active(client) else "thanks"]
    if config.BILLING_ON and billing.configured():  # the way to upgrade is to pay the Plus amount
        text += "\n\n" + billing.how_to_pay(client, "basic" if plans.raw_plan(client) == "lite" else "plus")
    return text


def pending() -> list[dict]:
    with db.conn() as c:
        rows = c.execute("SELECT u.id, u.phone, u.at, COALESCE(cl.name,'') AS name FROM upgrade_requests u "
                         "LEFT JOIN clients cl ON cl.phone=u.phone WHERE u.handled=0 ORDER BY u.id").fetchall()
    return [dict(r) for r in rows]


def mark_handled(request_id: int) -> bool:
    with db.conn() as c:
        return c.execute("UPDATE upgrade_requests SET handled=1 WHERE id=?", (request_id,)).rowcount > 0
