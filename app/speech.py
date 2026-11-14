"""Turns a briefing script into text that sounds right when read aloud by a text-to-speech voice.

Models and text-to-speech both stumble on 'EUR/TRY', 'BIST 100', '%1,2' and '1.234,50'. This deterministic pass rewrites
them as the words a person would say, in ONE language (the script's), so the voice never mixes English letters into
Turkish speech. Pure functions, no network.
"""
from __future__ import annotations

import re

PAIRS = {
    "tr": {"EUR/TRY": "euro kuru", "USD/TRY": "dolar kuru", "GBP/TRY": "sterlin kuru", "EUR/USD": "euro dolar paritesi",
           "BIST 100": "Borsa İstanbul yüz endeksi", "BIST100": "Borsa İstanbul yüz endeksi", "XU100": "Borsa İstanbul yüz endeksi",
           "S&P 500": "S ve P beş yüz endeksi", "S&P500": "S ve P beş yüz endeksi", "Nasdaq": "Nasdaq", "VIX": "korku endeksi",
           "Brent": "Brent petrol", "10y": "on yıllık tahvil faizi"},
    "en": {"EUR/TRY": "euro to lira", "USD/TRY": "dollar to lira", "GBP/TRY": "pound to lira", "EUR/USD": "euro to dollar",
           "BIST 100": "the BIST one hundred index", "BIST100": "the BIST one hundred index", "XU100": "the BIST one hundred index",
           "S&P 500": "the S and P five hundred", "S&P500": "the S and P five hundred", "VIX": "the volatility index",
           "Brent": "Brent crude"},
}
WORDS = {"tr": {"percent": "yüzde", "lira": "lira", "up": "artı", "down": "eksi"},
         "en": {"percent": "percent", "lira": "lira", "up": "plus", "down": "minus"}}


def _number(raw: str, lang: str) -> str:
    """'1.234,50' (Turkish) or '1,234.50' (English) -> digits a voice reads naturally, with a spoken decimal mark."""
    if lang == "tr":
        if "," in raw:
            whole, _, frac = raw.partition(",")
            whole = whole.replace(".", "")
        elif re.fullmatch(r"\d{1,3}(\.\d{3})+", raw):
            whole, frac = raw.replace(".", ""), ""
        else:
            whole, _, frac = raw.partition(".")
    else:
        if "." in raw:
            whole, _, frac = raw.partition(".")
            whole = whole.replace(",", "")
        elif re.fullmatch(r"\d{1,3}(,\d{3})+", raw):
            whole, frac = raw.replace(",", ""), ""
        else:
            whole, _, frac = raw.partition(",")
    sep = " virgül " if lang == "tr" else " point "
    return whole + (sep + frac if frac else "")


_DAYS_TR = ["Pazartesi", "Salı", "Çarşamba", "Perşembe", "Cuma", "Cumartesi", "Pazar"]
_MONTHS_TR = ["Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran", "Temmuz", "Ağustos", "Eylül", "Ekim", "Kasım", "Aralık"]


def long_date(moment, lang: str) -> str:
    """'29 Ekim Perşembe' for Turkish, 'Thursday 29 October' for English: a briefing never mixes the two."""
    if lang == "tr":
        return f"{moment.day} {_MONTHS_TR[moment.month - 1]} {_DAYS_TR[moment.weekday()]}"
    return moment.strftime("%A %d %B")


def normalize(text: str, lang: str = "tr") -> str:
    """Spoken-word form of `text`. `lang` is 'tr' or 'en'; anything else is treated as English."""
    lang = "tr" if lang == "tr" else "en"
    w = WORDS[lang]
    out = text or ""
    for key in sorted(PAIRS[lang], key=len, reverse=True):
        out = re.sub(re.escape(key), PAIRS[lang][key], out, flags=re.IGNORECASE if key.isalpha() else 0)
    # signed percentages: +1.2% / -0,8% / %1,2 / 1.2%
    def pct(m: re.Match) -> str:
        sign = {"+": w["up"] + " ", "-": w["down"] + " ", "−": w["down"] + " "}.get(m.group("sign") or "", "")
        n = _number(m.group("n"), lang)
        return f" {sign}{w['percent']} {n} " if lang == "tr" else f" {sign}{n} {w['percent']} "
    out = re.sub(r"(?P<sign>[+\-−])?\s?%\s?(?P<n>\d[\d.,]*\d|\d)", pct, out)
    out = re.sub(r"(?P<sign>[+\-−])?\s?(?P<n>\d[\d.,]*\d|\d)\s?%", pct, out)
    out = re.sub(r"\.IS\b", "", out)  # THYAO.IS -> THYAO
    out = re.sub(r"\bTL\b|\bTRY\b|₺", w["lira"], out)
    out = out.replace("&", " ve " if lang == "tr" else " and ")
    out = re.sub(r"\d[\d.,]*\d", lambda m: _number(m.group(), lang), out)  # remaining decimals and grouped thousands
    out = re.sub(r"[*_#~`<>|\[\]{}]", " ", out)  # symbols a voice would read out loud
    out = re.sub(r"\s*[-–—]\s+", ", ", out)
    out = " ".join(out.split())
    out = re.sub(r"\(\s+", "(", out)
    return re.sub(r"\s+([,.)!?])", r"\1", out)
