"""Normalize raw JobRecords: detect role type, language, clean fields."""

from __future__ import annotations

import logging
import re
from typing import Any

from src.models import JobRecord

log = logging.getLogger(__name__)

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
    for j in jobs:
        title = (j.title or "").strip()
        company = (j.company or "").strip() or "Unknown"
        location = (j.location or "").strip() or "Germany"
        description = (j.description or "").strip()
        url = (j.url or "").strip()
        if not title or not url:
            continue
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
    log.info("Normalize: %s → %s (dropped empty title/url)", len(jobs), len(out))
    return out
