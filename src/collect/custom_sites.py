"""Custom site collector via Crawl4AI (with BeautifulSoup fallback).

Add/remove sites in config.yaml under sources.custom_sites — no code changes.
Each entry may be a URL string or:
  {url, name, enabled, selectors: {job_link, title, company, location}}
"""

from __future__ import annotations

import logging
import re
import time
from typing import Any, Optional
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from src.config import num
from src.http_util import get_text
from src.models import JobRecord

log = logging.getLogger(__name__)

# Heuristic patterns for internship / student roles in listing text
ROLE_HINT = re.compile(
    r"(internship|praktikum|praktikant|werkstudent|working\s*student|"
    r"research\s*assistant|hilfskraft|hiwi|\bshk\b|student\s*job|"
    r"trainee|werkvertrag)",
    re.I,
)
DOMAIN_HINT = re.compile(
    r"(machine\s*learning|deep\s*learning|data\s*science|computer\s*vision|"
    r"\bnlp\b|\bllm\b|generative\s*ai|mlops|devops|artificial\s*intelligence|"
    r"\bki\b|\bai\b|data\s*analys|data\s*engineer)",
    re.I,
)


def _crawl_markdown(url: str) -> Optional[str]:
    """Prefer Crawl4AI for JS-heavy boards; return markdown or None."""
    try:
        import asyncio
        from crawl4ai import AsyncWebCrawler

        async def _run() -> str:
            async with AsyncWebCrawler(verbose=False) as crawler:
                result = await crawler.arun(url=url)
                if result and result.success:
                    return result.markdown or result.cleaned_html or ""
            return ""

        return asyncio.run(_run())
    except Exception:
        log.debug("Crawl4AI unavailable or failed for %s", url, exc_info=True)
        return None


def _parse_links_from_html(
    html: str,
    base_url: str,
    selectors: dict[str, str],
) -> list[dict[str, str]]:
    soup = BeautifulSoup(html, "lxml")
    items: list[dict[str, str]] = []
    link_sel = selectors.get("job_link") or "a[href]"

    for a in soup.select(link_sel):
        href = a.get("href") or ""
        text = a.get_text(" ", strip=True)
        if not href or not text or len(text) < 8:
            continue
        full = urljoin(base_url, href)
        # Skip pure navigation / anchors
        if full.startswith("mailto:") or full.startswith("javascript:"):
            continue
        path = urlparse(full).path.lower()
        blob = f"{text} {path}"
        if not (ROLE_HINT.search(blob) or DOMAIN_HINT.search(blob) or "/job" in path):
            # Keep if parent/card nearby mentions roles
            parent = a.find_parent(["article", "li", "div", "tr"])
            parent_text = parent.get_text(" ", strip=True) if parent else ""
            if not (ROLE_HINT.search(parent_text) or DOMAIN_HINT.search(parent_text)):
                continue
            text = text if len(text) > 20 else (parent_text[:200] or text)

        company = ""
        location = ""
        if selectors.get("company") and a.find_parent():
            c_el = a.find_parent().select_one(selectors["company"])
            if c_el:
                company = c_el.get_text(" ", strip=True)
        if selectors.get("location") and a.find_parent():
            l_el = a.find_parent().select_one(selectors["location"])
            if l_el:
                location = l_el.get_text(" ", strip=True)

        items.append(
            {
                "title": text[:200],
                "url": full,
                "company": company,
                "location": location,
                "snippet": text[:500],
            }
        )
    return items


def _parse_markdown_listings(md: str, base_url: str) -> list[dict[str, str]]:
    """Extract markdown links that look like job postings."""
    items: list[dict[str, str]] = []
    # [title](url)
    for m in re.finditer(r"\[([^\]]{8,180})\]\((https?://[^)\s]+)\)", md):
        title, url = m.group(1).strip(), m.group(2).strip()
        blob = f"{title} {url}"
        if ROLE_HINT.search(blob) or DOMAIN_HINT.search(blob) or "/job" in url.lower():
            items.append(
                {
                    "title": title,
                    "url": url,
                    "company": "",
                    "location": "",
                    "snippet": title,
                }
            )
    # Also relative links in HTML leftovers
    for m in re.finditer(r"\[([^\]]{8,180})\]\((/[^)\s]+)\)", md):
        title, path = m.group(1).strip(), m.group(2).strip()
        url = urljoin(base_url, path)
        blob = f"{title} {url}"
        if ROLE_HINT.search(blob) or DOMAIN_HINT.search(blob) or "/job" in path.lower():
            items.append(
                {
                    "title": title,
                    "url": url,
                    "company": "",
                    "location": "",
                    "snippet": title,
                }
            )
    return items


def _scrape_one_site(site: dict[str, Any]) -> list[JobRecord]:
    url = site["url"]
    name = site.get("name") or urlparse(url).netloc
    selectors = site.get("selectors") or {}
    log.info("Custom site scrape: %s (%s)", name, url)

    records: list[JobRecord] = []
    listings: list[dict[str, str]] = []

    md = _crawl_markdown(url)
    if md:
        listings = _parse_markdown_listings(md, url)

    if not listings:
        # Let this propagate: the caller counts consecutive failures so a dead
        # network stops the whole source instead of retrying every site.
        html = get_text(url, timeout=40)
        listings = _parse_links_from_html(html, url, selectors)

    # Dedup by URL within site
    seen: set[str] = set()
    for item in listings:
        job_url = item["url"]
        if job_url in seen or job_url.rstrip("/") == url.rstrip("/"):
            continue
        seen.add(job_url)
        records.append(
            JobRecord(
                title=item["title"],
                company=item.get("company") or name,
                location=item.get("location") or "Germany",
                source=f"custom:{name}",
                url=job_url,
                description=item.get("snippet") or item["title"],
            )
        )

    log.info("Custom site %s → %s candidate links", name, len(records))
    return records


def collect_custom_sites(
    cfg: dict[str, Any],
    sites: list[dict[str, Any]],
) -> list[JobRecord]:
    all_jobs: list[JobRecord] = []
    failures = 0
    deadline = time.monotonic() + num(cfg, "custom_sites_budget_s", 900)

    for site in sites:
        if time.monotonic() > deadline:
            log.warning(
                "Custom site budget exhausted — skipping the remaining site(s)"
            )
            break
        # If nothing is reachable, the network is the problem, not the sites.
        if failures >= 5:
            log.error("5 custom sites failed in a row — skipping the rest this run")
            break
        try:
            batch = _scrape_one_site(site)
            all_jobs.extend(batch)
            failures = 0
        except Exception as exc:
            log.warning("Failed custom site %s: %s", site.get("url"), exc)
            failures += 1
        time.sleep(2.0)  # polite delay between sites

    log.info("Custom sites total: %s", len(all_jobs))
    return all_jobs
