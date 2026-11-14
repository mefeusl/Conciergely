"""Envelope encryption for secrets (client mail logins, the calling key).

  * every secret gets its own random 256-bit data key; the data key is encrypted with the master key
    (MAIL_MASTER_KEY in .env) and stored next to the secret;
  * both layers use AES-256-GCM, and the owner (a client's phone number, or 'settings') is bound in as
    associated data, so a blob copied onto another client's row refuses to decrypt;
  * the master key never touches the database. A database file or backup alone is unreadable;
  * key rotation: put the new key in MAIL_MASTER_KEY, the old one in MAIL_MASTER_KEY_OLD, then run rewrap()
    (or let rewrap_all run from /admin). Blobs record which key made them.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from . import config


class VaultError(Exception):
    """No master key, a wrong key, or a blob that does not belong to this owner."""


def _decode(key: str) -> bytes:
    try:
        raw = base64.urlsafe_b64decode(key.strip() + "=" * (-len(key.strip()) % 4))
    except Exception as exc:
        raise VaultError("master key is not valid base64") from exc
    if len(raw) != 32:
        raise VaultError("master key must be 32 bytes (see README: how to make one)")
    return raw


def _keys() -> dict[str, bytes]:
    out: dict[str, bytes] = {}
    for text in [config.MAIL_MASTER_KEY, *(config.MAIL_MASTER_KEY_OLD or "").split(",")]:
        if text.strip():
            raw = _decode(text)
            out[hashlib.sha256(raw).hexdigest()[:8]] = raw
    return out


def available() -> bool:
    try:
        return bool(config.MAIL_MASTER_KEY.strip()) and bool(_keys())
    except VaultError:
        return False


def new_key() -> str:
    return base64.urlsafe_b64encode(os.urandom(32)).decode()


def _current() -> tuple[str, bytes]:
    if not config.MAIL_MASTER_KEY.strip():
        raise VaultError("MAIL_MASTER_KEY is not set")
    raw = _decode(config.MAIL_MASTER_KEY)
    return hashlib.sha256(raw).hexdigest()[:8], raw


def _b(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode()


def _u(text: str) -> bytes:
    return base64.urlsafe_b64decode(text.encode())


def seal(owner: str, plaintext: str) -> str:
    kid, master = _current()
    aad = owner.encode()
    data_key, n1, n2 = os.urandom(32), os.urandom(12), os.urandom(12)
    wrapped = AESGCM(master).encrypt(n1, data_key, aad)
    body = AESGCM(data_key).encrypt(n2, plaintext.encode(), aad)
    return json.dumps({"v": 1, "kid": kid, "n1": _b(n1), "wk": _b(wrapped), "n2": _b(n2), "ct": _b(body)})


def open_(owner: str, blob: str) -> str:
    try:
        doc = json.loads(blob)
        master = _keys().get(doc["kid"])
        if master is None:
            raise VaultError("the key that sealed this secret is not available")
        aad = owner.encode()
        data_key = AESGCM(master).decrypt(_u(doc["n1"]), _u(doc["wk"]), aad)
        return AESGCM(data_key).decrypt(_u(doc["n2"]), _u(doc["ct"]), aad).decode()
    except VaultError:
        raise
    except (InvalidTag, KeyError, ValueError, TypeError) as exc:
        raise VaultError("secret could not be opened (wrong key or wrong owner)") from exc


def key_id(blob: str) -> str:
    try:
        return json.loads(blob).get("kid", "")
    except ValueError:
        return ""


def current_key_id() -> str:
    try:
        return _current()[0]
    except VaultError:
        return ""
