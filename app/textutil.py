"""Text helpers shared by memory matching and chat commands."""
from __future__ import annotations

import re

# Fold Turkish letters (and a few accents) to plain ASCII so "İstanbul", "Istanbul" and "istanbul"
# all compare equal, and "GEÇ" matches "gec".
_FOLD = str.maketrans({
    "İ": "i", "I": "i", "ı": "i", "Ç": "c", "ç": "c", "Ğ": "g", "ğ": "g",
    "Ö": "o", "ö": "o", "Ş": "s", "ş": "s", "Ü": "u", "ü": "u",
    "Â": "a", "â": "a", "Î": "i", "î": "i", "Û": "u", "û": "u",
})


def fold(text: str) -> str:
    return (text or "").translate(_FOLD).lower()


def key(text: str) -> str:
    """Comparison key: folded, punctuation removed, whitespace collapsed."""
    return re.sub(r"[\W_]+", " ", fold(text)).strip()
