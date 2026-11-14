"""Playbooks: the 11 trading skills as method guides the assistant loads on demand.

The system prompt carries only a one-line index; `use_playbook` returns the full method for one topic,
so most messages pay no extra tokens. Files are shipped with the app (app/playbooks/*.md) and read-only.
"""
from __future__ import annotations

import os
import re
from functools import lru_cache

_DIR = os.path.join(os.path.dirname(__file__), "playbooks")
_NAME = re.compile(r"^[a-z][a-z\-]{1,40}$")


@lru_cache(maxsize=1)
def _load() -> dict[str, dict]:
    out: dict[str, dict] = {}
    try:
        names = sorted(f[:-3] for f in os.listdir(_DIR) if f.endswith(".md"))
    except OSError:
        return out
    for name in names:
        if not _NAME.match(name):
            continue
        with open(os.path.join(_DIR, name + ".md"), encoding="utf-8") as fh:
            raw = fh.read()
        m = re.match(r"^---\nname: .+\ndescription: (.+)\n---\n+(.*)$", raw, re.DOTALL)
        if not m:
            continue
        desc, body = m.group(1).strip(), m.group(2).strip()
        out[name] = {"description": desc, "summary": re.split(r"(?<=\.)\s", desc, maxsplit=1)[0], "body": body}
    return out


def names() -> list[str]:
    return list(_load())


def get(name: str) -> str | None:
    item = _load().get(str(name))
    return item["body"] if item else None


def index() -> str:
    """One line per playbook for the system prompt."""
    return "\n".join(f"  {n}: {p['summary']}" for n, p in _load().items())
