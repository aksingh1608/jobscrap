"""JobSpy collector — Indeed, Glassdoor, Google Jobs (no LinkedIn)."""

from __future__ import annotations

import logging
import re
import time
from typing import Any

from src.config import num
from src.models import JobRecord

log = logging.getLogger(__name__)

# Never scrape LinkedIn via JobSpy — user handles it manually.
BLOCKED_SITES = {"linkedin"}

INTERNSHIP_QUERY = re.compile(r"\b(internship|intern|praktikum|praktikant\w*)\b", re.I)


def collect_jobspy(cfg: dict[str, Any], site_names: list[str]) -> list[JobRecord]:
    try:
        from jobspy import scrape_jobs
    except ImportError as e:
        log.error("python-jobspy not installed: %s", e)
        return []

    sites = [s for s in site_names if s.lower() not in BLOCKED_SITES]
    if not sites:
        log.warning("No JobSpy sites enabled after filtering LinkedIn")
        return []

    location = cfg.get("search_location") or "Berlin"
    freshness_days = int(num(cfg, "freshness_days", 7))
    hours_old = freshness_days * 24
    queries = cfg.get("search_queries") or ["AI internship"]
    max_raw = int(num(cfg, "max_raw_per_source", 300))
    results_per_query = max(20, max_raw // max(len(queries), 1))

    all_jobs: list[JobRecord] = []
    seen_urls: set[str] = set()

    for q in queries:
        # job_type="internship" is only right for internship-shaped queries.
        # Forcing it on a Werkstudent/HiWi search drops most real results,
        # because those are part-time contracts on the boards.
        wants_internship = INTERNSHIP_QUERY.search(q) is not None
        attempts: list[dict[str, Any]] = []
        if wants_internship:
            attempts.append({"job_type": "internship"})
        attempts.append({})  # unrestricted retry

        df = None
        for extra in attempts:
            try:
                log.info(
                    "JobSpy: query=%r sites=%s location=%s extra=%s",
                    q,
                    sites,
                    location,
                    extra,
                )
                df = scrape_jobs(
                    site_name=sites,
                    search_term=q,
                    location=location,
                    results_wanted=min(results_per_query, 50),
                    hours_old=hours_old,
                    country_indeed="germany",
                    description_format="markdown",
                    verbose=0,
                    **extra,
                )
                if df is not None and not df.empty:
                    break
            except Exception:
                log.warning("JobSpy attempt failed for query=%r extra=%s", q, extra)
                df = None
        if df is None or df.empty:
            log.info("JobSpy: 0 results for %r", q)
            time.sleep(2)
            continue

        for _, row in df.iterrows():
            url = str(row.get("job_url") or row.get("job_url_direct") or "").strip()
            if not url or url in seen_urls:
                continue
            seen_urls.add(url)
            title = str(row.get("title") or "").strip()
            company = str(row.get("company") or "").strip()
            loc_parts = [
                str(row.get("city") or "").strip(),
                str(row.get("state") or "").strip(),
                str(row.get("country") or "").strip(),
            ]
            loc = ", ".join(p for p in loc_parts if p and p.lower() != "nan")
            if not loc:
                loc = str(row.get("location") or location)
            desc = str(row.get("description") or "")
            posted = row.get("date_posted")
            posted_str = None
            if posted is not None and str(posted) not in ("", "NaT", "nan", "None"):
                posted_str = str(posted)[:10]
            source = f"jobspy:{row.get('site') or 'unknown'}"
            all_jobs.append(
                JobRecord(
                    title=title,
                    company=company,
                    location=loc,
                    source=source,
                    url=url,
                    description=desc,
                    posted_date=posted_str,
                    raw=row.to_dict() if hasattr(row, "to_dict") else {},
                )
            )

        # Be polite between queries
        time.sleep(3)

    log.info("JobSpy collected %s unique jobs", len(all_jobs))
    return all_jobs
