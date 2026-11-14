"""The relay program (relay/relay.py) and the bot's use of it."""
import base64
import json
import smtplib
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "relay"))
import relay as R  # noqa: E402

KEY = "k" * 32
PUBLIC = lambda host: ["142.250.1.1"]  # noqa: E731


class FakeSMTP:
    log: list = []
    fail_login: Exception | None = None
    fail_send: Exception | None = None

    def __init__(self, host, port):
        self.host, self.port = host, port

    def login(self, user, pw):
        if FakeSMTP.fail_login:
            raise FakeSMTP.fail_login

    def sendmail(self, frm, to, raw):
        if FakeSMTP.fail_send:
            raise FakeSMTP.fail_send
        FakeSMTP.log.append((self.host, self.port, frm, list(to), raw))

    def quit(self):
        pass


@pytest.fixture(autouse=True)
def _reset():
    FakeSMTP.log, FakeSMTP.fail_login, FakeSMTP.fail_send = [], None, None


def make(**kw):
    return R.Relay(KEY, smtp_factory=FakeSMTP, resolver=kw.pop("resolver", PUBLIC), **kw)


def raw(mid="<a@x.com>"):
    return base64.b64encode(f"From: me@gmail.com\r\nTo: you@x.com\r\nMessage-ID: {mid}\r\nSubject: hi\r\n\r\nbody".encode()).decode()


def body(**over):
    d = {"host": "smtp.gmail.com", "port": 465, "ssl": True, "user": "me@gmail.com", "password": "secretpw",
         "from": "me@gmail.com", "to": ["you@x.com"], "raw": raw()}
    d.update(over)
    return json.dumps(d).encode()


AUTH = "Bearer " + KEY


def test_short_key_refused():
    with pytest.raises(ValueError):
        R.Relay("short")


def test_health_open_but_nothing_else_without_key():
    r = make()
    assert r.handle("GET", "/health", "", b"")[0] == 200
    assert r.handle("POST", "/send", "", body())[0] == 401
    assert r.handle("POST", "/send", "Bearer wrong", body())[0] == 401
    assert FakeSMTP.log == []


def test_send_ok():
    r = make()
    assert r.handle("POST", "/send", AUTH, body()) == (200, {"ok": True})
    host, port, frm, to, _ = FakeSMTP.log[0]
    assert (host, port, frm, to) == ("smtp.gmail.com", 465, "me@gmail.com", ["you@x.com"])


def test_from_must_equal_login():
    assert make().handle("POST", "/send", AUTH, body(**{"from": "boss@gmail.com"}))[1]["error"] == "bad_from"
    assert FakeSMTP.log == []


@pytest.mark.parametrize("over,err", [
    ({"port": 25}, "bad_port"), ({"port": True}, "bad_port"), ({"host": "localhost"}, "bad_host"),
    ({"host": "a b.com"}, "bad_host"), ({"user": "nope"}, "bad_user"), ({"password": ""}, "bad_password"),
    ({"to": []}, "bad_to"), ({"to": ["x"]}, "bad_to"), ({"to": ["a@b.co"] * 11}, "bad_to"),
    ({"raw": "!!!"}, "bad_raw"), ({"raw": ""}, "bad_raw")])
def test_validation(over, err):
    assert make().handle("POST", "/send", AUTH, body(**over))[1]["error"] == err
    assert FakeSMTP.log == []


@pytest.mark.parametrize("ip", ["127.0.0.1", "10.0.0.5", "192.168.1.1", "169.254.169.254", "::1"])
def test_private_hosts_refused(ip):
    r = make(resolver=lambda h: [ip])
    assert r.handle("POST", "/send", AUTH, body())[1]["error"] == "bad_host"


def test_one_private_answer_among_public_refused():
    r = make(resolver=lambda h: ["142.250.1.1", "10.0.0.1"])
    assert r.handle("POST", "/verify", AUTH, body())[0] == 400


def test_bad_json_and_too_large():
    r = make()
    assert r.handle("POST", "/send", AUTH, b"{")[1]["error"] == "bad_json"
    assert r.handle("POST", "/send", AUTH, b"[]")[1]["error"] == "bad_json"
    assert r.handle("POST", "/send", AUTH, b"x" * (R.MAX_BODY_BYTES + 1))[0] == 413


def test_error_mapping():
    r = make()
    FakeSMTP.fail_login = smtplib.SMTPAuthenticationError(535, b"no")
    assert r.handle("POST", "/verify", AUTH, body())[1]["error"] == "auth"
    FakeSMTP.fail_login = ConnectionRefusedError()
    assert r.handle("POST", "/verify", AUTH, body())[1]["error"] == "unreachable"
    FakeSMTP.fail_login = None
    FakeSMTP.fail_send = smtplib.SMTPRecipientsRefused({})
    assert r.handle("POST", "/send", AUTH, body())[1]["error"] == "refused"
    FakeSMTP.fail_send = RuntimeError("x")
    assert r.handle("POST", "/send", AUTH, body())[1]["error"] == "failed"


def test_verify_ok_sends_nothing():
    assert make().handle("POST", "/verify", AUTH, body()) == (200, {"ok": True})
    assert FakeSMTP.log == []


def test_same_message_id_sent_once():
    r = make()
    assert r.handle("POST", "/send", AUTH, body())[0] == 200
    s, out = r.handle("POST", "/send", AUTH, body())
    assert (s, out["duplicate"]) == (200, True)
    assert len(FakeSMTP.log) == 1


def test_failed_send_can_be_retried():
    r = make()
    FakeSMTP.fail_send = ConnectionResetError()
    assert r.handle("POST", "/send", AUTH, body())[0] == 502
    FakeSMTP.fail_send = None
    assert r.handle("POST", "/send", AUTH, body())[0] == 200
    assert len(FakeSMTP.log) == 1


def test_dedup_expires():
    t = [1000.0]
    r = make(clock=lambda: t[0])
    r.handle("POST", "/send", AUTH, body())
    t[0] += R.DEDUP_SECONDS + 1
    assert "duplicate" not in r.handle("POST", "/send", AUTH, body())[1]
    assert len(FakeSMTP.log) == 2


def test_rate_limit_per_account(monkeypatch):
    monkeypatch.setattr(R, "PER_ACCOUNT_PER_HOUR", 2)
    r = make()
    codes = [r.handle("POST", "/send", AUTH, body(raw=raw(f"<{i}@x>")))[0] for i in range(3)]
    assert codes == [200, 200, 429]
    other = body(user="b@gmail.com", **{"from": "b@gmail.com"})
    assert r.handle("POST", "/send", AUTH, other)[0] == 200


def test_rate_limit_window_moves(monkeypatch):
    monkeypatch.setattr(R, "PER_ACCOUNT_PER_HOUR", 1)
    t = [0.0]
    r = make(clock=lambda: t[0])
    assert r.handle("POST", "/send", AUTH, body(raw=raw("<1@x>")))[0] == 200
    assert r.handle("POST", "/send", AUTH, body(raw=raw("<2@x>")))[0] == 429
    t[0] += 3601
    assert r.handle("POST", "/send", AUTH, body(raw=raw("<3@x>")))[0] == 200


def test_overall_limit(monkeypatch):
    monkeypatch.setattr(R, "PER_HOUR_OVERALL", 1)
    r = make()
    assert r.handle("POST", "/send", AUTH, body(raw=raw("<1@x>")))[0] == 200
    other = body(user="b@gmail.com", raw=raw("<2@x>"), **{"from": "b@gmail.com"})
    assert r.handle("POST", "/send", AUTH, other)[0] == 429


def test_logs_hold_no_secrets(caplog):
    caplog.set_level("INFO", logger="relay")
    r = make()
    FakeSMTP.fail_login = smtplib.SMTPAuthenticationError(535, b"bad secretpw")
    r.handle("POST", "/send", AUTH, body())
    FakeSMTP.fail_login = None
    r.handle("POST", "/send", AUTH, body())
    text = caplog.text
    assert "secretpw" not in text and "you@x.com" not in text and "me@gmail.com" not in text and "body" not in text
    assert "gmail.com" in text


def test_unknown_route_and_method():
    r = make()
    assert r.handle("GET", "/send", AUTH, b"")[0] == 404
    assert r.handle("POST", "/nope", AUTH, b"{}")[0] == 404
