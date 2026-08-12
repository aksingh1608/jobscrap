"""Offline smoke test for normalize → dedup → filter → store → export."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.config import load_config
from src.dedup import dedup_jobs
from src.export import export_excel
from src.filter import filter_jobs
from src.models import JobRecord
from src.normalize import normalize_jobs
from src.store import JobStore


SAMPLES = [
    JobRecord(
        title="Machine Learning Internship",
        company="AI Labs Berlin",
        location="Berlin, Germany",
        source="test",
        url="https://example.com/jobs/ml-intern-1",
        description=(
            "We are looking for an Internship in Machine Learning. "
            "Working language: English. Python, PyTorch, NLP/LLM projects. "
            "German is a plus but not required."
        ),
        posted_date="2026-08-10",
    ),
    JobRecord(
        title="Senior ML Engineer",
        company="BigCorp",
        location="Berlin",
        source="test",
        url="https://example.com/jobs/senior-ml",
        description="Permanent senior role. 5+ years experience. Lead the team.",
        posted_date="2026-08-10",
    ),
    JobRecord(
        title="Werkstudent Data Science",
        company="DataCo",
        location="München, Deutschland",
        source="test",
        url="https://example.com/jobs/ws-ds",
        description=(
            "Werkstudent Data Science mit Python und Machine Learning. "
            "Deutschkenntnisse mindestens C1 erforderlich, verhandlungssicher."
        ),
        posted_date="2026-08-09",
    ),
    JobRecord(
        title="HiWi Computer Vision",
        company="TU Berlin",
        location="Berlin",
        source="arbeitsagentur",
        url="https://example.com/jobs/hiwi-cv",
        description=(
            "Studentische Hilfskraft / HiWi for Computer Vision research. "
            "English working language. Deep Learning with PyTorch."
        ),
        posted_date="2026-08-11",
    ),
    JobRecord(
        title="Machine Learning Internship",
        company="AI Labs Berlin",
        location="Berlin",
        source="jobspy:indeed",
        url="https://example.com/jobs/ml-intern-dup",
        description="Duplicate of first posting for dedup test. Python ML internship.",
        posted_date="2026-08-10",
    ),
]


def main() -> None:
    cfg = load_config()
    # Use a temp DB so we don't clobber real data
    cfg["paths"]["sqlite"] = str(ROOT / "data" / "smoke_test.db")
    cfg["paths"]["exports_dir"] = str(ROOT / "exports")

    norm = normalize_jobs(SAMPLES, cfg)
    unique = dedup_jobs(norm)
    filtered = filter_jobs(unique, cfg)
    # Lightweight score without downloading the embedding model
    for i, j in enumerate(filtered):
        j.fit_score = 90 - i * 5
        j.fit_reason = f"Smoke test · role={j.role_type} · lang={j.language}"

    store = JobStore(cfg["paths"]["sqlite"])
    store.upsert_ranked(filtered)
    active = store.active_for_export()
    path = export_excel(active, cfg["paths"]["exports_dir"])

    print(
        f"OK normalize={len(norm)} dedup={len(unique)} "
        f"filter={len(filtered)} stored={len(active)} excel={path}"
    )
    assert len(filtered) >= 1, "expected at least the English ML internship"
    assert all("senior" not in j.title.lower() for j in filtered)
    print("smoke test passed")


if __name__ == "__main__":
    main()
