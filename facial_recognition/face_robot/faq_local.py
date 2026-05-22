"""Local Q&A: load phrases from JSON and match user text (no cloud)."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from face_robot import config

_ENTRIES: list[dict[str, Any]] | None = None


def _default_json_path() -> Path:
    base = Path(__file__).resolve().parent.parent
    p = config.FAQ_JSON_PATH.strip()
    if p:
        return Path(os.path.expanduser(p)).resolve()
    return (base / "data" / "nora_faq.json").resolve()


def load() -> None:
    global _ENTRIES
    path = _default_json_path()
    if not path.is_file():
        _ENTRIES = []
        return
    try:
        raw = path.read_text(encoding="utf-8").strip()
    except OSError:
        _ENTRIES = []
        return
    if not raw:
        _ENTRIES = []
        return
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ValueError(
            f"Invalid JSON in {path}: {e}. Restore data/nora_faq.json or fix FAQ_JSON_PATH."
        ) from e
    if not isinstance(data, list):
        raise ValueError(f"FAQ JSON must be a list: {path}")
    _ENTRIES = []
    for item in data:
        if not isinstance(item, dict):
            continue
        patterns = item.get("patterns") or []
        answer = (item.get("answer") or "").strip()
        if not answer or not isinstance(patterns, list):
            continue
        plist = [str(p).strip() for p in patterns if str(p).strip()]
        if plist:
            _ENTRIES.append({"patterns": plist, "answer": answer})


def entries() -> list[dict[str, Any]]:
    if _ENTRIES is None:
        load()
    return _ENTRIES or []


def _normalize(text: str) -> str:
    t = text.lower().strip()
    t = re.sub(r"[^a-z0-9\s]", " ", t)
    return " ".join(t.split())


def match(user_text: str) -> str | None:
    """Return a canned answer if user_text matches any pattern, else None."""
    if not user_text or not config.ENABLE_LOCAL_FAQ:
        return None
    ut = _normalize(user_text)
    if len(ut) < 2:
        return None
    for entry in entries():
        for pat in entry["patterns"]:
            pn = _normalize(pat)
            if len(pn) < 2:
                continue
            if pn in ut:
                return entry["answer"]
            words = [w for w in pn.split() if len(w) > 2]
            if len(words) >= 2 and all(w in ut for w in words):
                return entry["answer"]
    return None


def is_configured() -> bool:
    return bool(entries())


def is_ready() -> bool:
    """FAQ can run: enabled and JSON has at least one Q&A entry."""
    return bool(config.ENABLE_LOCAL_FAQ and is_configured())
