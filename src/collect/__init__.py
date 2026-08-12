"""Collect package — pull raw jobs from all configured sources."""

from __future__ import annotations

import logging
import time
from typing import Any

from src.collect.arbeitsagentur import collect_arbeitsagentur
from src.collect.custom_sites import collect_custom_sites
from src.collect.jobspy_source import collect_jobspy
from src.config import num, resolve_custom_sites
from src.models import JobRecord

log = logging.getLogger(__name__)


def collect_all(cfg: dict[str, Any]) -> list[JobRecord]:
    """Fetch wide from every enabled source. Target ~200–400 raw postings.

    One broken source must never sink the run: each is wrapped independently,
    and a whole-stage deadline guarantees the pipeline still ranks and exports
    whatever it managed to gather.
    """
    jobs: list[JobRecord] = []
    stats: dict[str, int] = {}
    started = time.monotonic()
    budget_s = num(cfg, "collect_budget_s", 2400)

    def _budget_left(source: str) -> bool:
        if time.monotonic() - started < budget_s:
            return True
        log.warning("Collection budget (%ss) spent — skipping %s", budget_s, source)
        return False

    jobspy_sites = cfg.get("sources", {}).get("jobspy") or []
    if jobspy_sites and _budget_left("jobspy"):
        try:
            batch = collect_jobspy(cfg, site_names=jobspy_sites)
            stats["jobspy"] = len(batch)
            jobs.extend(batch)
            log.info("JobSpy returned %s jobs", len(batch))
        except Exception:
            log.exception("JobSpy collection failed")
            stats["jobspy"] = 0

    if cfg.get("sources", {}).get("arbeitsagentur") and _budget_left("arbeitsagentur"):
        try:
            batch = collect_arbeitsagentur(cfg)
            stats["arbeitsagentur"] = len(batch)
            jobs.extend(batch)
            log.info("Arbeitsagentur returned %s jobs", len(batch))
        except Exception:
            log.exception("Arbeitsagentur collection failed")
            stats["arbeitsagentur"] = 0

    custom = resolve_custom_sites(cfg)
    if custom and _budget_left("custom sites"):
        try:
            batch = collect_custom_sites(cfg, custom)
            stats["custom_sites"] = len(batch)
            jobs.extend(batch)
            log.info("Custom sites returned %s jobs", len(batch))
        except Exception:
            log.exception("Custom site collection failed")
            stats["custom_sites"] = 0

    elapsed = round(time.monotonic() - started, 1)
    log.info(
        "Collection totals by source: %s | grand total raw=%s | %ss",
        stats,
        len(jobs),
        elapsed,
    )
    if not jobs:
        log.error(
            "Every source returned 0 jobs — check network access and the logs "
            "above before assuming the market is quiet"
        )
    return jobs
