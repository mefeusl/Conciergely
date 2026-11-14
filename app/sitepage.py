"""The public site: landing page, 404 page, robots.txt and the security headers for them.

Everything here is plain functions (no web framework) so it can be tested without a server. The pages are static
files in site/; the owner's contact details are filled in from .env. Nothing secret is ever put in a page: only the
public contact details the owner chose to show.
"""
from __future__ import annotations

import base64
import hashlib
import html as _html
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import NamedTuple

from . import config

SITE_DIR = Path(__file__).resolve().parent.parent / "site"
STATIC_DIR = SITE_DIR / "static"  # the only folder served as plain files

PERMISSIONS_POLICY = "camera=(), microphone=(), geolocation=(), payment=(), usb=(), interrupt-cohort=()"
_SCRIPT = re.compile(r"<script(?P<attrs>[^>]*)>(?P<body>.*?)</script>", re.S | re.I)
_STYLE = re.compile(r"<style[^>]*>(?P<body>.*?)</style>", re.S | re.I)


class Page(NamedTuple):
    html: str
    csp: str


def _lira(amount: int) -> str:
    return f"{amount:,}".replace(",", ".") + " TL"


def contact() -> dict:
    return {"price_lite": _lira(config.PRICE_LITE_MONTH), "price_basic": _lira(config.PRICE_BASIC_MONTH), "price_plus": _lira(config.PRICE_PLUS_MONTH),
            "setup_basic": _lira(config.SETUP_BASIC), "setup_plus": _lira(config.SETUP_PLUS),
            "half_months": config.HALF_PRICE_MONTHS, "half_percent": config.HALF_PRICE_PERCENT,
            "name": config.BOT_NAME, "email": config.CONTACT_EMAIL.strip(), "whatsapp": "".join(
        ch for ch in config.CONTACT_WHATSAPP if ch.isdigit()),
        "phone": config.CONTACT_PHONE.strip(),
        "website": config.CONTACT_WEBSITE.strip()}


def _hash(text: str) -> str:
    return "'sha256-" + base64.b64encode(hashlib.sha256(text.encode("utf-8")).digest()).decode() + "'"


def csp_for(html: str) -> str:
    """A strict policy for one page: only its own inline script and style may run (by hash), nothing else loads."""
    scripts = [_hash(m.group("body")) for m in _SCRIPT.finditer(html)
               if "src=" not in m.group("attrs").lower() and "json" not in m.group("attrs").lower()]
    styles = [_hash(m.group("body")) for m in _STYLE.finditer(html)]
    return "; ".join([
        "default-src 'none'",
        "script-src " + (" ".join(scripts) or "'none'"),
        "style-src " + (" ".join(styles) or "'none'"),
        "img-src 'self' data:",
        "connect-src 'none'",
        "base-uri 'self'",
        "form-action 'none'",
        "frame-ancestors 'none'",
    ])


def canonical() -> str:
    site = config.CONTACT_WEBSITE.strip().rstrip("/")
    if re.fullmatch(r"https://[A-Za-z0-9.-]+(:\d+)?", site):
        return '<link rel="canonical" href="%s/">' % site
    return ""


def _fill(text: str) -> str:
    blob = json.dumps(contact(), ensure_ascii=False).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    return (text.replace("__SITE_JSON__", blob)
            .replace("__YEAR__", str(datetime.now(timezone.utc).year))
            .replace("__CANONICAL__", canonical()))


def _page(name: str) -> Page | None:
    path = SITE_DIR / name
    if not path.is_file():
        return None
    html = _fill(path.read_text(encoding="utf-8"))
    return Page(html, csp_for(html))


def landing() -> Page | None:
    return _page("index.html")


def not_found() -> Page | None:
    return _page("404.html")


def _contact_text() -> str:
    """Where to write, as escaped plain text. Never invents a name or address."""
    c = contact()
    parts = []
    if c["email"]:
        parts.append(c["email"])
    if c["whatsapp"]:
        parts.append("WhatsApp +" + c["whatsapp"])
    return _html.escape(" / ".join(parts)) if parts else "the contact details of the person who gave you access"


def privacy() -> Page | None:
    return _legal("privacy.html")


def terms() -> Page | None:
    return _legal("terms.html")


def _legal(name: str) -> Page | None:
    path = SITE_DIR / name
    if not path.is_file():
        return None
    page = _fill(path.read_text(encoding="utf-8")).replace("__CONTACT__", _contact_text())
    return Page(page, csp_for(page))


def render() -> str | None:
    page = landing()
    return page.html if page else None


def robots_txt() -> str:
    lines = ["User-agent: *", "Allow: /", "Disallow: /admin", "Disallow: /webhook", "Disallow: /health", "Disallow: /talk/"]
    site = config.CONTACT_WEBSITE.strip().rstrip("/")
    if re.fullmatch(r"https://[A-Za-z0-9.-]+(:\d+)?", site):
        lines.append("Sitemap: %s/sitemap.xml" % site)
    return "\n".join(lines) + "\n"


def sitemap_xml() -> str | None:
    site = config.CONTACT_WEBSITE.strip().rstrip("/")
    if not re.fullmatch(r"https://[A-Za-z0-9.-]+(:\d+)?", site):
        return None
    return ('<?xml version="1.0" encoding="UTF-8"?>\n'
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"><url><loc>%s/</loc></url><url><loc>%s/privacy</loc></url><url><loc>%s/terms</loc></url></urlset>\n' % (site, site, site))


def wants_html(accept: str) -> bool:
    return "text/html" in (accept or "").lower()


def security_headers(path: str, https: bool) -> dict:
    """Headers added to every response. /admin gets the strictest set; HSTS only when the request came in over HTTPS."""
    h = {
        "X-Content-Type-Options": "nosniff",
        "Referrer-Policy": "same-origin",
        "Permissions-Policy": PERMISSIONS_POLICY,
        "Cross-Origin-Opener-Policy": "same-origin",
        "X-Frame-Options": "DENY",
    }
    if https:
        h["Strict-Transport-Security"] = "max-age=15552000"  # six months, no subdomains, no preload
    if path.startswith("/talk/"):  # personal voice-setup page: never cached or indexed, and the only page allowed the microphone
        h["Cache-Control"] = "no-store"
        h["X-Robots-Tag"] = "noindex, nofollow"
        h["Referrer-Policy"] = "no-referrer"
        h["Permissions-Policy"] = PERMISSIONS_POLICY.replace("microphone=()", "microphone=(self)")
    if path.startswith("/admin"):
        h["Cache-Control"] = "no-store"
        h["X-Robots-Tag"] = "noindex, nofollow"
        h["Content-Security-Policy"] = ("default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; "
                                        "base-uri 'none'; frame-ancestors 'none'")
    return h


DEFAULT_CSP = "default-src 'none'; frame-ancestors 'none'; base-uri 'none'"
