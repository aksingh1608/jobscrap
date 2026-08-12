#!/usr/bin/env python3
"""Re-apply the current filters and scoring to jobs already in the database.

Useful after tightening `config.yaml`: rows admitted by the old rules keep
their old score and would otherwise sit in the sheet until they expire. This
dismisses whatever no longer passes and refreshes the score on the rest.

    python scripts/recheck_store.py --dry-run   # show what would change
    python scripts/recheck_store.py             # apply
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.config import load_config
from src.export import export_excel
from src.filter import filter_jobs
from src.pipeline import setup_logging
from src.rank import rank_jobs
from src.store import JobStore


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("-c", "--config", default=None)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would change without touching the database",
    )
    parser.add_argument(
        "--no-export",
        action="store_true",
        help="Skip rewriting today's Excel file afterwards",
    )
    args = parser.parse_args()

    cfg = load_config(args.config)
    setup_logging(cfg["paths"].get("log_file"))

    store = JobStore(cfg["paths"]["sqlite"])
    open_jobs = store.active_for_export()
    if not open_jobs:
        print("Nothing open to re-check.")
        return 0

    passed = filter_jobs(open_jobs, cfg)
    # Re-score everything that still passes, then apply the same floor the daily
    # run uses — otherwise rows admitted under an older, looser bar keep sitting
    # in the sheet with a stale score.
    rescored = rank_jobs(passed, {**cfg, "min_fit_score": 0, "max_per_day": 10**6})
    floor = float(cfg.get("floor_fit_score") or 0)
    keep = {j.url: j for j in rescored if j.fit_score >= floor}
    drop = [j for j in open_jobs if j.url not in keep]

    print(f"Open jobs: {len(open_jobs)}")
    print(f"Pass the current filters: {len(passed)}")
    print(f"Score at or above floor_fit_score={floor}: {len(keep)}")
    print(f"Would be dismissed: {len(drop)}")
    for j in drop:
        new = next((r.fit_score for r in rescored if r.url == j.url), None)
        now = f"{new:.1f}" if new is not None else "filtered out"
        print(f"  - was {j.fit_score:5.1f} → {now:>12}  {j.title[:60]}")

    if args.dry_run:
        print("\nDry run — nothing changed.")
        return 0

    for j in drop:
        if j.id is not None:
            store.set_status(j.id, "dismissed")

    store.upsert_ranked(list(keep.values()))

    print(f"\nDismissed {len(drop)} job(s), re-scored {len(keep)}.")

    if not args.no_export:
        path = export_excel(store.active_for_export(), cfg["paths"]["exports_dir"])
        print(f"Rewrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
