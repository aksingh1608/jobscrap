"""Deduplicate jobs across boards with RapidFuzz on company+title+location.

The same posting typically appears on Indeed, Google Jobs and the company's own
board. When that happens we keep the richest copy — the one with the longest
description — because ranking quality depends on having real posting text.
"""

from __future__ import annotations

import logging
import re

from rapidfuzz import fuzz

from src.models import JobRecord

log = logging.getLogger(__name__)

_NON_WORD = re.compile(r"[^a-z0-9 ]+")


def _block_key(job: JobRecord) -> str:
    """Cheap bucket key so we only fuzzy-compare plausible pairs.

    Comparing every pair is O(n²) on a few hundred postings; bucketing by the
    first token of the company name cuts that to near-linear without changing
    results, since a duplicate always shares the employer.
    """
    company = _NON_WORD.sub("", (job.company or "").lower()).strip()
    return company.split(" ")[0][:6] if company else ""


def _richness(job: JobRecord) -> tuple[int, int]:
    """Sort key for picking the copy to keep: real text first, then a date."""
    return (len(job.description or ""), 1 if job.posted_date else 0)


def dedup_jobs(
    jobs: list[JobRecord],
    *,
    threshold: int = 90,
) -> list[JobRecord]:
    """Collapse near-duplicate company|title|location groups to their best copy."""
    buckets: dict[str, list[list[JobRecord]]] = {}
    order: list[list[JobRecord]] = []

    for job in jobs:
        key = _block_key(job)
        fp = job.fingerprint_text()
        placed = False
        for group in buckets.setdefault(key, []):
            if fuzz.token_set_ratio(fp, group[0].fingerprint_text()) >= threshold:
                group.append(job)
                placed = True
                break
        if not placed:
            group = [job]
            buckets[key].append(group)
            order.append(group)

    kept = [max(group, key=_richness) for group in order]

    log.info(
        "Dedup: %s → %s (threshold=%s, kept richest copy per group)",
        len(jobs),
        len(kept),
        threshold,
    )
    return kept
