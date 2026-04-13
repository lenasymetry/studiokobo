"""Sanitizers DXF: noms de tables et textes ASCII."""

from __future__ import annotations

import re
import unicodedata

SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9_-]+")
SAFE_TEXT_RE = re.compile(r"[^\x20-\x7E]")


def normalize_ascii(value: str) -> str:
    return unicodedata.normalize("NFKD", str(value or "")).encode("ascii", "ignore").decode("ascii")


def limit_length(value: str, max_len: int) -> str:
    return (value or "")[: max(1, int(max_len))]


def sanitize_layer_name(name: str, fallback: str = "LAYER", max_len: int = 31) -> str:
    cleaned = SAFE_NAME_RE.sub("_", normalize_ascii(name)).strip("_")
    if not cleaned:
        cleaned = fallback
    return limit_length(cleaned, max_len)


def sanitize_table_name(name: str, fallback: str = "NAME", max_len: int = 31) -> str:
    return sanitize_layer_name(name=name, fallback=fallback, max_len=max_len)


def sanitize_text(text: str, fallback: str = "") -> str:
    cleaned = SAFE_TEXT_RE.sub("", normalize_ascii(text)).strip()
    return cleaned or fallback
