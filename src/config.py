"""Load and validate config.yaml."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG_PATH = ROOT / "config.yaml"


def load_config(path: str | Path | None = None) -> dict[str, Any]:
    cfg_path = Path(path) if path else DEFAULT_CONFIG_PATH
    if not cfg_path.is_absolute():
        cfg_path = ROOT / cfg_path
    with open(cfg_path, encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}

    # Defaults so partial configs still work
    cfg.setdefault("role_types", [])
    cfg.setdefault("domains", [])
    cfg.setdefault("must_have_any", [])
    cfg.setdefault("exclude_words", [])
    cfg.setdefault("german_max_level", "A2")
    cfg.setdefault("prefer_english", True)
    cfg.setdefault("locations", ["Germany", "Remote"])
    cfg.setdefault("radius_km", 200)
    cfg.setdefault("freshness_days", 7)
    cfg.setdefault("min_fit_score", 45)
    cfg.setdefault("max_per_day", 50)
    cfg.setdefault("search_location", "Berlin")
    cfg.setdefault("search_queries", ["AI internship"])
    cfg.setdefault("sources", {})
    cfg["sources"].setdefault("jobspy", ["indeed", "glassdoor", "google"])
    cfg["sources"].setdefault("arbeitsagentur", True)
    cfg["sources"].setdefault("custom_sites", [])
    cfg.setdefault("profile_text", "")
    cfg.setdefault(
        "scoring",
        {
            "embedding_weight": 100,
            "english_boost": 5,
            "must_have_boost": 3,
            "must_have_boost_cap": 12,
            "german_penalty": 20,
            "exclude_penalty": 15,
        },
    )
    cfg.setdefault(
        "paths",
        {
            "sqlite": "data/jobs.db",
            "exports_dir": "exports",
            "log_file": "data/pipeline.log",
        },
    )
    cfg.setdefault("notify", {"telegram": True, "email": False})

    # Resolve relative paths against project root
    for key in ("sqlite", "exports_dir", "log_file"):
        p = Path(cfg["paths"][key])
        if not p.is_absolute():
            cfg["paths"][key] = str(ROOT / p)

    return cfg


def resolve_custom_sites(cfg: dict[str, Any]) -> list[dict[str, Any]]:
    """Normalize custom_sites entries into dicts with url/name/enabled."""
    sites: list[dict[str, Any]] = []
    for item in cfg.get("sources", {}).get("custom_sites") or []:
        if isinstance(item, str):
            sites.append({"url": item, "name": item, "enabled": True})
        elif isinstance(item, dict) and item.get("url"):
            sites.append(
                {
                    "url": item["url"],
                    "name": item.get("name") or item["url"],
                    "enabled": item.get("enabled", True),
                    "selectors": item.get("selectors") or {},
                }
            )
    return [s for s in sites if s.get("enabled", True)]
