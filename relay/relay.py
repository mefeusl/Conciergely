"""Mail relay: a tiny sender that runs on a SECOND server whose host allows outgoing SMTP.

Why: the main server's host (DigitalOcean) blocks ports 465/587, so it cannot send mail through a client's own
Gmail/Outlook. The bot posts the finished message here over HTTPS; this program logs in to the client's mail
provider with the client's app password, sends that one message, and forgets everything.

Design rules (read before changing anything):
  * Standard library only, so the install is "copy one file".
  * Stores nothing: no passwords, no message text, no recipient addresses on disk or in logs.
  * Every request needs the shared key (Authorization: Bearer ...), compared in constant time.
  * It may only talk to mail ports (465/587) on public internet hosts (no localhost / private ranges).
  * 'from' must equal the login: the relay cannot be used to forge another sender.
  * Rate limited (per account and overall), and a repeated Message-ID is answered "sent" without sending twice.
  * It listens on 127.0.0.1 only. Caddy in front of it does HTTPS and accepts only the bot server's IP.

Endpoints:  GET /health   POST /verify   POST /send
Run:        RELAY_KEY=... python3 relay.py
"""
from __future__ import annotations

import base64
import binascii
import hmac
import ipaddress
import json
import logging
import os
import re
import smtplib
import socket
import ssl
import threading
import time
from collections import deque
from email import message_from_bytes
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

log = logging.getLogger("relay")

ALLOWED_PORTS = (465, 587)
MAX_BODY_BYTES = 400_000          # the whole request
MAX_RAW_BYTES = 250_000           # the decoded email
MAX_RECIPIENTS = 10
SMTP_TIMEOUT = 25
DEDUP_SECONDS = 2 * 3600
PER_ACCOUNT_PER_HOUR = int(os.environ.get("RELAY_PER_ACCOUNT_HOUR", "30"))
PER_HOUR_OVERALL = int(os.environ.get("RELAY_PER_HOUR", "150"))

HOST_RE = re.compile(r"^(?=.{1,253}$)([A-Za-z0-9]([A-Za-z0-9\-]{0,61}[A-Za-z0-9])?\.)+[A-Za-z]{2,}$")
ADDR_RE = re.compile(r"^[A-Za-z0-9._%+'\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")


class Relay:
    """All the logic, with the network parts injectable so it can be tested without a network."""

    def __init__(self, key: str, smtp_factory=None, resolver=None, clock=time.time):
        if len(key or "") < 24:
            raise ValueError("RELAY_KEY must be at least 24 characters")
        self.key = key.encode()
        self.smtp_factory = smtp_factory or self._real_smtp
        self.resolver = resolver or self._real_resolve
        self.clock = clock
        self._lock = threading.Lock()
        self._hits: deque[float] = deque()
        self._acct_hits: dict[str, deque[float]] = {}
        self._done: dict[str, float] = {}      # Message-ID -> when it was sent
        self._inflight: set[str] = set()

    # ---------- entry point ----------
    def handle(self, method: str, path: str, auth_header: str, body: bytes) -> tuple[int, dict]:
        if method == "GET" and path == "/health":
            return 200, {"ok": True}
        if method != "POST" or path not in ("/send", "/verify"):
            return 404, {"ok": False, "error": "not_found"}
        if not self._authorised(auth_header):
            return 401, {"ok": False, "error": "unauthorised"}
        if len(body) > MAX_BODY_BYTES:
            return 413, {"ok": False, "error": "too_large"}
        try:
            data = json.loads(body.decode("utf-8"))
            if not isinstance(data, dict):
                raise ValueError
        except (ValueError, UnicodeDecodeError):
            return 400, {"ok": False, "error": "bad_json"}
        problem = self._validate_login(data)
        if problem:
            return 400, {"ok": False, "error": problem}
        if not self._within_rate(data["user"].lower(), count=(path == "/send")):
            return 429, {"ok": False, "error": "rate_limited"}
        if path == "/verify":
            return self._verify(data)
        return self._send(data)

    # ---------- checks ----------
    def _authorised(self, header: str) -> bool:
        prefix = "Bearer "
        if not header or not header.startswith(prefix):
            return False
        return hmac.compare_digest(header[len(prefix):].encode(), self.key)

    def _validate_login(self, d: dict) -> str:
        host, port, user, password = d.get("host"), d.get("port"), d.get("user"), d.get("password")
        if not isinstance(host, str) or not HOST_RE.match(host):
            return "bad_host"
        if port not in ALLOWED_PORTS or isinstance(port, bool):
            return "bad_port"
        if not isinstance(user, str) or not ADDR_RE.match(user) or len(user) > 254:
            return "bad_user"
        if not isinstance(password, str) or not password or len(password) > 200:
            return "bad_password"
        if not self._public_host(host):
            return "bad_host"
        return ""

    def _public_host(self, host: str) -> bool:
        try:
            addresses = self.resolver(host)
        except OSError:
            return False
        if not addresses:
            return False
        for a in addresses:
            ip = ipaddress.ip_address(a)
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved or ip.is_unspecified:
                return False
        return True

    def _within_rate(self, account: str, count: bool) -> bool:
        if not count:
            return True
        now = self.clock()
        with self._lock:
            for q in [self._hits, *self._acct_hits.values()]:
                while q and q[0] < now - 3600:
                    q.popleft()
            mine = self._acct_hits.setdefault(account, deque())
            if len(self._hits) >= PER_HOUR_OVERALL or len(mine) >= PER_ACCOUNT_PER_HOUR:
                return False
            self._hits.append(now)
            mine.append(now)
            return True

    # ---------- the two jobs ----------
    def _open(self, d: dict):
        server = self.smtp_factory(d["host"], d["port"])
        server.login(d["user"], d["password"])
        return server

    def _verify(self, d: dict) -> tuple[int, dict]:
        try:
            server = self._open(d)
        except Exception as exc:  # noqa: BLE001 - mapped below, never echoed
            return self._failure("verify", d, exc)
        self._quit(server)
        self._log("verify", d, "ok")
        return 200, {"ok": True}

    def _send(self, d: dict) -> tuple[int, dict]:
        sender, to, raw_b64 = d.get("from"), d.get("to"), d.get("raw")
        if not isinstance(sender, str) or sender.lower() != d["user"].lower():
            return 400, {"ok": False, "error": "bad_from"}
        if (not isinstance(to, list) or not to or len(to) > MAX_RECIPIENTS
                or not all(isinstance(x, str) and ADDR_RE.match(x) for x in to)):
            return 400, {"ok": False, "error": "bad_to"}
        try:
            raw = base64.b64decode(raw_b64 or "", validate=True)
        except (binascii.Error, ValueError, TypeError):
            return 400, {"ok": False, "error": "bad_raw"}
        if not raw or len(raw) > MAX_RAW_BYTES:
            return 400, {"ok": False, "error": "bad_raw"}
        message_id = (message_from_bytes(raw).get("Message-ID") or "").strip()

        if message_id:
            with self._lock:
                self._forget_old()
                if message_id in self._done:
                    return 200, {"ok": True, "duplicate": True}
                if message_id in self._inflight:
                    return 409, {"ok": False, "error": "busy"}
                self._inflight.add(message_id)
        try:
            server = self._open(d)
            try:
                server.sendmail(sender, to, raw)
            finally:
                self._quit(server)
        except Exception as exc:  # noqa: BLE001
            return self._failure("send", d, exc)
        finally:
            if message_id:
                with self._lock:
                    self._inflight.discard(message_id)
        if message_id:
            with self._lock:
                self._done[message_id] = self.clock()
        self._log("send", d, "ok", recipients=len(to))
        return 200, {"ok": True}

    def _forget_old(self) -> None:
        cutoff = self.clock() - DEDUP_SECONDS
        for k in [k for k, t in self._done.items() if t < cutoff]:
            del self._done[k]

    @staticmethod
    def _quit(server) -> None:
        try:
            server.quit()
        except Exception:  # noqa: BLE001
            pass

    # ---------- errors and logging (never the password, text or addresses) ----------
    def _failure(self, what: str, d: dict, exc: Exception) -> tuple[int, dict]:
        if isinstance(exc, smtplib.SMTPAuthenticationError):
            code = "auth"
        elif isinstance(exc, (smtplib.SMTPRecipientsRefused, smtplib.SMTPSenderRefused, smtplib.SMTPDataError)):
            code = "refused"
        elif isinstance(exc, (OSError, smtplib.SMTPConnectError, smtplib.SMTPServerDisconnected)):
            code = "unreachable"
        else:
            code = "failed"
        self._log(what, d, code, error=type(exc).__name__)
        return 502, {"ok": False, "error": code}

    @staticmethod
    def _log(what: str, d: dict, result: str, **extra) -> None:
        domain = str(d.get("user", "")).rpartition("@")[2]
        bits = " ".join(f"{k}={v}" for k, v in extra.items())
        log.info("%s %s account_domain=%s %s", what, result, domain, bits)

    # ---------- the real network ----------
    @staticmethod
    def _real_resolve(host: str) -> list[str]:
        return sorted({info[4][0] for info in socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)})

    @staticmethod
    def _real_smtp(host: str, port: int):
        context = ssl.create_default_context()
        if port == 465:
            return smtplib.SMTP_SSL(host, port, timeout=SMTP_TIMEOUT, context=context)
        server = smtplib.SMTP(host, port, timeout=SMTP_TIMEOUT)
        server.starttls(context=context)
        return server


def make_handler(relay: Relay):
    class Handler(BaseHTTPRequestHandler):
        server_version = "relay"
        sys_version = ""

        def _run(self, method: str) -> None:
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                length = 0
            body = self.rfile.read(min(length, MAX_BODY_BYTES + 1)) if length > 0 else b""
            status, payload = relay.handle(method, self.path.split("?")[0], self.headers.get("Authorization", ""), body)
            out = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(out)))
            self.end_headers()
            self.wfile.write(out)

        def do_GET(self):  # noqa: N802
            self._run("GET")

        def do_POST(self):  # noqa: N802
            self._run("POST")

        def log_message(self, *args):  # the default access log would print paths; we log our own safe lines
            pass

    return Handler


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    key = os.environ.get("RELAY_KEY", "")
    try:
        relay = Relay(key)
    except ValueError as exc:
        raise SystemExit(str(exc))
    host, port = os.environ.get("RELAY_BIND", "127.0.0.1"), int(os.environ.get("RELAY_PORT", "8081"))
    log.info("mail relay listening on %s:%s", host, port)
    ThreadingHTTPServer((host, port), make_handler(relay)).serve_forever()


if __name__ == "__main__":
    main()
