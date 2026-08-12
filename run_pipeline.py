#!/usr/bin/env python3
"""CLI entrypoint: run the full daily internship aggregation pipeline."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Ensure project root is on sys.path when run as a script
ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.pipeline import run_pipeline  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Collect, filter, rank, store, and export internship jobs."
    )
    parser.add_argument(
        "-c",
        "--config",
        default=None,
        help="Path to config.yaml (default: ./config.yaml)",
    )
    parser.add_argument(
        "--no-notify",
        action="store_true",
        help="Skip Telegram / email alerts for this run",
    )
    parser.add_argument(
        "--min-score",
        type=float,
        default=None,
        help="Override min_fit_score for this run (useful when tuning)",
    )
    parser.add_argument(
        "--max-per-day",
        type=int,
        default=None,
        help="Override max_per_day for this run",
    )
    parser.add_argument(
        "--summary-json",
        default=None,
        help="Also write the run summary to this file (for CI step summaries)",
    )
    args = parser.parse_args()

    overrides: dict[str, object] = {}
    if args.min_score is not None:
        overrides["min_fit_score"] = args.min_score
    if args.max_per_day is not None:
        overrides["max_per_day"] = args.max_per_day

    summary = run_pipeline(
        args.config,
        notify=not args.no_notify,
        overrides=overrides,
    )
    text = json.dumps(summary, indent=2)
    print(text)
    if args.summary_json:
        Path(args.summary_json).write_text(text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
