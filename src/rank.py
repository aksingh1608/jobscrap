"""Rank jobs with a composite fit score (0–100).

The old ranker used raw cosine similarity * 100. MiniLM cosine for *any* two
job-ish texts sits around 0.30–0.60, so every posting landed in a narrow 30–60
band and `min_fit_score` could not separate a real ML internship from a
marketing one. Scoring is now a weighted blend of independent signals:

    semantic   — rescaled embedding similarity against your profile
    role       — internship / Werkstudent / HiWi match (title beats body)
    domain     — ML / AI / data match (title beats body)
    recency    — how many days ago it was posted
    language   — English working language
    minus penalties for a high German bar and lingering exclude signals

Weights live in config.yaml under `scoring:` and are normalized, so they are
relative — doubling every weight changes nothing.

If `sentence-transformers` is unavailable the semantic weight is redistributed
across the other signals and the run still produces a ranked sheet.

fit_reason always quotes real text from the posting — never invented.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Optional

from src.config import num
from src.models import JobRecord
from src.textmatch import contains, matched, quote_around

log = logging.getLogger(__name__)

MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"

# Cosine range used to rescale similarity into 0–1. Below `floor` the posting is
# unrelated; at/above `ceil` it is as close as MiniLM realistically gets.
DEFAULT_SIM_FLOOR = 0.15
DEFAULT_SIM_CEIL = 0.60

DEFAULT_WEIGHTS = {
    "semantic": 35.0,
    "role": 25.0,
    "domain": 25.0,
    "recency": 8.0,
    "language": 7.0,
}
DEFAULT_PENALTIES = {
    "german_penalty": 20.0,
    "exclude_penalty": 15.0,
}

EN_SNIPPET = re.compile(r".{0,40}(english|working\s*language).{0,40}", re.I)
DE_SNIPPET = re.compile(
    r".{0,30}(verhandlungssicher|flie[sß]end|deutschkenntnisse|german\s*(b2|c1|c2)).{0,30}",
    re.I,
)


# ---------------------------------------------------------------------------
# Embeddings
# ---------------------------------------------------------------------------


def _load_model():
    """Load the sentence-transformer, or return None if it is unavailable.

    A missing/broken model must not take down the daily run — the pipeline
    falls back to keyword-only scoring instead.
    """
    try:
        from sentence_transformers import SentenceTransformer
    except Exception:
        log.warning(
            "sentence-transformers unavailable — scoring without the semantic "
            "signal (its weight is redistributed)",
            exc_info=True,
        )
        return None
    try:
        log.info("Loading embedding model %s ...", MODEL_NAME)
        return SentenceTransformer(MODEL_NAME)
    except Exception:
        log.warning("Embedding model failed to load — keyword-only scoring", exc_info=True)
        return None


def _similarities(model, profile: str, jobs: list[JobRecord]) -> Optional[list[float]]:
    """Cosine similarity of each job against the profile, or None on failure."""
    if model is None:
        return None
    try:
        import numpy as np

        profile_emb = np.asarray(model.encode(profile, normalize_embeddings=True))
        texts = [
            f"{j.title}\n{j.company}\n{j.location}\n{j.description[:3000]}" for j in jobs
        ]
        job_embs = np.asarray(
            model.encode(texts, normalize_embeddings=True, show_progress_bar=False)
        )
        # Vectors are already L2-normalized, so the dot product is the cosine.
        return [float(v) for v in job_embs @ profile_emb]
    except Exception:
        log.warning("Embedding pass failed — keyword-only scoring", exc_info=True)
        return None


def _rescale(sim: float, floor: float, ceil: float) -> float:
    if ceil <= floor:
        return max(0.0, min(1.0, sim))
    return max(0.0, min(1.0, (sim - floor) / (ceil - floor)))


# ---------------------------------------------------------------------------
# Component scores — each returns 0.0–1.0
# ---------------------------------------------------------------------------


def _role_component(job: JobRecord, role_types: list[str]) -> tuple[float, list[str]]:
    title_hits = matched(job.title, role_types)
    if title_hits:
        return 1.0, title_hits
    body_hits = matched(job.description, role_types)
    if body_hits:
        return 0.5, body_hits
    if job.role_type:
        return 0.4, [job.role_type]
    return 0.0, []


def _domain_component(job: JobRecord, domains: list[str]) -> tuple[float, list[str]]:
    title_hits = matched(job.title, domains)
    body_hits = matched(job.description, domains)
    all_hits = title_hits + [h for h in body_hits if h not in title_hits]
    if not all_hits:
        return 0.0, []
    # Title match is the strong signal; extra body mentions add confidence.
    base = 0.7 if title_hits else 0.25
    depth = min(0.3, 0.1 * len(all_hits))
    return min(1.0, base + depth), all_hits


def _recency_component(job: JobRecord, freshness_days: int) -> float:
    from src.filter import days_since_posted

    age = days_since_posted(job)
    if age is None:
        return 0.5  # unknown date — neither rewarded nor punished
    if freshness_days <= 0:
        return 1.0
    return max(0.0, 1.0 - (age / float(freshness_days)))


def _language_component(job: JobRecord, english: bool) -> float:
    if english:
        return 1.0
    if job.language == "de":
        return 0.2
    return 0.5


def _is_english(job: JobRecord) -> bool:
    if job.language == "en":
        return True
    return contains(f"{job.title} {job.description}", "english")


def _find_snippet(pattern: re.Pattern[str], text: str) -> str:
    m = pattern.search(text.replace("\n", " "))
    return m.group(0).strip() if m else ""


def _build_reason(
    job: JobRecord,
    *,
    parts_scores: dict[str, float],
    role_hits: list[str],
    domain_hits: list[str],
    english: bool,
    german_hit: bool,
    sim: Optional[float],
) -> str:
    """One-line reason grounded in real posting text."""
    blob = f"{job.title}. {job.description}"
    parts: list[str] = []

    if role_hits:
        quote = quote_around(blob, role_hits[0], window=20)
        parts.append(f"Role: «{quote}»" if quote else f"Role: {role_hits[0]}")

    if domain_hits:
        quote = quote_around(blob, domain_hits[0], window=25)
        shown = ", ".join(domain_hits[:3])
        parts.append(f"Domain: «{quote}»" if quote else f"Domain: {shown}")

    if english:
        en = _find_snippet(EN_SNIPPET, blob)
        parts.append(f"English: «{en}»" if en else "English working language detected")

    if german_hit:
        de = _find_snippet(DE_SNIPPET, blob)
        parts.append(f"German bar (penalty): «{de}»" if de else "German above A2 required")

    breakdown = " ".join(
        f"{k}={v:.0f}" for k, v in parts_scores.items() if v
    )
    if sim is not None:
        breakdown = f"{breakdown} sim={sim:.2f}"
    parts.append(f"[{breakdown.strip()}]")

    return " · ".join(parts)[:400]


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def rank_jobs(jobs: list[JobRecord], cfg: dict[str, Any]) -> list[JobRecord]:
    if not jobs:
        log.info("Rank: nothing to score")
        return []

    scoring = cfg.get("scoring") or {}
    weights = {k: float(scoring.get(k, v)) for k, v in DEFAULT_WEIGHTS.items()}
    german_penalty = float(scoring.get("german_penalty", DEFAULT_PENALTIES["german_penalty"]))
    exclude_penalty = float(scoring.get("exclude_penalty", DEFAULT_PENALTIES["exclude_penalty"]))
    sim_floor = float(scoring.get("sim_floor", DEFAULT_SIM_FLOOR))
    sim_ceil = float(scoring.get("sim_ceil", DEFAULT_SIM_CEIL))

    role_types = cfg.get("role_types") or []
    domains = cfg.get("domains") or []
    exclude_words = cfg.get("exclude_words") or []
    freshness = int(num(cfg, "freshness_days", 7))
    prefer_english = bool(cfg.get("prefer_english", True))
    min_score = num(cfg, "min_fit_score", 55)
    max_per_day = int(num(cfg, "max_per_day", 50))
    profile = (cfg.get("profile_text") or "").strip()
    if not profile:
        profile = "AI ML data science internship student Berlin Python"

    model = _load_model()
    sims = _similarities(model, profile, jobs)
    if sims is None:
        # Redistribute the semantic weight over the remaining signals so scores
        # still span the full 0–100 range.
        weights["semantic"] = 0.0

    total_weight = sum(weights.values()) or 1.0

    from src.filter import german_requirement_too_high

    scored: list[JobRecord] = []
    for idx, job in enumerate(jobs):
        sim = sims[idx] if sims is not None else None

        components = {
            "semantic": _rescale(sim, sim_floor, sim_ceil) if sim is not None else 0.0,
            "role": 0.0,
            "domain": 0.0,
            "recency": _recency_component(job, freshness),
            "language": 0.0,
        }
        components["role"], role_hits = _role_component(job, role_types)
        components["domain"], domain_hits = _domain_component(job, domains)

        english = _is_english(job)
        components["language"] = (
            _language_component(job, english) if prefer_english else 0.5
        )

        # Weighted average, rescaled to 0–100.
        points = {k: components[k] * weights[k] for k in weights}
        score = 100.0 * sum(points.values()) / total_weight

        german_hit = german_requirement_too_high(job, cfg.get("german_max_level") or "A2")
        if german_hit:
            score -= german_penalty
        if any(contains(f"{job.title} {job.description}", w) for w in exclude_words):
            score -= exclude_penalty

        score = max(0.0, min(100.0, score))
        job.fit_score = round(score, 1)
        job.fit_reason = _build_reason(
            job,
            parts_scores=points,
            role_hits=role_hits,
            domain_hits=domain_hits,
            english=english and prefer_english,
            german_hit=german_hit,
            sim=sim,
        )
        scored.append(job)

    scored.sort(key=lambda j: j.fit_score, reverse=True)
    if scored:
        log.info(
            "Rank score spread: top=%.1f median=%.1f bottom=%.1f",
            scored[0].fit_score,
            scored[len(scored) // 2].fit_score,
            scored[-1].fit_score,
        )
    above = [j for j in scored if j.fit_score >= min_score]
    final = above[:max_per_day]

    # Thin days still deserve a sheet: if too few clear the bar, top up with the
    # next best rather than mailing an empty file. `floor_fit_score` is the hard
    # line below which a posting is not worth your time at all.
    min_results = int(num(cfg, "min_results", 0))
    if min_results and len(final) < min_results:
        floor = num(cfg, "floor_fit_score", 0)
        chosen = {id(j) for j in final}
        backfill = [
            j for j in scored if id(j) not in chosen and j.fit_score >= floor
        ][: min_results - len(final)]
        if backfill:
            log.info(
                "Rank: only %s cleared min_fit_score(%s) — backfilled %s more down to "
                "floor_fit_score(%s)",
                len(final),
                min_score,
                len(backfill),
                floor,
            )
            final = final + backfill

    log.info(
        "Rank: scored=%s | above min_fit_score(%s)=%s | kept %s%s",
        len(scored),
        min_score,
        len(above),
        len(final),
        "" if sims is not None else " (keyword-only, no embeddings)",
    )
    return final


__all__ = ["rank_jobs", "MODEL_NAME"]
