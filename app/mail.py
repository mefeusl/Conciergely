"""Email on demand: read, search and draft; send only after the client says so.

  * login = the client's address + an app password (IMAP to read, SMTP to send). No OAuth, no Google review.
  * the login is sealed with vault.py (envelope encryption) and opened only for the length of one request;
  * mail is fetched only when the client asks; the chat gets a short answer, no mail is stored here;
  * every access is written to mail_audit (what, when; never the content), shown with 'mail geçmişi';
  * email text is untrusted data. There is no delete / move tool. Sending needs a draft and then the
    client's own 'gönder' / 'send' message, checked in code before the AI is involved.
"""
from __future__ import annotations

import base64
import email
import hashlib
import imaplib
import json
import logging
import re
import smtplib
from email import policy
from email.header import decode_header, make_header
from email.message import EmailMessage
from email.utils import parseaddr
from html import unescape

from . import config, db, vault

log = logging.getLogger(__name__)

ADDRESS_RE = re.compile(r"^[A-Za-z0-9._%+'\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")
BODY_LIMIT = 4000
SERVERS = {  # domain -> (imap host, smtp host, smtp port, ssl)
    "gmail.com": ("imap.gmail.com", "smtp.gmail.com", 465, True),
    "googlemail.com": ("imap.gmail.com", "smtp.gmail.com", 465, True),
    "outlook.com": ("outlook.office365.com", "smtp.office365.com", 587, False),
    "hotmail.com": ("outlook.office365.com", "smtp.office365.com", 587, False),
    "live.com": ("outlook.office365.com", "smtp.office365.com", 587, False),
    "msn.com": ("outlook.office365.com", "smtp.office365.com", 587, False),
    "yahoo.com": ("imap.mail.yahoo.com", "smtp.mail.yahoo.com", 465, True),
    "icloud.com": ("imap.mail.me.com", "smtp.mail.me.com", 587, False),
    "me.com": ("imap.mail.me.com", "smtp.mail.me.com", 587, False),
    "yandex.com": ("imap.yandex.com", "smtp.yandex.com", 465, True),
    "yandex.com.tr": ("imap.yandex.com", "smtp.yandex.com", 465, True),
}


class MailError(Exception):
    """Something the client can be told about in a sentence."""


# ---------------- servers and connections (replaced in tests) ----------------

def servers_for(address: str) -> tuple[str, str, int, bool]:
    domain = address.rsplit("@", 1)[-1].lower()
    return SERVERS.get(domain) or (f"imap.{domain}", f"smtp.{domain}", 465, True)


def imap_open(acct: dict) -> imaplib.IMAP4:
    box = imaplib.IMAP4_SSL(acct["imap"], timeout=config.MAIL_TIMEOUT)
    box.login(acct["address"], acct["password"])
    return box


def smtp_open(acct: dict) -> smtplib.SMTP:
    if acct["ssl"]:
        s = smtplib.SMTP_SSL(acct["smtp"], acct["port"], timeout=config.MAIL_TIMEOUT)
    else:
        s = smtplib.SMTP(acct["smtp"], acct["port"], timeout=config.MAIL_TIMEOUT)
        s.starttls()
    s.login(acct["address"], acct["password"])
    return s


# ---------------- sending through the relay ----------------
# The main server's host blocks outgoing SMTP, so when MAIL_RELAY_URL is set the finished message is handed to
# relay/relay.py on a second server over HTTPS. Without it, the direct smtp_open path is used (unchanged).

class RelayError(OSError):
    """The relay refused or could not be reached. .code: auth, unreachable, refused, failed, rate_limited."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def relay_url() -> str:
    """The configured relay address, or '' when unset or unsafe (plain http is only allowed for localhost)."""
    from urllib.parse import urlparse
    url = (config.MAIL_RELAY_URL or "").strip().rstrip("/")
    if not url or not config.MAIL_RELAY_KEY:
        return ""
    p = urlparse(url)
    if p.scheme == "https" and p.hostname:
        return url
    if p.scheme == "http" and p.hostname in ("localhost", "127.0.0.1", "::1"):
        return url
    log.error("MAIL_RELAY_URL ignored: it must start with https://")
    return ""


def _relay_call(path: str, payload: dict) -> None:
    import urllib.error
    import urllib.request
    req = urllib.request.Request(
        relay_url() + path, data=json.dumps(payload).encode(), method="POST",
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + config.MAIL_RELAY_KEY})
    try:
        with urllib.request.urlopen(req, timeout=config.MAIL_TIMEOUT + 20) as resp:
            resp.read()
        return
    except urllib.error.HTTPError as exc:
        try:
            code = str(json.loads(exc.read().decode()).get("error") or "failed")
        except Exception:
            code = "failed"
        if exc.code in (401, 403):
            code = "relay_key"
        log.warning("mail relay %s answered %s (%s)", path, exc.code, code)
        raise RelayError(code) from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        log.warning("mail relay unreachable: %s", type(exc).__name__)
        raise RelayError("unreachable") from exc


def _login_payload(acct: dict) -> dict:
    return {"host": acct["smtp"], "port": acct["port"], "ssl": bool(acct["ssl"]),
            "user": acct["address"], "password": acct["password"]}


def smtp_verify(acct: dict) -> None:
    """Prove the sending login works (used when connecting)."""
    if relay_url():
        _relay_call("/verify", _login_payload(acct))
        return
    server = smtp_open(acct)
    try:
        server.quit()
    except Exception:
        pass


def smtp_send(acct: dict, msg: EmailMessage) -> None:
    if relay_url():
        from email.utils import getaddresses
        to = [a for _, a in getaddresses([str(msg["To"])]) if a]
        _relay_call("/send", {**_login_payload(acct), "from": acct["address"], "to": to,
                              "raw": base64.b64encode(msg.as_bytes()).decode()})
        return
    server = smtp_open(acct)
    try:
        server.send_message(msg)
    finally:
        try:
            server.quit()
        except Exception:
            pass


# ---------------- accounts ----------------

def audit(phone: str, action: str, detail: str = "") -> None:
    with db.conn() as c:
        c.execute("INSERT INTO mail_audit(phone, at, action, detail) VALUES(?,?,?,?)",
                  (phone, db.now_utc_iso(), action, detail[:200]))


def history(phone: str, limit: int = 15) -> list[dict]:
    with db.conn() as c:
        rows = c.execute("SELECT at, action, detail FROM mail_audit WHERE phone=? ORDER BY id DESC LIMIT ?",
                         (phone, limit)).fetchall()
    return [dict(r) for r in rows]


def is_connected(phone: str) -> bool:
    with db.conn() as c:
        return c.execute("SELECT 1 FROM mail_accounts WHERE phone=?", (phone,)).fetchone() is not None


def _row(phone: str) -> dict | None:
    with db.conn() as c:
        row = c.execute("SELECT * FROM mail_accounts WHERE phone=?", (phone,)).fetchone()
    return dict(row) if row else None


def account(phone: str) -> dict:
    """The decrypted login. Only call this right before talking to the mail server."""
    row = _row(phone)
    if not row:
        raise MailError("not_connected")
    try:
        acct = json.loads(vault.open_(phone, row["secret"]))
    except vault.VaultError as exc:
        log.error("mail secret for %s could not be opened: %s", db.last4(phone), exc)
        raise MailError("vault") from exc
    acct["folder"], acct["senders"] = row["folder"], row["senders"]
    return acct


def public_info(phone: str) -> dict | None:
    """What /admin may show: never the password."""
    row = _row(phone)
    if not row:
        return None
    return {"address": _masked(json.loads(vault.open_(phone, row["secret"]))["address"]) if vault.available() else "(sealed)",
            "folder": row["folder"], "senders": row["senders"], "since": row["created_at"],
            "key_ok": vault.key_id(row["secret"]) in _known_ids()}


def _masked(address: str) -> str:
    local, _, domain = address.partition("@")
    return (local[:2] + "***@" + domain) if local else address


def _known_ids() -> set[str]:
    try:
        return set(vault._keys())
    except vault.VaultError:
        return set()


def connect(phone: str, address: str, password: str, folder: str = "INBOX", senders: str = "") -> str:
    """Test the login, then store it sealed. Returns the address. Raises MailError with a reason code."""
    if not vault.available():
        raise MailError("no_key")
    address = (address or "").strip().lower()
    password = "".join((password or "").split())  # app passwords are shown with spaces; they are not part of it
    if not ADDRESS_RE.match(address) or not password or len(password) > 200:
        raise MailError("bad_input")
    imap_host, smtp_host, port, ssl = servers_for(address)
    acct = {"address": address, "password": password, "imap": imap_host, "smtp": smtp_host, "port": port, "ssl": ssl}
    try:
        box = imap_open(acct)
        try:
            box.logout()
        except Exception:
            pass
    except imaplib.IMAP4.error as exc:
        raise MailError("login_failed") from exc
    except (OSError, imaplib.IMAP4.abort) as exc:
        raise MailError("unreachable") from exc
    try:
        smtp_verify(acct)
    except smtplib.SMTPAuthenticationError as exc:
        raise MailError("smtp_failed") from exc
    except RelayError as exc:
        # a wrong password is the client's to fix; anything else is our relay or the network
        raise MailError("smtp_failed" if exc.code == "auth" else "unreachable") from exc
    except (OSError, smtplib.SMTPException) as exc:
        raise MailError("smtp_failed") from exc
    with db.conn() as c:
        c.execute("INSERT INTO mail_accounts(phone, secret, folder, senders, created_at) VALUES(?,?,?,?,?) "
                  "ON CONFLICT(phone) DO UPDATE SET secret=excluded.secret, folder=excluded.folder, "
                  "senders=excluded.senders, created_at=excluded.created_at",
                  (phone, vault.seal(phone, json.dumps(acct)), (folder or "INBOX")[:80], senders[:300], db.now_utc_iso()))
    audit(phone, "connected", _masked(address))
    return address


def set_scope(phone: str, folder: str, senders: str) -> bool:
    with db.conn() as c:
        n = c.execute("UPDATE mail_accounts SET folder=?, senders=? WHERE phone=?",
                      ((folder or "INBOX")[:80], senders[:300], phone)).rowcount
    if n:
        audit(phone, "scope", f"folder={folder or 'INBOX'}")
    return bool(n)


def disconnect(phone: str) -> bool:
    with db.conn() as c:
        n = c.execute("DELETE FROM mail_accounts WHERE phone=?", (phone,)).rowcount
        c.execute("UPDATE mail_drafts SET status='cancelled' WHERE phone=? AND status='pending'", (phone,))
    if n:
        audit(phone, "disconnected")
    return bool(n)


def rewrap_all() -> int:
    """After a key change: seal every login with the current key. Returns how many were rewritten."""
    n = 0
    with db.conn() as c:
        rows = c.execute("SELECT phone, secret FROM mail_accounts").fetchall()
    for row in rows:
        if vault.key_id(row["secret"]) != vault.current_key_id():
            fresh = vault.seal(row["phone"], vault.open_(row["phone"], row["secret"]))
            with db.conn() as c:
                c.execute("UPDATE mail_accounts SET secret=? WHERE phone=?", (fresh, row["phone"]))
            n += 1
    return n


# ---------------- reading ----------------

def _text(value) -> str:
    try:
        return str(make_header(decode_header(value or "")))
    except Exception:
        return str(value or "")


def _html_to_text(html: str) -> str:
    html = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", html)
    html = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</tr>", "\n", html)
    return re.sub(r"[ \t]+", " ", unescape(re.sub(r"<[^>]+>", " ", html)))


def _body(msg) -> tuple[str, list[str]]:
    plain, html, names = "", "", []
    for part in msg.walk():
        if part.is_multipart():
            continue
        name = part.get_filename()
        if name:
            names.append(_text(name)[:80])
            continue
        kind = part.get_content_type()
        try:
            content = part.get_content()
        except Exception:
            continue
        if kind == "text/plain" and not plain:
            plain = content
        elif kind == "text/html" and not html:
            html = content
    text = plain or _html_to_text(html)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text[:BODY_LIMIT], names


def _allowed(acct: dict, sender: str) -> bool:
    rules = [r.strip().lower() for r in re.split(r"[,\s;]+", acct.get("senders") or "") if r.strip()]
    if not rules:
        return True
    addr = parseaddr(sender)[1].lower()
    return any(r == addr or addr.endswith("@" + r.lstrip("@")) or (r.startswith("@") and addr.endswith(r)) for r in rules)


def _quote(text: str) -> str:
    return '"' + text.replace("\\", " ").replace('"', " ") + '"'


def _session(phone: str):
    acct = account(phone)
    try:
        box = imap_open(acct)
        typ, _ = box.select(f'"{acct["folder"]}"', readonly=True)
        if typ != "OK":
            raise MailError("folder")
        return acct, box
    except imaplib.IMAP4.error as exc:
        raise MailError("login_failed") from exc
    except (OSError, imaplib.IMAP4.abort) as exc:
        raise MailError("unreachable") from exc


def _close(box) -> None:
    try:
        box.logout()
    except Exception:
        pass


def list_messages(phone: str, query: str = "", unread: bool = False, limit: int = 5) -> list[dict]:
    limit = max(1, min(int(limit or 5), 10))
    acct, box = _session(phone)
    try:
        if query.strip():
            q = _quote(query.strip()[:80]).encode("utf-8")
            typ, data = box.uid("SEARCH", b"CHARSET", b"UTF-8", b"OR", b"FROM", q, b"OR", b"SUBJECT", q, b"TEXT", q)
        else:
            typ, data = box.uid("SEARCH", None, "UNSEEN" if unread else "ALL")
        if typ != "OK":
            raise MailError("search")
        uids = (data[0] or b"").split()[::-1][: limit * 3]  # newest first; a few extra in case some are filtered out
        out = []
        for uid in uids:
            typ, parts = box.uid("FETCH", uid, "(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE)])")
            if typ != "OK" or not parts or not isinstance(parts[0], tuple):
                continue
            head = email.message_from_bytes(parts[0][1], policy=policy.default)
            sender = _text(head.get("From"))
            if not _allowed(acct, sender):
                continue
            out.append({"id": uid.decode(), "from": sender[:120], "subject": _text(head.get("Subject"))[:150],
                        "date": _text(head.get("Date"))[:40]})
            if len(out) >= limit:
                break
        audit(phone, "search" if query.strip() else ("unread" if unread else "list"), f"{len(out)} found")
        return out
    finally:
        _close(box)


def read_message(phone: str, uid: str) -> dict:
    if not re.fullmatch(r"\d{1,12}", str(uid or "")):
        raise MailError("bad_input")
    acct, box = _session(phone)
    try:
        typ, parts = box.uid("FETCH", str(uid), "(BODY.PEEK[])")
        if typ != "OK" or not parts or not isinstance(parts[0], tuple):
            raise MailError("not_found")
        msg = email.message_from_bytes(parts[0][1], policy=policy.default)
        sender = _text(msg.get("From"))
        if not _allowed(acct, sender):
            raise MailError("not_allowed")
        body, names = _body(msg)
        audit(phone, "read", f"id {uid}")
        return {"id": str(uid), "from": sender[:120], "to": _text(msg.get("To"))[:150], "subject": _text(msg.get("Subject"))[:150],
                "date": _text(msg.get("Date"))[:40], "message_id": str(msg.get("Message-ID") or "")[:200],
                "attachments": names[:10], "body": body}
    finally:
        _close(box)


# ---------------- writing: draft now, send only on the client's word ----------------

def make_draft(phone: str, to: str, subject: str, body: str, in_reply_to: str = "") -> dict:
    to = (to or "").strip()
    if not ADDRESS_RE.match(to) or "\n" in to or "\r" in to:
        raise MailError("bad_recipient")
    subject = " ".join((subject or "").split())[:200]
    body = (body or "").strip()[:8000]
    if not body:
        raise MailError("bad_input")
    with db.conn() as c:
        c.execute("UPDATE mail_drafts SET status='replaced' WHERE phone=? AND status='pending'", (phone,))
        cur = c.execute("INSERT INTO mail_drafts(phone, to_addr, subject, body, in_reply_to, created_at) VALUES(?,?,?,?,?,?)",
                        (phone, to, subject, body, (in_reply_to or "")[:200].replace("\n", " "), db.now_utc_iso()))
    audit(phone, "draft", f"to {to.partition('@')[2]}")
    return {"id": cur.lastrowid, "to": to, "subject": subject, "body": body}


def pending_draft(phone: str) -> dict | None:
    from datetime import timedelta
    cutoff = db.to_iso(db.now_utc() - timedelta(minutes=config.MAIL_DRAFT_MINUTES))
    with db.conn() as c:
        row = c.execute("SELECT * FROM mail_drafts WHERE phone=? AND status='pending' AND created_at>=? ORDER BY id DESC LIMIT 1",
                        (phone, cutoff)).fetchone()
    return dict(row) if row else None


def discard_draft(phone: str) -> bool:
    with db.conn() as c:
        n = c.execute("UPDATE mail_drafts SET status='cancelled' WHERE phone=? AND status='pending'", (phone,)).rowcount
    return bool(n)


def send_pending(phone: str) -> dict:
    draft = pending_draft(phone)
    if not draft:
        raise MailError("no_draft")
    acct = account(phone)
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = acct["address"], draft["to_addr"], draft["subject"] or "(no subject)"
    if draft["in_reply_to"]:
        msg["In-Reply-To"] = draft["in_reply_to"]
        msg["References"] = draft["in_reply_to"]
    msg.set_content(draft["body"])
    # one fixed Message-ID per draft: if a retry happens after a half-finished send, the relay and the
    # recipient's server both see the same message and it cannot arrive twice
    token = hashlib.sha256(f"{phone}:{draft['id']}:{draft['created_at']}".encode()).hexdigest()[:20]
    msg["Message-ID"] = f"<asistan.{draft['id']}.{token}@{acct['address'].rpartition('@')[2]}>"
    with db.conn() as c:  # claim it first, so a retried job can never send it twice
        n = c.execute("UPDATE mail_drafts SET status='sending' WHERE id=? AND status='pending'", (draft["id"],)).rowcount
    if not n:
        raise MailError("no_draft")
    try:
        smtp_send(acct, msg)
    except (OSError, smtplib.SMTPException) as exc:
        with db.conn() as c:
            c.execute("UPDATE mail_drafts SET status='pending' WHERE id=?", (draft["id"],))
        audit(phone, "send_failed", type(exc).__name__)
        raise MailError("send_failed") from exc
    with db.conn() as c:
        c.execute("UPDATE mail_drafts SET status='sent' WHERE id=?", (draft["id"],))
    audit(phone, "sent", f"to {draft['to_addr'].partition('@')[2]}")
    return draft
