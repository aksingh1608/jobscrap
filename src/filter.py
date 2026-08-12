"""Hard filters: role type, domain, location, freshness, exclude words, German level.

Every keyword check goes through `src.textmatch` so short tokens ("ai", "ml",
"data") match whole words only. Substring matching used to let unrelated
postings through — "E-commerce Intern" matched "ai" inside "e-mail".
"""

from __future__ import annotations

import logging
import re
from datetime import date, datetime, timedelta
from typing import Any, Optional

from src.config import num
from src.models import JobRecord
from src.textmatch import any_match, contains

log = logging.getLogger(__name__)

# CEFR order for comparisons
CEFR_ORDER = {"A1": 1, "A2": 2, "B1": 3, "B2": 4, "C1": 5, "C2": 6}

GERMAN_HIGH_PATTERNS = [
    re.compile(r"verhandlungssicher", re.I),
    re.compile(r"flie[sß]end\s*(in\s*)?(deutsch|german)", re.I),
    re.compile(r"deutsch\s*(muttersprach|native)", re.I),
    re.compile(r"(german|deutsch)\s*(level|kenntnisse|skills)?\s*:?\s*(B1|B2|C1|C2)", re.I),
    re.compile(r"(mindestens|at\s*least|min\.?)\s*(B1|B2|C1|C2)", re.I),
    re.compile(r"sehr\s*gute\s*Deutschkenntnisse", re.I),
    re.compile(r"Deutschkenntnisse\s*(mindestens\s*)?(B1|B2|C1|C2)", re.I),
]

GERMAN_OK_PATTERNS = [
    re.compile(r"german\s*(is\s*)?(optional|a\s*plus|nice\s*to\s*have|not\s*required)", re.I),
    re.compile(r"deutsch\s*(von\s*vorteil|wünschenswert|optional|nicht\s*erforderlich)", re.I),
    re.compile(r"(no|kein)\s*(german|deutsch)\s*(required|nötig|erforderlich)", re.I),
    re.compile(r"english\s*(only|speaking|working\s*language)", re.I),
]

# Seniority signals in the *title* are a hard no for a student role, regardless
# of what the description mentions.
SENIOR_TITLE = re.compile(
    r"\b(senior|sr\.?|lead|principal|staff|head\s+of|director|manager|"
    r"architect|chief|vp|expert|teamleiter|abteilungsleiter)\b",
    re.I,
)

# Countries that regularly leak into "remote" postings we cannot take.
NON_DE_ONLY = re.compile(
    r"\b(us\s*only|usa\s*only|united\s*states\s*only|uk\s*only|india\s*only|"
    r"canada\s*only|must\s*be\s*(located|based)\s*in\s*the\s*(us|usa|uk|india))\b",
    re.I,
)

REMOTE_HINT = re.compile(r"\b(remote|homeoffice|home\s*office|hybrid|telearbeit)\b", re.I)


def _parse_date(value: Optional[str]) -> Optional[date]:
    if not value:
        return None
    value = str(value)[:10]
    for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    return None


def days_since_posted(job: JobRecord) -> Optional[int]:
    posted = _parse_date(job.posted_date)
    if posted is None:
        return None
    return max(0, (date.today() - posted).days)


def _has_role(job: JobRecord, role_types: list[str]) -> bool:
    if job.role_type:
        return True
    return any_match(f"{job.title} {job.description}", role_types)


def _has_domain(job: JobRecord, domains: list[str]) -> bool:
    return any_match(f"{job.title} {job.description}", domains)


def _location_ok(job: JobRecord, locations: list[str]) -> bool:
    blob = f"{job.location} {job.description}"
    if NON_DE_ONLY.search(blob):
        return False
    if any_match(blob, locations):
        return True
    if REMOTE_HINT.search(blob) and any_match(blob, ["germany", "deutschland", "europe", "eu"]):
        return True
    # Many German postings omit the country entirely — trust DE-only sources.
    if job.source.startswith("arbeitsagentur") or job.source.startswith("custom:"):
        return True
    return False


def _fresh_enough(job: JobRecord, freshness_days: int) -> bool:
    age = days_since_posted(job)
    if age is None:
        # Unknown date: keep (many boards omit dates); freshness applied when known
        return True
    return age <= freshness_days


def _has_exclude(job: JobRecord, exclude_words: list[str]) -> bool:
    return any_match(f"{job.title} {job.description}", exclude_words)


def is_senior_title(job: JobRecord) -> bool:
    return bool(SENIOR_TITLE.search(job.title or ""))


def german_requirement_too_high(job: JobRecord, german_max: str) -> bool:
    """Public alias used by ranking penalties."""
    return _german_too_high(job, german_max)


def _german_too_high(job: JobRecord, german_max: str) -> bool:
    blob = f"{job.title} {job.description}"
    # Explicitly OK → keep
    if any(p.search(blob) for p in GERMAN_OK_PATTERNS):
        return False
    max_level = CEFR_ORDER.get(german_max.upper(), 2)
    for p in GERMAN_HIGH_PATTERNS:
        m = p.search(blob)
        if not m:
            continue
        # If pattern captured a level group, compare
        level = None
        for g in m.groups() or ():
            if g and g.upper() in CEFR_ORDER:
                level = g.upper()
                break
        if level is None:
            # qualitative high bar (verhandlungssicher / fließend)
            return True
        if CEFR_ORDER[level] > max_level:
            return True
    return False


def _has_must_have(job: JobRecord, must_have_any: list[str]) -> bool:
    if not must_have_any:
        return True
    return any_match(f"{job.title} {job.description}", must_have_any)


def _thin_description(job: JobRecord, min_chars: int) -> bool:
    """Listing-page links with no real text cannot be judged — drop them.

    Applies only to custom sites, where we scrape link text rather than a
    posting body. Board APIs sometimes legitimately return short descriptions.
    """
    if not job.source.startswith("custom:"):
        return False
    return len(job.description or "") < min_chars


def filter_jobs(jobs: list[JobRecord], cfg: dict[str, Any]) -> list[JobRecord]:
    role_types = cfg.get("role_types") or []
    domains = cfg.get("domains") or []
    locations = cfg.get("locations") or []
    freshness = int(num(cfg, "freshness_days", 7))
    exclude = cfg.get("exclude_words") or []
    must_have = cfg.get("must_have_any") or []
    german_max = cfg.get("german_max_level") or "A2"
    min_desc = int(num(cfg, "min_description_chars", 0))

    counts = {
        "input": len(jobs),
        "senior": 0,
        "role": 0,
        "domain": 0,
        "location": 0,
        "freshness": 0,
        "exclude": 0,
        "german": 0,
        "must_have": 0,
        "thin": 0,
        "passed": 0,
    }
    kept: list[JobRecord] = []

    for job in jobs:
        if is_senior_title(job):
            counts["senior"] += 1
            continue
        if not _has_role(job, role_types):
            counts["role"] += 1
            continue
        if not _has_domain(job, domains):
            counts["domain"] += 1
            continue
        if not _location_ok(job, locations):
            counts["location"] += 1
            continue
        if not _fresh_enough(job, freshness):
            counts["freshness"] += 1
            continue
        if _has_exclude(job, exclude):
            counts["exclude"] += 1
            continue
        if _german_too_high(job, german_max):
            counts["german"] += 1
            continue
        if not _has_must_have(job, must_have):
            counts["must_have"] += 1
            continue
        if _thin_description(job, min_desc):
            counts["thin"] += 1
            continue
        kept.append(job)
        counts["passed"] += 1

    log.info(
        "Filter drop-off — input=%s | fail senior=%s role=%s domain=%s location=%s "
        "freshness=%s exclude=%s german=%s must_have=%s thin=%s | passed=%s",
        counts["input"],
        counts["senior"],
        counts["role"],
        counts["domain"],
        counts["location"],
        counts["freshness"],
        counts["exclude"],
        counts["german"],
        counts["must_have"],
        counts["thin"],
        counts["passed"],
    )
    return kept


__all__ = [
    "filter_jobs",
    "german_requirement_too_high",
    "is_senior_title",
    "days_since_posted",
]
