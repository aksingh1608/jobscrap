"""End-to-end pipeline: collect → normalize → dedup → filter → rank → store → export."""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Any

from src.collect import collect_all
from src.config import load_config
from src.dedup import dedup_jobs
from src.export import export_excel
from src.filter import filter_jobs
from src.models import now_iso
from src.normalize import normalize_jobs
from src.notify import notify_run
from src.rank import rank_jobs
from src.store import JobStore

log = logging.getLogger(__name__)


def setup_logging(log_file: str | None = None) -> None:
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stdout)]
    if log_file:
        Path(log_file).parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(log_file, encoding="utf-8"))
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=handlers,
        force=True,
    )


def run_pipeline(config_path: str | None = None) -> dict[str, Any]:
    cfg = load_config(config_path)
    setup_logging(cfg["paths"].get("log_file"))

    log.info("=== Pipeline start ===")

    raw = collect_all(cfg)
    log.info("Stage collect: %s raw jobs", len(raw))

    normalized = normalize_jobs(raw, cfg)
    log.info("Stage normalize: %s", len(normalized))

    unique = dedup_jobs(normalized)
    log.info("Stage dedup: %s", len(unique))

    filtered = filter_jobs(unique, cfg)
    log.info("Stage filter: %s", len(filtered))

    ranked = rank_jobs(filtered, cfg)
    log.info("Stage rank/final: %s", len(ranked))

    store = JobStore(cfg["paths"]["sqlite"])
    upsert_stats = store.upsert_ranked(ranked)
    store.set_meta("last_run", now_iso())

    active = store.active_for_export()
    excel_path = export_excel(active, cfg["paths"]["exports_dir"])

    new_today = store.count_new_today()
    total_open = store.count_open()
    notify_run(
        cfg,
        new_count=new_today,
        total_open=total_open,
        excel_path=str(excel_path),
    )

    summary = {
        "raw": len(raw),
        "normalized": len(normalized),
        "deduped": len(unique),
        "filtered": len(filtered),
        "ranked": len(ranked),
        "upsert": upsert_stats,
        "new_today": new_today,
        "total_open": total_open,
        "excel": str(excel_path),
        "last_run": store.get_meta("last_run"),
    }
    log.info("=== Pipeline done: %s ===", summary)
    print(f"\nExcel ready: {excel_path}\nOpen that file — no dashboard needed.")
    return summary
