"""Check 2 of the launch audit: one client can never read, change or delete another client's records."""
import pytest

from app import db, journal, memory, portfolio

A, B = "+905551110001", "+905552220002"


@pytest.fixture
def two():
    db.upsert_client(A, name="Ayşe")
    db.upsert_client(B, name="Bora")
    return A, B


def test_memory_is_per_client(two):
    memory.add_fact(A, "notes", "Ayşe'nin kedisi Pamuk")
    assert memory.count(B) == 0 and "Pamuk" not in memory.render(B)
    assert memory.forget(B, "Pamuk") == 0 and memory.count(A) == 1
    memory.forget_all(B)
    assert memory.count(A) == 1


def test_memory_file_name_cannot_escape_the_folder(two):
    from app import config
    for evil in ("../../etc/passwd", "+90/../../x", "..\\..\\x"):
        try:
            path = memory.path_for(evil)
        except ValueError:
            continue
        assert str(path.resolve()).startswith(str(__import__("pathlib").Path(config.MEMORY_DIR).resolve()))


def test_history_and_clients_are_per_client(two):
    db.add_message(A, "user", "private to A")
    db.add_message(A, "assistant", "reply to A")
    assert db.get_history(B) == [] and any("private to A" in str(m) for m in db.get_history(A))


def test_reminders_cannot_be_listed_or_cancelled_by_another_client(two):
    rid = db.add_reminder(A, "A's secret reminder", "2099-01-01T10:00:00Z")
    assert db.list_pending_reminders(B) == [] and len(db.list_pending_reminders(A)) == 1
    assert db.cancel_reminder(B, rid) is False
    assert db.cancel_reminder(A, rid) is True


def test_trades_and_journal_are_private(two):
    assert "error" not in portfolio.add_trade(A, "AAPL", "buy", 10, 100.0)
    assert portfolio.positions(B) == {}
    with db.conn() as c:
        trade_id = c.execute("SELECT id FROM trades WHERE phone=?", (A,)).fetchone()["id"]
    assert portfolio.delete_trade(B, trade_id) is False
    assert portfolio.positions(A)["AAPL"]["qty"] == 10
    entry = journal.add(A, "AAPL", "A thinks it rises", 60)
    assert journal.list_open(B) == []
    assert journal.close(B, entry["id"], 120.0) == {"error": "no such journal entry"}
    assert len(journal.list_open(A)) == 1


def test_watchlist_is_private(two):
    db.add_watch(A, "AAPL", 200.0, None, None)
    assert db.list_watch(B) == [] and len(db.list_watch(A)) == 1
