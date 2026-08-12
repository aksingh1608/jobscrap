"""Deduplicate jobs across boards with RapidFuzz on company+title+location."""

from __future__ import annotations

import logging

from rapidfuzz import fuzz

from src.models import JobRecord

log = logging.getLogger(__name__)


def dedup_jobs(
    jobs: list[JobRecord],
    *,
    threshold: int = 90,
) -> list[JobRecord]:
    """Keep first occurrence when company|title|location is near-duplicate."""
    kept: list[JobRecord] = []
    fps: list[str] = []

    for job in jobs:
        fp = job.fingerprint_text()
        is_dup = False
        for existing in fps:
            # token_set_ratio handles word-order differences well
            if fuzz.token_set_ratio(fp, existing) >= threshold:
                is_dup = True
                break
        if is_dup:
            continue
        kept.append(job)
        fps.append(fp)

    log.info("Dedup: %s → %s (threshold=%s)", len(jobs), len(kept), threshold)
    return kept
