"""Admin-page security helpers (no web framework needed, so they are unit-tested directly)."""
from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import time
from urllib.parse import urlparse

from . import config

ADMIN_USER = "admin"


def check_admin_credentials(auth_header: str | None) -> bool:
    """Validate an HTTP Basic 'Authorization' header against ADMIN_PASSWORD (constant-time)."""
    if not config.ADMIN_PASSWORD or not auth_header:
        return False
    scheme, _, encoded = auth_header.partition(" ")
    if scheme.lower() != "basic":
        return False
    try:
        user, _, password = base64.b64decode(encoded.strip(), validate=True).decode("utf-8").partition(":")
    except (binascii.Error, UnicodeDecodeError, ValueError):
        return False
    user_ok = hmac.compare_digest(user.encode(), ADMIN_USER.encode())
    pass_ok = hmac.compare_digest(password.encode(), config.ADMIN_PASSWORD.encode())
    return user_ok and pass_ok


def csrf_token() -> str:
    """A token only someone logged in to the admin page can read (it is derived from the admin password)."""
    return hmac.new(config.ADMIN_PASSWORD.encode(), b"admin-csrf-v1", hashlib.sha256).hexdigest()


def csrf_ok(token: str | None) -> bool:
    return bool(config.ADMIN_PASSWORD and token) and hmac.compare_digest(token.encode(), csrf_token().encode())


def origin_ok(origin: str | None, host: str | None) -> bool:
    """Browsers send Origin on form posts. If it is present it must be this very site."""
    if not origin:
        return True  # non-browser client; the CSRF token still has to match
    return bool(host) and (urlparse(origin).netloc or "").lower() == host.lower()


class FailureLimiter:
    """Blocks a client address after too many failed logins within a time window."""

    def __init__(self, max_failures: int = 10, window_seconds: int = 900):
        self.max_failures, self.window = max_failures, window_seconds
        self._fails: dict[str, list[float]] = {}

    def _recent(self, key: str) -> list[float]:
        cutoff = time.monotonic() - self.window
        recent = [t for t in self._fails.get(key, []) if t > cutoff]
        if recent:
            self._fails[key] = recent
        else:
            self._fails.pop(key, None)
        return recent

    def blocked(self, key: str) -> bool:
        return len(self._recent(key)) >= self.max_failures

    def record_failure(self, key: str) -> None:
        self._recent(key)
        self._fails.setdefault(key, []).append(time.monotonic())

    def reset(self, key: str) -> None:
        self._fails.pop(key, None)
