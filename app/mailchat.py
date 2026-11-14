"""The chat side of email: connecting, disconnecting, the audit history, and the send / cancel words.

These are recognised in code before the AI sees the message. That matters twice:
  * a password typed into the chat is never given to the AI and never saved in the chat history;
  * an email is sent only by the client's own 'gönder' / 'send', never because a tool or an email asked for it.
"""
from __future__ import annotations

from . import db, mail, plans, vault

CONNECT = {"mail bagla", "e-posta bagla", "eposta bagla", "mail baglama", "connect mail", "mail connect", "connect email"}
DISCONNECT = {"mail baglantisini kes", "mail baglantisini sil", "mail baglantisi kes", "disconnect mail", "mail disconnect",
              "disconnect email"}
HISTORY = {"mail gecmisi", "mail gecmisim", "mail log", "mail history", "email history"}
SEND = {"gonder", "send", "evet gonder", "send it", "gonder lutfen", "yolla"}
DISCARD = {"vazgec", "iptal", "cancel", "discard", "gonderme", "dont send"}

T = {
    "tr": {
        "plus_only": "E-posta asistanı Plus paketine dahildir. 7 günlük ücretsiz denemede de kullanabilirsiniz.",
        "no_key": "E-posta bağlantısı henüz bu sistemde açılmadı. Kurulumu yapan kişiyle görüşün.",
        "ask_address": ("E-posta adresinizi yazın (örnek: ad@gmail.com). Vazgeçmek için “iptal” yazın.\n\n"
                        "Not: Şifrenizi değil, size özel oluşturacağınız bir uygulama şifresini isteyeceğim. "
                        "Daha güvenlisi, bağlantıyı kurulumu yapan kişinin sizinle birlikte yönetim ekranından yapmasıdır."),
        "ask_password": ("Şimdi uygulama şifrenizi gönderin (Google: myaccount.google.com/apppasswords, 16 haneli kod). "
                         "Sohbetteki mesajı sonradan siz de silin. Bu şifre yalnızca e-posta için çalışır, "
                         "istediğiniz an oradan iptal edebilirsiniz."),
        "bad_address": "Bu bir e-posta adresine benzemiyor. Tekrar yazın ya da “iptal” deyin.",
        "connected": ("E-posta bağlandı: {address}. Okuma isteklerinizi karşılarım; e-posta göndermeden önce taslağı size "
                      "gösterir, siz “gönder” demeden göndermem. Kesmek için “mail bağlantısını kes” yazın."),
        "login_failed": ("Giriş yapılamadı. Uygulama şifresini doğru yazdığınızdan ve hesapta 2 adımlı doğrulamanın açık "
                         "olduğundan emin olun. Tekrar denemek için “mail bağla” yazın."),
        "smtp_failed": "Okuma çalıştı ama gönderme girişi başarısız oldu. Şifreyi kontrol edip “mail bağla” ile tekrar deneyin.",
        "unreachable": "Posta sunucusuna ulaşılamadı. Biraz sonra tekrar deneyin.",
        "cancelled": "Vazgeçildi.",
        "disconnected": "E-posta bağlantısı kesildi ve kayıtlı giriş bilgisi silindi. Uygulama şifresini Google hesabınızdan da silebilirsiniz.",
        "not_connected": "E-posta bağlı değil.",
        "history_head": "Son e-posta işlemleri (içerik saklanmaz):",
        "history_empty": "Henüz e-posta işlemi yok.",
        "sent": "Gönderildi: {to} adresine “{subject}”.",
        "send_failed": "Gönderilemedi, taslak duruyor. Biraz sonra “gönder” yazarak tekrar deneyin.",
        "no_draft": "Gönderilecek bir taslak yok.",
        "draft_dropped": "Taslak silindi, e-posta gönderilmedi.",
    },
    "en": {
        "plus_only": "The email assistant is part of the Plus plan. You can also use it in the 7-day free trial.",
        "no_key": "Email is not switched on in this system yet. Please talk to the person who set you up.",
        "ask_address": ("Type your email address (for example name@gmail.com). Say “cancel” to stop.\n\n"
                        "Note: I will ask for an app password made just for this, not your normal password. Even safer: "
                        "ask the person who set you up to connect it with you in the admin screen."),
        "ask_password": ("Now send your app password (Google: myaccount.google.com/apppasswords, a 16-character code). "
                         "Delete that message from the chat afterwards. It only works for email and you can revoke it "
                         "there at any time."),
        "bad_address": "That does not look like an email address. Type it again or say “cancel”.",
        "connected": ("Email connected: {address}. I will read when you ask, and I will show you a draft before sending; "
                      "I never send until you say “send”. To disconnect, write “disconnect mail”."),
        "login_failed": ("Login failed. Check the app password and that 2-step verification is on for the account. "
                         "Write “connect mail” to try again."),
        "smtp_failed": "Reading worked but the sending login failed. Check the password and try “connect mail” again.",
        "unreachable": "I could not reach the mail server. Please try again in a moment.",
        "cancelled": "Cancelled.",
        "disconnected": "Email disconnected and the stored login deleted. You can also delete the app password in your Google account.",
        "not_connected": "Email is not connected.",
        "history_head": "Recent email actions (contents are never stored):",
        "history_empty": "No email actions yet.",
        "sent": "Sent to {to}: “{subject}”.",
        "send_failed": "It could not be sent and the draft is kept. Write “send” again in a moment.",
        "no_draft": "There is no draft to send.",
        "draft_dropped": "Draft discarded. Nothing was sent.",
    },
}


def t(lang: str, key: str, **kw) -> str:
    return T["en" if lang == "en" else "tr"][key].format(**kw)


def _reason(lang: str, code: str) -> str:
    return t(lang, code if code in T["tr"] else "unreachable")


def handle(client: dict, cmd: str, text: str) -> tuple[str | None, bool]:
    """(reply or None, secret): secret=True means the incoming message held a password and must be scrubbed."""
    phone, lang, state = client["phone"], client["lang"], client.get("pending_action") or ""

    if state == "mail_addr":
        if cmd in DISCARD:
            db.set_client_fields(phone, pending_action="")
            return t(lang, "cancelled"), False
        address = (text or "").strip().lower()
        if not mail.ADDRESS_RE.match(address):
            return t(lang, "bad_address"), False
        db.set_client_fields(phone, pending_action="mail_pass|" + address)
        return t(lang, "ask_password"), False

    if state.startswith("mail_pass|"):
        db.set_client_fields(phone, pending_action="")
        if cmd in DISCARD:
            return t(lang, "cancelled"), False
        try:
            address = mail.connect(phone, state.split("|", 1)[1], text)
        except mail.MailError as exc:
            return _reason(lang, str(exc)), True
        return t(lang, "connected", address=address), True

    if cmd in CONNECT:
        if not plans.has_feature(client, "mail"):
            return t(lang, "plus_only"), False
        if not vault.available():
            return t(lang, "no_key"), False
        db.set_client_fields(phone, pending_action="mail_addr")
        return t(lang, "ask_address"), False

    if cmd in DISCONNECT:
        return t(lang, "disconnected" if mail.disconnect(phone) else "not_connected"), False

    if cmd in HISTORY:
        rows = mail.history(phone)
        if not rows:
            return t(lang, "history_empty"), False
        lines = [f"{r['at'].replace('T', ' ')[:16]}  {r['action']}" + (f"  {r['detail']}" if r["detail"] else "") for r in rows]
        return t(lang, "history_head") + "\n" + "\n".join(lines), False

    if cmd in SEND and mail.pending_draft(phone):
        if not plans.has_feature(client, "mail"):
            mail.discard_draft(phone)
            return t(lang, "plus_only"), False
        try:
            draft = mail.send_pending(phone)
        except mail.MailError as exc:
            return t(lang, "send_failed" if str(exc) == "send_failed" else "no_draft"), False
        plans.record(phone, "mail")
        return t(lang, "sent", to=draft["to_addr"], subject=draft["subject"] or "-"), False

    if cmd in DISCARD and mail.pending_draft(phone):
        mail.discard_draft(phone)
        return t(lang, "draft_dropped"), False

    return None, False
