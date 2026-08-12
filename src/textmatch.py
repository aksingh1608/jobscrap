"""Shared keyword matching helpers.

Plain substring matching is why postings like "E-commerce Intern" used to pass
the AI/ML filters: `"ai" in "e-mail available"` is True. Every keyword check in
the pipeline goes through this module so short tokens are matched on word
boundaries instead.
"""

from __future__ import annotations

import re
from functools import lru_cache

def _to_pattern(keyword: str) -> re.Pattern[str]:
    kw = keyword.strip().lower()
    # Whitespace and hyphens are interchangeable: "machine learning" == "machine-learning"
    body = r"[\s\-_/]+".join(re.escape(part) for part in re.split(r"[\s\-_/]+", kw) if part)
    if not body:
        # Never match on an empty keyword
        return re.compile(r"(?!x)x")
    left = r"\b" if kw[0].isalnum() else ""
    right = r"\b" if kw[-1].isalnum() else ""
    return re.compile(f"{left}{body}{right}", re.I)


@lru_cache(maxsize=2048)
def _compiled(keyword: str) -> re.Pattern[str]:
    return _to_pattern(keyword)


def contains(text: str, keyword: str) -> bool:
    """True when `keyword` appears in `text` as a whole word / phrase."""
    if not text or not keyword:
        return False
    return bool(_compiled(keyword).search(text))


def any_match(text: str, keywords: list[str]) -> bool:
    return any(contains(text, k) for k in keywords)


def matched(text: str, keywords: list[str]) -> list[str]:
    """Every keyword present in `text`, in config order, deduplicated."""
    hits: list[str] = []
    for k in keywords:
        if contains(text, k) and k not in hits:
            hits.append(k)
    return hits


def quote_around(text: str, keyword: str, window: int = 30) -> str:
    """Return a short real snippet of `text` around `keyword` (never invented)."""
    m = _compiled(keyword).search(text)
    if not m:
        return ""
    start = max(0, m.start() - window)
    end = min(len(text), m.end() + window)
    return " ".join(text[start:end].split())


__all__ = ["contains", "any_match", "matched", "quote_around"]
