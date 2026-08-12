"""End-to-end pipeline: collect → normalize → dedup → filter → rank → store → export."""

from __future__ import annotations

import logging
import sys
import time
import traceback
from pathlib import Path
from typing import Any

from src.collect import collect_all
from src.config import load_config
from src.dedup import dedup_jobs
from src.export import export_excel, prune_exports
from src.filter import filter_jobs
from src.models import now_iso
from src.normalize import normalize_jobs
from src.notify import notify_failure, notify_run
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


def run_pipeline(
    config_path: str | None = None,
    *,
    notify: bool = True,
    overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    cfg = load_config(config_path)
    cfg.update(overrides or {})
    setup_logging(cfg["paths"].get("log_file"))

    started = time.time()
    log.info("=== Pipeline start ===")

    try:
        return _run(cfg, notify=notify, started=started)
    except Exception as exc:  # noqa: BLE001 — a scheduled run must report why it died
        log.exception("Pipeline failed")
        if notify:
            notify_failure(cfg, f"{type(exc).__name__}: {exc}\n{traceback.format_exc()[-800:]}")
        raise


def _run(cfg: dict[str, Any], *, notify: bool, started: float) -> dict[str, Any]:
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

    # Retire postings that have dropped off every board, then bound history.
    retention = cfg.get("retention") or {}
    expired = store.expire_stale(int(retention.get("expire_days", 21)))
    pruned = store.prune(int(retention.get("db_keep_days", 180)))

    store.set_meta("last_run", now_iso())

    active = store.active_for_export()
    excel_path = export_excel(active, cfg["paths"]["exports_dir"])
    prune_exports(cfg["paths"]["exports_dir"], int(retention.get("keep_exports", 30)))

    new_today = store.count_new_today()
    total_open = store.count_open()

    if notify:
        notify_run(
            cfg,
            new_count=new_today,
            total_open=total_open,
            excel_path=str(excel_path),
            top_jobs=active,
        )
    else:
        log.info("Notifications disabled for this run")

    summary = {
        "raw": len(raw),
        "normalized": len(normalized),
        "deduped": len(unique),
        "filtered": len(filtered),
        "ranked": len(ranked),
        "upsert": upsert_stats,
        "expired": expired,
        "pruned": pruned,
        "new_today": new_today,
        "total_open": total_open,
        "excel": str(excel_path),
        "duration_s": round(time.time() - started, 1),
        "last_run": store.get_meta("last_run"),
    }
    log.info("=== Pipeline done: %s ===", summary)

    if not ranked:
        log.warning(
            "No job cleared min_fit_score=%s today. Lower it in config.yaml or "
            "widen search_queries if this repeats.",
            cfg.get("min_fit_score"),
        )

    print(f"\nExcel ready: {excel_path}\nOpen that file — no dashboard needed.")
    return summary
