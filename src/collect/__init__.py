"""Collect package — pull raw jobs from all configured sources."""

from __future__ import annotations

import logging
from typing import Any

from src.collect.arbeitsagentur import collect_arbeitsagentur
from src.collect.custom_sites import collect_custom_sites
from src.collect.jobspy_source import collect_jobspy
from src.config import resolve_custom_sites
from src.models import JobRecord

log = logging.getLogger(__name__)


def collect_all(cfg: dict[str, Any]) -> list[JobRecord]:
    """Fetch wide from every enabled source. Target ~200–400 raw postings."""
    jobs: list[JobRecord] = []
    stats: dict[str, int] = {}

    jobspy_sites = cfg.get("sources", {}).get("jobspy") or []
    if jobspy_sites:
        try:
            batch = collect_jobspy(cfg, site_names=jobspy_sites)
            stats["jobspy"] = len(batch)
            jobs.extend(batch)
            log.info("JobSpy returned %s jobs", len(batch))
        except Exception:
            log.exception("JobSpy collection failed")
            stats["jobspy"] = 0

    if cfg.get("sources", {}).get("arbeitsagentur"):
        try:
            batch = collect_arbeitsagentur(cfg)
            stats["arbeitsagentur"] = len(batch)
            jobs.extend(batch)
            log.info("Arbeitsagentur returned %s jobs", len(batch))
        except Exception:
            log.exception("Arbeitsagentur collection failed")
            stats["arbeitsagentur"] = 0

    custom = resolve_custom_sites(cfg)
    if custom:
        try:
            batch = collect_custom_sites(cfg, custom)
            stats["custom_sites"] = len(batch)
            jobs.extend(batch)
            log.info("Custom sites returned %s jobs", len(batch))
        except Exception:
            log.exception("Custom site collection failed")
            stats["custom_sites"] = 0

    log.info(
        "Collection totals by source: %s | grand total raw=%s",
        stats,
        len(jobs),
    )
    return jobs
