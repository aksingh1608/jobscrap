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
    args = parser.parse_args()
    summary = run_pipeline(args.config)
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
