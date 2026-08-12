"""Hard filters: role type, domain, location, freshness, exclude words, German level."""

from __future__ import annotations

import logging
import re
from datetime import date, datetime, timedelta
from typing import Any, Optional

from src.models import JobRecord

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


def _has_role(job: JobRecord, role_types: list[str]) -> bool:
    if job.role_type:
        return True
    blob = f"{job.title} {job.description}".lower()
    return any(rt.lower() in blob for rt in role_types)


def _has_domain(job: JobRecord, domains: list[str]) -> bool:
    blob = f"{job.title} {job.description}".lower()
    return any(d.lower() in blob for d in domains)


def _location_ok(job: JobRecord, locations: list[str]) -> bool:
    loc = (job.location or "").lower()
    blob = f"{loc} {job.description}".lower()
    # Remote open to Germany
    if "remote" in blob and (
        "germany" in blob
        or "deutschland" in blob
        or "eu" in blob
        or "europe" in blob
        or not re.search(r"\b(us|usa|uk only|india only)\b", blob)
    ):
        return True
    for allowed in locations:
        if allowed.lower() in blob:
            return True
    # Default: many DE postings omit country — accept if source is DE-focused
    if job.source.startswith("arbeitsagentur") or job.source.startswith("custom:"):
        return True
    if "jobspy" in job.source and ("germany" in blob or "deutschland" in blob or loc):
        return True
    return False


def _fresh_enough(job: JobRecord, freshness_days: int) -> bool:
    posted = _parse_date(job.posted_date)
    if posted is None:
        # Unknown date: keep (many boards omit dates); freshness applied when known
        return True
    cutoff = date.today() - timedelta(days=freshness_days)
    return posted >= cutoff


def _has_exclude(job: JobRecord, exclude_words: list[str]) -> bool:
    blob = f"{job.title} {job.description}".lower()
    for w in exclude_words:
        if w.lower() in blob:
            return True
    return False


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
    blob = f"{job.title} {job.description}".lower()
    return any(m.lower() in blob for m in must_have_any)


def filter_jobs(jobs: list[JobRecord], cfg: dict[str, Any]) -> list[JobRecord]:
    role_types = cfg.get("role_types") or []
    domains = cfg.get("domains") or []
    locations = cfg.get("locations") or []
    freshness = int(cfg.get("freshness_days") or 7)
    exclude = cfg.get("exclude_words") or []
    must_have = cfg.get("must_have_any") or []
    german_max = cfg.get("german_max_level") or "A2"

    counts = {
        "input": len(jobs),
        "role": 0,
        "domain": 0,
        "location": 0,
        "freshness": 0,
        "exclude": 0,
        "german": 0,
        "must_have": 0,
        "passed": 0,
    }
    kept: list[JobRecord] = []

    for job in jobs:
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
        kept.append(job)
        counts["passed"] += 1

    log.info(
        "Filter drop-off — input=%s | fail role=%s domain=%s location=%s "
        "freshness=%s exclude=%s german=%s must_have=%s | passed=%s",
        counts["input"],
        counts["role"],
        counts["domain"],
        counts["location"],
        counts["freshness"],
        counts["exclude"],
        counts["german"],
        counts["must_have"],
        counts["passed"],
    )
    return kept
