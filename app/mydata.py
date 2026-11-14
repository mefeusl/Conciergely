"""'My data': the client sees what the assistant remembers about them, numbered, and can edit or delete single items.

Commands (after folding case and accents): `verilerim` / `my data` shows the list; `sil 3` / `delete 3` removes item 3;
`degistir 3: yeni metin` / `edit 3: new text` replaces it; `hepsini unut` (already global) erases everything.
Only the memory map is shown here. Chat messages and reminders are never listed back.
"""
from __future__ import annotations

import re

from . import memory, textutil

SHOW = {"verilerim", "my data", "neler biliyorsun", "ne biliyorsun", "benim hakkimda ne biliyorsun", "what do you know about me",
        "what do you know", "hafizam", "my memory", "bilgilerim"}
_DELETE = re.compile(r"^(sil|unut|delete|forget|remove)\s+(\d{1,3})$")
_EDIT = re.compile(r"^(degistir|duzelt|duzenle|edit|change)\s+(\d{1,3})\s*[:\-]\s*(.+)$", re.DOTALL)

TEXT = {
    "tr": {"head": "Sizin hakkınızda hatırladıklarım:", "empty": "Şu an hakkınızda kayıtlı bir şey yok.",
           "how": ("\n\nBir maddeyi silmek için 'sil 3', değiştirmek için 'değiştir 3: yeni metin', hepsini silmek için 'hepsini unut' yazın. "
                   "Yazışmalarınız ve hatırlatmalarınız bu listede gösterilmez; hatırlatmalar şifreli saklanır."),
           "deleted": "Sildim: {n}. madde.", "edited": "Güncelledim: {n}. madde.", "missing": "Bu numarada bir madde yok. 'verilerim' yazarak listeyi görün.",
           "refused": "Kart, IBAN veya şifre gibi bilgileri kaydetmiyorum.", "invalid": "Yeni metin çok kısa."},
    "en": {"head": "What I remember about you:", "empty": "I have nothing saved about you right now.",
           "how": ("\n\nWrite 'delete 3' to remove an item, 'edit 3: new text' to change it, or 'forget everything' to erase it all. "
                   "Your chats and reminders are not shown here; reminders are stored encrypted."),
           "deleted": "Deleted item {n}.", "edited": "Updated item {n}.", "missing": "There is no item with that number. Write 'my data' for the list.",
           "refused": "I don't store card numbers, IBANs or passwords.", "invalid": "The new text is too short."},
}


def handle(client: dict, cmd: str, raw: str) -> str | None:
    lang = "en" if client.get("lang") == "en" else "tr"
    t = TEXT[lang]
    phone = client["phone"]
    if cmd in SHOW:
        facts = memory.flat(phone)
        if not facts:
            return t["empty"]
        return t["head"] + "\n" + "\n".join(f"{i}. {text}" for i, (_, text) in enumerate(facts, 1)) + t["how"]
    m = _DELETE.match(cmd)
    if m:
        n = int(m.group(2))
        return t["deleted"].format(n=n) if memory.remove_nth(phone, n) else t["missing"]
    m = _EDIT.match(textutil.fold(raw).strip())
    if m:
        n = int(m.group(2))
        new_text = re.split(r"[:\-]", raw, maxsplit=1)[1].strip() if re.search(r"[:\-]", raw) else ""
        result = memory.edit_nth(phone, n, new_text)
        return t["edited"].format(n=n) if result == "ok" else t.get(result, t["missing"])
    return None
