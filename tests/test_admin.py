import base64
import time

import pytest

from app import adminlogic, config, db, jobs, memory, pages, security, whatsapp, worker

P = "+905551234567"


def basic(user="admin", password="correct-horse-battery"):
    return "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()


# ---------- login ----------

def test_correct_credentials_accepted():
    assert security.check_admin_credentials(basic()) is True


@pytest.mark.parametrize("header", [
    None, "", "Bearer abc", "Basic", "Basic !!!notbase64", basic(password="wrong"), basic(user="root"),
    basic(password=""), basic(password="correct-horse-battery "), "Basic " + base64.b64encode(b"\xff\xfe:x").decode(),
    "Basic " + base64.b64encode(b"no-colon").decode(),
])
def test_bad_credentials_rejected(header):
    assert security.check_admin_credentials(header) is False


def test_admin_disabled_without_password(monkeypatch):
    monkeypatch.setattr(config, "ADMIN_PASSWORD", "")
    assert security.check_admin_credentials(basic(password="")) is False
    assert security.csrf_ok("anything") is False


def test_password_may_contain_colons():
    config.ADMIN_PASSWORD = "a:b:c-very-long"
    assert security.check_admin_credentials(basic(password="a:b:c-very-long")) is True


def test_csrf_token_checks():
    token = security.csrf_token()
    assert security.csrf_ok(token) and not security.csrf_ok("") and not security.csrf_ok(None)
    assert not security.csrf_ok(token[:-1] + ("0" if token[-1] != "0" else "1"))


def test_origin_must_match_host():
    assert security.origin_ok(None, "bot.example.com") is True
    assert security.origin_ok("https://bot.example.com", "bot.example.com") is True
    assert security.origin_ok("https://evil.example.net", "bot.example.com") is False
    assert security.origin_ok("https://bot.example.com.evil.net", "bot.example.com") is False
    assert security.origin_ok("https://bot.example.com", None) is False


def test_login_failures_are_throttled_per_address(monkeypatch):
    limiter = security.FailureLimiter(max_failures=3, window_seconds=900)
    for _ in range(3):
        assert limiter.blocked("1.2.3.4") is False
        limiter.record_failure("1.2.3.4")
    assert limiter.blocked("1.2.3.4") is True and limiter.blocked("5.6.7.8") is False
    limiter.reset("1.2.3.4")
    assert limiter.blocked("1.2.3.4") is False


def test_throttle_expires_after_the_window():
    limiter = security.FailureLimiter(max_failures=1, window_seconds=0.05)
    limiter.record_failure("x")
    assert limiter.blocked("x") is True
    time.sleep(0.08)
    assert limiter.blocked("x") is False


# ---------- admin actions ----------

def test_add_client_normalises_and_validates():
    assert adminlogic.add_client("0555 123 45 67", "  Mehmet   Yılmaz ")[0] is True
    assert db.get_client(P)["name"] == "Mehmet Yılmaz"
    for bad in ("", "abc", "123", "+1"):
        ok, message = adminlogic.add_client(bad, "x")
        assert ok is False and "country code" in message
    assert len(db.list_clients()) == 1


def test_adding_again_keeps_setup_progress():
    adminlogic.add_client(P, "A")
    db.set_client_fields(P, onboarding_done=1, onboarding_step=11, lang="en")
    adminlogic.add_client(P, "B")
    c = db.get_client(P)
    assert (c["name"], c["onboarding_done"], c["lang"]) == ("B", 1, "en")


def test_toggle_ping_and_clear_memory():
    adminlogic.add_client(P, "A")
    cid = db.get_client(P)["id"]
    assert adminlogic.ping(cid) is True and jobs.counts()["pending"] == 1
    assert adminlogic.toggle(cid) is True and db.get_client(P)["active"] == 0
    assert adminlogic.ping(cid) is False  # paused clients get no pings
    adminlogic.toggle(cid)
    memory.add_fact(P, "notes", "Something to remember")
    assert adminlogic.clear_memory(cid) is True and memory.count(P) == 0
    assert adminlogic.toggle(99999) is False and adminlogic.ping(99999) is False


def test_ping_is_delivered_as_the_hello_template():
    adminlogic.add_client(P, "A")
    adminlogic.ping(db.get_client(P)["id"])
    worker.process_one()
    assert whatsapp.OUTBOX == [(P, "[HELLO TEMPLATE]")]


def test_delete_client_needs_the_last_four_digits():
    adminlogic.add_client(P, "A")
    memory.add_fact(P, "notes", "Something to remember")
    cid = db.get_client(P)["id"]
    assert adminlogic.delete_client(cid, "") is False
    assert adminlogic.delete_client(cid, "0000") is False
    assert db.get_client(P) is not None
    assert adminlogic.delete_client(cid, "4567") is True
    assert db.get_client(P) is None and not memory.path_for(P).exists()


def test_retry_failed_job_from_the_dashboard():
    job_id = jobs.enqueue("send", P, {"text": "x"})
    jobs.claim()
    jobs.fail(job_id, "token expired", permanent=True)
    assert adminlogic.dashboard()["dead"][0]["last_error"] == "token expired"
    assert adminlogic.retry_job(job_id) is True and adminlogic.retry_job(job_id) is False


# ---------- pages ----------

def test_pages_render_and_escape_hostile_text():
    adminlogic.add_client(P, "<script>alert(1)</script>")
    memory.add_fact(P, "notes", "<img src=x onerror=alert(2)> note")
    db.add_watch(P, "THYAO.IS", 320.0, None, 3.0)
    db.add_reminder(P, "<b>bold</b> reminder", "2030-01-01T06:00:00")
    job_id = jobs.enqueue("send", P, {})
    jobs.claim()
    jobs.fail(job_id, "<i>boom</i>", permanent=True)
    cid = db.get_client(P)["id"]

    common = {"bot": "Conciergely", "csrf": security.csrf_token()}
    home = pages.render("dashboard", **common, **adminlogic.dashboard())
    detail = pages.render("detail", **common, **adminlogic.detail(cid))
    for html in (home, detail):
        assert "<script>alert" not in html and "<img src=x" not in html and "<b>bold" not in html
        assert "<i>boom" not in html
    assert "&lt;script&gt;" in home and "THYAO.IS" in detail
    assert "bold" not in detail and "2030-01-01" not in detail and "No pending reminders" not in detail  # never shown
    assert security.csrf_token() in home and "…4567" in home
    assert "Failed messages" in home


def test_empty_pages_render():
    common = {"bot": "Conciergely", "csrf": "t"}
    assert "No clients yet" in pages.render("dashboard", **common, **adminlogic.dashboard())
    assert adminlogic.detail(12345) is None


def test_settings_and_mail_sections_render_and_never_show_secrets(client, monkeypatch):
    PHONE = P
    from app import config, mail, vault
    monkeypatch.setattr(config, "MAIL_MASTER_KEY", vault.new_key())
    monkeypatch.setattr(mail, "imap_open", lambda acct: type("B", (), {"logout": lambda s: None})())
    monkeypatch.setattr(mail, "smtp_open", lambda acct: type("S", (), {"quit": lambda s: None})())
    cid = db.get_client(PHONE)["id"]
    ok, _ = adminlogic.connect_mail(cid, "ayse@gmail.com", "topsecretapppw", "INBOX", "bank.com")
    assert ok
    common = {"bot": "Conciergely", "csrf": "tok"}
    detail = pages.render("detail", **common, **adminlogic.detail(cid))
    assert "ay***@gmail.com" in detail and "topsecretapppw" not in detail and "bank.com" in detail
    settings_html = pages.render("settings", **common, **adminlogic.settings_page())
    assert "Calling add-on" in settings_html and "Back up now" in settings_html and "topsecretapppw" not in settings_html
    assert "checked" not in settings_html.split('id="calling"')[1].split("</form>")[0].split("<label")[1][:120]  # calling is off by default


def test_admin_design_has_no_gradients_and_no_scripts():
    src = open(pages.__file__, encoding="utf-8").read()
    assert "gradient" not in src.lower().replace("no gradients", "") and "<script" not in src


def test_mail_connect_errors_are_explained(client, monkeypatch):
    PHONE = P
    cid = db.get_client(PHONE)["id"]
    ok, message = adminlogic.connect_mail(cid, "a@gmail.com", "pw", "INBOX", "")
    assert not ok and "MAIL_MASTER_KEY" in message
