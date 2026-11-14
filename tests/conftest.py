"""Shared test setup: every test gets a fresh database, memory folder and DRY_RUN outbox."""
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("ENABLE_WORKER", "0")
os.environ.setdefault("ENABLE_SCHEDULER", "0")

from app import bars as _bars, briefing, config, db, news, whatsapp  # noqa: E402

PHONE = "+905551234567"


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setattr(config, "MEMORY_DIR", str(tmp_path / "memory"))
    monkeypatch.setattr(config, "DRY_RUN", True)
    monkeypatch.setattr(config, "WHATSAPP_PROVIDER", "meta")
    monkeypatch.setattr(config, "META_APP_SECRET", "test-app-secret")
    monkeypatch.setattr(config, "META_VERIFY_TOKEN", "test-verify-token")
    monkeypatch.setattr(config, "META_ACCESS_TOKEN", "test-access-token")
    monkeypatch.setattr(config, "META_PHONE_NUMBER_ID", "123456789")
    monkeypatch.setattr(config, "META_TEMPLATE_NAME", "")
    monkeypatch.setattr(config, "ADMIN_PASSWORD", "correct-horse-battery")
    monkeypatch.setattr(config, "ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(config, "OPENAI_API_KEY", "")
    monkeypatch.setattr(config, "PUBLIC_BASE_URL", "")
    monkeypatch.setattr(config, "MODEL_ROUTING", False)  # routing has its own tests; the rest use one model
    monkeypatch.setattr(config, "PLAN_LIMITS_JSON", "")
    monkeypatch.setattr(config, "WOW_ON", False)  # the first-day message has its own tests
    monkeypatch.setattr(config, "WORKING_AFTER_SECONDS", 0)  # no timer threads in tests
    monkeypatch.setattr(news, "_get", lambda url: None)  # no test ever reaches the real internet
    monkeypatch.setattr(_bars, "corporate_actions", lambda symbol: [])
    briefing._news_cache.clear()
    whatsapp.OUTBOX.clear()
    whatsapp.TYPING.clear()
    db.init_db()
    yield


@pytest.fixture
def client():
    """A registered, fully onboarded client on the Plus plan (every feature on)."""
    c = db.upsert_client(PHONE, name="Test Client")
    db.set_client_fields(PHONE, onboarding_done=1, onboarding_step=11, plan="plus")
    return db.get_client(PHONE)


@pytest.fixture
def basic_client():
    """A registered, fully onboarded client on the Basic plan."""
    db.upsert_client(PHONE, name="Test Client")
    db.set_client_fields(PHONE, onboarding_done=1, onboarding_step=11)
    return db.get_client(PHONE)


# ---- a scripted stand-in for the Anthropic client ----

def text_block(text):
    return SimpleNamespace(type="text", text=text)


def tool_block(tool_id, name, **args):
    return SimpleNamespace(type="tool_use", id=tool_id, name=name, input=args)


def response(*blocks, stop="end_turn"):
    return SimpleNamespace(content=list(blocks), stop_reason=stop)


class FakeAnthropic:
    """messages.create() returns the scripted responses in order and records every call."""

    def __init__(self, *responses):
        self._responses = list(responses)
        self.calls = []
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        if not self._responses:
            raise AssertionError("FakeAnthropic ran out of scripted responses")
        item = self._responses.pop(0)
        if isinstance(item, BaseException):  # scripted API failure
            raise item
        return item


class ApiError(Exception):
    """Stand-in for an Anthropic API error: has the same status_code attribute."""

    def __init__(self, status_code, message="error"):
        super().__init__(message)
        self.status_code = status_code
