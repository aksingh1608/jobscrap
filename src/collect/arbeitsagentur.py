"""Arbeitsagentur Jobsuche API collector (official German employment agency).

Auth header: X-API-Key: jobboerse-jobsuche
Docs: https://github.com/bundesAPI/jobsuche-api

Notes:
- Search works on /pc/v6/jobs (v4 search currently returns 403).
- Job details work on /pc/v4/jobdetails/{base64(refnr)}.
- angebotsart=34 filters Praktikum/Trainee when useful.
"""

from __future__ import annotations

import base64
import logging
import time
from typing import Any, Optional
from urllib.parse import urlencode

from src.http_util import get_json
from src.models import JobRecord

log = logging.getLogger(__name__)

BASE = "https://rest.arbeitsagentur.de/jobboerse/jobsuche-service"
SEARCH_URL = f"{BASE}/pc/v6/jobs"
DETAIL_URL = f"{BASE}/pc/v4/jobdetails"
API_HEADERS = {
    "X-API-Key": "jobboerse-jobsuche",
    "Accept": "application/json",
}


def _encode_refnr(refnr: str) -> str:
    return base64.b64encode(refnr.encode("utf-8")).decode("ascii")


def _fetch_detail(refnr: str) -> Optional[dict[str, Any]]:
    try:
        encoded = _encode_refnr(refnr)
        return get_json(f"{DETAIL_URL}/{encoded}", headers=API_HEADERS, timeout=40)
    except Exception:
        log.debug("Detail fetch failed for %s", refnr, exc_info=True)
        return None


def _location_from_item(item: dict[str, Any], fallback: str) -> str:
    locs = item.get("stellenlokationen") or []
    if locs:
        addr = (locs[0] or {}).get("adresse") or {}
        ort = addr.get("ort") or ""
        region = addr.get("region") or ""
        land = addr.get("land") or ""
        parts = [p for p in (ort, region, land) if p]
        if parts:
            return ", ".join(parts)
    ort = item.get("arbeitsort") or item.get("ort") or {}
    if isinstance(ort, dict):
        return ort.get("ort") or fallback
    return str(ort or fallback)


def _posted_from_item(item: dict[str, Any], detail: Optional[dict[str, Any]]) -> Optional[str]:
    for src in (detail or {}, item):
        for key in (
            "datumErsteVeroeffentlichung",
            "aktuelleVeroeffentlichungsdatum",
            "aenderungsdatum",
        ):
            val = src.get(key)
            if val:
                return str(val)[:10]
        vz = src.get("veroeffentlichungszeitraum") or {}
        if isinstance(vz, dict) and vz.get("von"):
            return str(vz["von"])[:10]
    return None


def collect_arbeitsagentur(cfg: dict[str, Any]) -> list[JobRecord]:
    location = cfg.get("search_location") or "Berlin"
    radius = int(cfg.get("radius_km") or 200)
    freshness = int(cfg.get("freshness_days") or 7)
    queries = cfg.get("search_queries") or ["Machine Learning"]
    # German role keywords for better coverage on this board
    de_extra = [
        "Praktikum Machine Learning",
        "Praktikum KI",
        "Werkstudent KI",
        "Praktikant Data Science",
        "HiWi KI",
        "Studentische Hilfskraft Machine Learning",
        "Praktikum Data Science",
        "Werkstudent Machine Learning",
    ]
    all_queries = list(dict.fromkeys(list(queries) + de_extra))

    jobs: list[JobRecord] = []
    seen_refs: set[str] = set()
    max_raw = 200

    for q in all_queries:
        if len(jobs) >= max_raw:
            break
        page = 1
        while page <= 4 and len(jobs) < max_raw:
            params: dict[str, Any] = {
                "was": q,
                "wo": location,
                "umkreis": radius,
                "page": page,
                "size": 25,
                "pav": "false",
                "veroeffentlichtseit": freshness,
            }
            # 34 = Praktikum/Trainee — keep as soft preference; also search without it
            if page == 1 and "praktikum" in q.lower():
                params["angebotsart"] = 34

            try:
                log.info("Arbeitsagentur search: %s", urlencode(params))
                data = get_json(SEARCH_URL, headers=API_HEADERS, params=params)
            except Exception:
                log.exception("Arbeitsagentur search failed page=%s q=%r", page, q)
                break

            stellen = (
                data.get("ergebnisliste")
                or data.get("stellenangebote")
                or data.get("jobs")
                or []
            )
            if not stellen:
                break

            for item in stellen:
                refnr = (
                    item.get("referenznummer")
                    or item.get("refnr")
                    or item.get("hashId")
                    or ""
                )
                if not refnr or refnr in seen_refs:
                    continue
                seen_refs.add(str(refnr))

                title = (
                    item.get("stellenangebotsTitel")
                    or item.get("titel")
                    or item.get("beruf")
                    or item.get("hauptberuf")
                    or ""
                )
                company = item.get("firma") or item.get("arbeitgeber") or ""
                if isinstance(company, dict):
                    company = company.get("name") or ""
                loc = _location_from_item(item, location)

                detail = _fetch_detail(str(refnr))
                time.sleep(0.4)  # keep request rate low

                description = ""
                url = ""
                if detail:
                    description = (
                        detail.get("stellenangebotsBeschreibung")
                        or detail.get("stellenbeschreibung")
                        or detail.get("jobBeschreibung")
                        or detail.get("beschreibung")
                        or ""
                    )
                    if isinstance(description, dict):
                        description = description.get("text") or str(description)
                    url = (
                        detail.get("externeUrl")
                        or detail.get("url")
                        or ""
                    )
                    if not company:
                        company = detail.get("firma") or detail.get("arbeitgeber") or company
                    if isinstance(company, dict):
                        company = company.get("name") or ""
                    if not title:
                        title = detail.get("stellenangebotsTitel") or title

                posted = _posted_from_item(item, detail)

                if not url:
                    url = (
                        "https://www.arbeitsagentur.de/jobsuche/jobdetail/"
                        f"{refnr}"
                    )

                jobs.append(
                    JobRecord(
                        title=str(title).strip(),
                        company=str(company).strip(),
                        location=str(loc).strip(),
                        source="arbeitsagentur",
                        url=url,
                        description=str(description),
                        posted_date=posted,
                        raw={"refnr": refnr, "item": item},
                    )
                )

            page += 1
            time.sleep(1.0)

    log.info("Arbeitsagentur collected %s jobs", len(jobs))
    return jobs
