"""Normalize raw JobRecords: detect role type, language, clean fields."""

from __future__ import annotations

import logging
import re
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from src.models import JobRecord

log = logging.getLogger(__name__)

# Tracking parameters that make one posting look like several distinct URLs.
TRACKING_PARAMS = re.compile(
    r"^(utm_\w+|gclid|fbclid|msclkid|mc_[ce]id|ref|referrer|source|src|"
    r"campaign|trk|trkCampaign|origin|from|sessionid|_ga)$",
    re.I,
)

WHITESPACE = re.compile(r"\s+")

ROLE_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("internship", re.compile(r"\bintern(ship|s)?\b", re.I)),
    ("praktikum", re.compile(r"\bpraktikum\b|\bpraktikant\w*\b", re.I)),
    ("werkstudent", re.compile(r"\bwerkstudent\w*\b|\bworking\s*student\b", re.I)),
    ("research assistant", re.compile(r"\bresearch\s*assistant\b", re.I)),
    ("studentische hilfskraft", re.compile(r"studentische\s*hilfskraft", re.I)),
    ("hiwi", re.compile(r"\bhiwi\b|\bhi-wi\b", re.I)),
    ("shk", re.compile(r"\bshk\b", re.I)),
]

ENGLISH_SIGNALS = re.compile(
    r"(working\s*language\s*:?\s*english|english\s*(speaking|required|preferred)|"
    r"fluent\s*english|business\s*english|language\s*:?\s*english|"
    r"english\s*is\s*(the\s*)?(main|working)|jobs?\s*in\s*english)",
    re.I,
)
GERMAN_ONLY_SIGNALS = re.compile(
    r"(deutsch\s*(als\s*)?(arbeitssprache|erforderlich)|"
    r"verhandlungssicher|flie[sß]end\s*deutsch|"
    r"deutschkenntnisse\s*(mindestens\s*)?(b2|c1|c2)|"
    r"german\s*(at\s*least\s*)?(b2|c1|c2)|"
    r"native\s*(german|deutsch))",
    re.I,
)


def detect_role_type(text: str, configured: list[str]) -> str:
    lower_cfg = [c.lower() for c in configured]
    for label, pat in ROLE_PATTERNS:
        if pat.search(text) and label in lower_cfg:
            return label
        # also allow fuzzy membership e.g. "working student" in config
        for c in lower_cfg:
            if c in label or label in c:
                if pat.search(text):
                    return c
    # Fallback: any configured phrase literally present
    low = text.lower()
    for c in lower_cfg:
        if c.lower() in low:
            return c
    return ""


def canonical_url(url: str) -> str:
    """Strip tracking params and fragments so one posting is one URL.

    The store keys on URL, so `?utm_source=x` variants used to be stored as
    separate jobs and show up as duplicate rows in the sheet.
    """
    url = (url or "").strip()
    if not url:
        return ""
    try:
        parsed = urlparse(url)
    except ValueError:
        return url
    if not parsed.scheme or not parsed.netloc:
        return url
    kept = [
        (k, v)
        for k, v in parse_qsl(parsed.query, keep_blank_values=False)
        if not TRACKING_PARAMS.match(k)
    ]
    netloc = parsed.netloc.lower()
    if netloc.startswith("www."):
        netloc = netloc[4:]
    path = parsed.path.rstrip("/") or "/"
    return urlunparse(
        (parsed.scheme.lower(), netloc, path, parsed.params, urlencode(kept), "")
    )


def clean_title(title: str) -> str:
    """Collapse whitespace and drop boilerplate scraped from listing pages."""
    title = WHITESPACE.sub(" ", (title or "").strip(" -–—|·\t\n "))
    # Listing links often end with a call to action
    title = re.sub(
        r"\s*[-–—|·]\s*(apply\s*now|jetzt\s*bewerben|mehr\s*erfahren|read\s*more|"
        r"details?|view\s*job)\s*$",
        "",
        title,
        flags=re.I,
    )
    return title.strip()


def detect_language(text: str) -> str:
    if ENGLISH_SIGNALS.search(text):
        return "en"
    if GERMAN_ONLY_SIGNALS.search(text):
        return "de"
    # Simple heuristic: mostly ASCII / English stopwords → en
    if re.search(r"\b(the|and|with|you|will|team)\b", text, re.I) and not re.search(
        r"\b(und|mit|wir|sie|gesucht)\b", text, re.I
    ):
        return "en"
    if re.search(r"\b(und|mit|wir|gesucht|kenntnisse)\b", text, re.I):
        return "de"
    return "unknown"


def normalize_jobs(jobs: list[JobRecord], cfg: dict[str, Any]) -> list[JobRecord]:
    role_types = cfg.get("role_types") or []
    out: list[JobRecord] = []
    seen_urls: set[str] = set()
    for j in jobs:
        title = clean_title(j.title)
        company = WHITESPACE.sub(" ", (j.company or "").strip()) or "Unknown"
        location = WHITESPACE.sub(" ", (j.location or "").strip()) or "Germany"
        description = (j.description or "").strip()
        url = canonical_url(j.url)
        if not title or not url:
            continue
        if url in seen_urls:
            continue
        seen_urls.add(url)
        blob = f"{title}\n{description}"
        j.title = title
        j.company = company
        j.location = location
        j.description = description
        j.url = url
        j.role_type = detect_role_type(blob, role_types) or j.role_type
        j.language = detect_language(blob) if not j.language else j.language
        if j.posted_date:
            j.posted_date = str(j.posted_date)[:10]
        out.append(j)
    log.info(
        "Normalize: %s → %s (dropped empty title/url and same-URL repeats)",
        len(jobs),
        len(out),
    )
    return out


__all__ = [
    "normalize_jobs",
    "canonical_url",
    "clean_title",
    "detect_language",
    "detect_role_type",
]
