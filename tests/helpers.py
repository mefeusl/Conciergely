"""Small helpers shared by the newer test files."""
from app import db, intake, whatsapp, worker
from app.models import Incoming

_n = [1000]


def say(text, phone="+905551234567", kind="text", **extra):
    """Deliver one message through the full pipeline and return everything the bot sent back."""
    _n[0] += 1
    whatsapp.OUTBOX.clear()
    intake.intake([Incoming(id=f"wamid.h{_n[0]}", phone=phone, type=kind, text=text, **extra)])
    drain()
    return [t for (_, t) in whatsapp.OUTBOX]


def drain():
    """Run the queue dry, treating short delays (a reply that is sent a moment later) as already due."""
    for _ in range(10):
        while worker.process_one():
            pass
        with db.conn() as c:
            moved = c.execute("UPDATE jobs SET run_at=? WHERE status='pending' AND run_at>?",
                              (db.now_utc_iso(), db.now_utc_iso())).rowcount
        if not moved:
            break


def open_window(phone="+905551234567"):
    db.set_client_fields(phone, last_inbound_at=db.now_utc_iso())
