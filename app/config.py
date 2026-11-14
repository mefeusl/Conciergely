"""Central configuration, read from environment variables / the .env file.

Values are module attributes so tests can override them with monkeypatch.
"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


def _get(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def _flag(name: str, default: str = "0") -> bool:
    return _get(name, default).lower() in {"1", "true", "yes", "on"}


def _int(name: str, default: int) -> int:
    try:
        return int(_get(name, str(default)))
    except ValueError:
        return default


def _float(name: str, default: float) -> float:
    try:
        return float(_get(name, str(default)))
    except ValueError:
        return default


def _path(name: str, default: str) -> str:
    raw = _get(name, default)
    p = Path(raw)
    return str(p if p.is_absolute() else BASE_DIR / p)


# --- AI ---
ANTHROPIC_API_KEY = _get("ANTHROPIC_API_KEY")
CLAUDE_MODEL = _get("CLAUDE_MODEL", "claude-sonnet-5-5")
OPENAI_API_KEY = _get("OPENAI_API_KEY")
WEB_SEARCH = _flag("WEB_SEARCH", "1")  # Claude's built-in web search tool (must be enabled in the Anthropic Console)
SERPAPI_KEY = _get("SERPAPI_KEY")  # SerpApi: Google Flights fares and times as data (optional, paid after 250 free searches/month)
MAX_FLIGHT_SEARCHES_PER_DAY = _int("MAX_FLIGHT_SEARCHES_PER_DAY", 10)  # per client, protects your search quota
MODEL_FAST = _get("MODEL_FAST", "claude-haiku-5-5")  # cheap model for short, simple chat (smart routing)
MODEL_ROUTING = _flag("MODEL_ROUTING", "1")  # 0 = always use CLAUDE_MODEL
AERODATABOX_KEY = _get("AERODATABOX_KEY")  # RapidAPI key for live flight status (optional)

# --- WhatsApp provider: "meta" (default) or "twilio" ---
WHATSAPP_PROVIDER = _get("WHATSAPP_PROVIDER", "meta").lower()

META_ACCESS_TOKEN = _get("META_ACCESS_TOKEN")
META_PHONE_NUMBER_ID = _get("META_PHONE_NUMBER_ID")
META_APP_SECRET = _get("META_APP_SECRET")
META_VERIFY_TOKEN = _get("META_VERIFY_TOKEN")
META_WABA_ID = _get("META_WABA_ID")  # reference only, not required
META_API_VERSION = _get("META_API_VERSION", "v23.0")
# Approved template with ONE body variable ({{1}}); used for reminders/alerts after 24h of silence.
META_TEMPLATE_NAME = _get("META_TEMPLATE_NAME")
META_TEMPLATE_LANG = _get("META_TEMPLATE_LANG", "tr")

TWILIO_ACCOUNT_SID = _get("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = _get("TWILIO_AUTH_TOKEN")
TWILIO_WHATSAPP_FROM = _get("TWILIO_WHATSAPP_FROM", "whatsapp:+14155238886")

# --- Web / admin ---
ADMIN_PASSWORD = _get("ADMIN_PASSWORD")
PUBLIC_BASE_URL = _get("PUBLIC_BASE_URL")

# --- Plans (basic / plus) ---
PLUS_ONLY = {x.strip() for x in _get("PLUS_ONLY", "trading,briefing,mail").lower().split(",") if x.strip()}
PLAN_LIMITS_JSON = _get("PLAN_LIMITS_JSON")  # optional override, e.g. {"basic": {"voice": 100}, "plus": {"flights": 40}}

TRIAL_DAYS = _int("TRIAL_DAYS", 7)  # free Plus trial for each new client, counted from their first message (0 = no trial)

# --- Billing by bank transfer (see app/billing.py). Off until BILLING_ON=1, so a deploy never freezes anyone by surprise. ---
BILLING_ON = _flag("BILLING_ON")
BANK_IBAN = _get("BANK_IBAN")  # the account clients pay into (spaces are fine)
BANK_HOLDER = _get("BANK_HOLDER")  # name on that account
BANK_NAME = _get("BANK_NAME")
PRICE_BASIC_MONTH = _int("PRICE_BASIC_MONTH", 1000)  # TL per month, full price
PRICE_PLUS_MONTH = _int("PRICE_PLUS_MONTH", 2000)
PRICE_LITE_MONTH = _int("PRICE_LITE_MONTH", 500)  # Lite: plain chat only, no reminders, no stocks, no tools
SETUP_BASIC = _int("SETUP_BASIC", 1000)  # one-time TL
SETUP_PLUS = _int("SETUP_PLUS", 2000)
SETUP_LITE = _int("SETUP_LITE", 0)
HALF_PRICE_MONTHS = _int("HALF_PRICE_MONTHS", 2)  # the first N paid months cost HALF_PRICE_PERCENT of the full price
HALF_PRICE_PERCENT = _int("HALF_PRICE_PERCENT", 50)
REMIND_DAYS_BEFORE = _int("REMIND_DAYS_BEFORE", 5)
LAST_REMINDER_DAYS_AFTER = _int("LAST_REMINDER_DAYS_AFTER", 2)
FREEZE_DAYS_AFTER = _int("FREEZE_DAYS_AFTER", 4)
GRACE_DAYS = _int("GRACE_DAYS", 3)  # extra time while a receipt waits for the owner to check it

# --- Referrals (see app/referrals.py) ---
REFERRALS_ON = _flag("REFERRALS_ON", "1")  # unknown numbers get a reply; a valid REF-code registers them
REF_SIGNUPS_PER_DAY = _int("REF_SIGNUPS_PER_DAY", 10)  # automatic sign-ups allowed per day
REF_FRIEND_PCT = _int("REF_FRIEND_PCT", 10)  # a referred friend's discount on the first payment
PHASH_SALT = _get("PHASH_SALT", "conciergely-v1")  # keeps the one-way number fingerprints stable; do NOT change after launch
REF_LADDER = _get("REF_LADDER", "1:25,3:35,6:50")  # "paid friend number from : discount percent for that friend's credit"

# --- Mail, calls, ops, backups
MAIL_MASTER_KEY = _get("MAIL_MASTER_KEY")  # 32 random bytes, urlsafe base64; encrypts every client's mail login (see README)
MAIL_MASTER_KEY_OLD = _get("MAIL_MASTER_KEY_OLD")  # during a key change: the previous key(s), comma separated
MAIL_TIMEOUT = _int("MAIL_TIMEOUT", 20)
MAIL_RELAY_URL = _get("MAIL_RELAY_URL")  # https://send.<your domain>: sends mail via the second server (see relay/README.md)
MAIL_RELAY_KEY = _get("MAIL_RELAY_KEY")  # the shared secret printed by relay/install.sh
MAIL_DRAFT_MINUTES = _int("MAIL_DRAFT_MINUTES", 30)  # a drafted mail can be sent for this long after it was written
CALLS_PER_DAY = _int("CALLS_PER_DAY", 3)
BACKUP_DIR = _get("BACKUP_DIR", "data/backups")
BACKUP_KEEP = _int("BACKUP_KEEP", 14)
# Rates for the cost estimate in /admin (US dollars). They are estimates: match them to your real plans.
COST_IN_PER_M = _float("COST_IN_PER_M", 3.0)      # AI input, per million tokens
COST_OUT_PER_M = _float("COST_OUT_PER_M", 15.0)   # AI output, per million tokens
COST_VOICE = _float("COST_VOICE", 0.003)          # one voice note understood
COST_BRIEFING = _float("COST_BRIEFING", 0.03)     # one spoken morning briefing (speech plus its AI call)
COST_FLIGHT = _float("COST_FLIGHT", 0.015)        # one flight search
USD_TRY = _float("USD_TRY", 0.0)                  # 0 = do not show lira
MONTHLY_FEE_TL = _float("MONTHLY_FEE_TL", 0.0)    # what a client pays you per month, to show cost as a share of it
CONTACT_EMAIL = _get("CONTACT_EMAIL")  # shown at the end of the landing page (all optional)
CONTACT_WHATSAPP = _get("CONTACT_WHATSAPP")
CONTACT_PHONE = _get("CONTACT_PHONE")  # shown as a tap-to-call link; optional
CONTACT_WEBSITE = _get("CONTACT_WEBSITE")

# --- Abuse protection: a client who keeps sending huge prompts, or burns huge amounts of AI, is frozen automatically ---
OWNER_PHONE = _get("OWNER_PHONE")  # your personal number, shown to a frozen client so they can call you
ABUSE_HUGE_CHARS = _int("ABUSE_HUGE_CHARS", 2500)  # a message this long counts as a "huge prompt"
ABUSE_HUGE_PER_HOUR = _int("ABUSE_HUGE_PER_HOUR", 6)  # this many huge prompts within an hour freezes the client
ABUSE_TOKENS_PER_HOUR = _int("ABUSE_TOKENS_PER_HOUR", 1_000_000)  # AI tokens used within an hour that freeze the client

# --- Morning audio briefing (Plus) ---
BRIEFING_DEFAULT_MINUTES = _int("BRIEFING_DEFAULT_MINUTES", 8 * 60 + 30)  # 08:30 when a client just says "yes"
BRIEFING_GRACE_MINUTES = _int("BRIEFING_GRACE_MINUTES", 180)  # after an outage, still send if no later than this
TRANSCRIBE_MODEL = _get("TRANSCRIBE_MODEL", "gpt-4o-transcribe")  # falls back to whisper-1 if the account cannot use it
TRANSCRIBE_LANG_HINT = _flag("TRANSCRIBE_LANG_HINT")  # 1 = tell the model the client's language (bad for clients who mix languages)
TTS_MODEL = _get("TTS_MODEL", "gpt-4o-mini-tts")
WOW_ON = _flag("WOW_ON", "1")  # the first-day message after setup
WORKING_AFTER_SECONDS = _float("WORKING_AFTER_SECONDS", 5.0)  # slower than this: send a short "working on it" (0 = never)
TTS_VOICE = _get("TTS_VOICE", "alloy")
TTS_INSTRUCTIONS_TR = _get("TTS_INSTRUCTIONS_TR", "Konuşma dili Türkçe. Sıcak, doğal ve akıcı bir sunucu gibi, İstanbul Türkçesiyle, ne çok hızlı ne yavaş oku. Kısaltmaları ve sayıları yazıldığı gibi değil, söylendiği gibi seslendir.")
TTS_INSTRUCTIONS_EN = _get("TTS_INSTRUCTIONS_EN", "Speak English in a warm, natural, conversational tone like a good radio host. Moderate pace, no robotic pauses.")
SPORTS_API_KEY = _get("SPORTS_API_KEY") or "3"  # TheSportsDB; '3' is its public test key (limited lists). A paid key gives full lists.
BRIEF_NEWS_TEXT = _int("BRIEF_NEWS_TEXT", 8)  # headlines in the written briefing
BRIEF_NEWS_VOICE = _int("BRIEF_NEWS_VOICE", 3)  # headlines read aloud
# Voice setup: a personal link where a new client answers the setup questions by speaking. Off until VOICE_SETUP=1.
VOICE_SETUP = _flag("VOICE_SETUP")
VOICE_LINK_HOURS = _int("VOICE_LINK_HOURS", 48)
VOICE_MAX_TURNS = _int("VOICE_MAX_TURNS", 40)       # per link: caps the OpenAI cost of one client
VOICE_MAX_AUDIO = _int("VOICE_MAX_AUDIO", 1_500_000)  # bytes per recording

# --- Behaviour ---
BOT_NAME = _get("BOT_NAME", "Conciergely")
TIMEZONE = _get("TIMEZONE", "Europe/Istanbul")
DB_PATH = _path("DB_PATH", "data/assistant.db")
MEMORY_DIR = _path("MEMORY_DIR", "data/memory")
HISTORY_TURNS = _int("HISTORY_TURNS", 8)
ALERT_DEFAULT_THRESHOLD_PCT = _float("ALERT_DEFAULT_THRESHOLD_PCT", 3.0)
ALERT_CHECK_MINUTES = _int("ALERT_CHECK_MINUTES", 5)
WORKER_THREADS = _int("WORKER_THREADS", 2)
MAX_INBOUND_PER_HOUR = _int("MAX_INBOUND_PER_HOUR", 60)
MAX_FILES_PER_HOUR = _int("MAX_FILES_PER_HOUR", 20)  # photos + documents (they cost the most to read)

# --- Switches (tests turn these off) ---
DRY_RUN = _flag("DRY_RUN")  # 1 = log outgoing WhatsApp messages instead of sending
ENABLE_WORKER = _flag("ENABLE_WORKER", "1")
ENABLE_SCHEDULER = _flag("ENABLE_SCHEDULER", "1")


def problems() -> list[str]:
    """Human-readable list of missing settings, logged at startup (never raises)."""
    out: list[str] = []
    if not ANTHROPIC_API_KEY:
        out.append("ANTHROPIC_API_KEY is empty: the assistant cannot answer.")
    if WHATSAPP_PROVIDER == "meta":
        for name, val in (
            ("META_ACCESS_TOKEN", META_ACCESS_TOKEN),
            ("META_PHONE_NUMBER_ID", META_PHONE_NUMBER_ID),
            ("META_APP_SECRET", META_APP_SECRET),
            ("META_VERIFY_TOKEN", META_VERIFY_TOKEN),
        ):
            if not val:
                out.append(f"{name} is empty.")
    elif WHATSAPP_PROVIDER == "twilio":
        if not TWILIO_ACCOUNT_SID or not TWILIO_AUTH_TOKEN:
            out.append("TWILIO_ACCOUNT_SID / TWILIO_AUTH_TOKEN are empty.")
        if not PUBLIC_BASE_URL:
            out.append("PUBLIC_BASE_URL is empty: every Twilio message will be refused until it is set.")
    else:
        out.append(f"WHATSAPP_PROVIDER='{WHATSAPP_PROVIDER}' is not 'meta' or 'twilio'.")
    if not ADMIN_PASSWORD:
        out.append("ADMIN_PASSWORD is empty: the /admin page is disabled.")
    elif len(ADMIN_PASSWORD) < 12:
        out.append("ADMIN_PASSWORD is shorter than 12 characters.")
    if not OPENAI_API_KEY:
        out.append("OPENAI_API_KEY is empty: voice notes will not be transcribed.")
    if not MAIL_MASTER_KEY.strip():
        out.append("MAIL_MASTER_KEY is empty: reminder texts and mail logins are stored unencrypted.")
    return out
