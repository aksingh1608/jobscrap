"""Rank jobs by embedding similarity + keyword boosts/penalties.

Scoring weights live in config.yaml under `scoring:` — tweak them there
(and/or the constants below) without rewriting the pipeline.

Final fit_score is clamped to 0–100.
fit_reason always quotes real snippets from the posting (never invented).
"""

from __future__ import annotations

import logging
import re
from typing import Any

import numpy as np

from src.models import JobRecord

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Adjustable defaults (overridden by config.scoring)
# ---------------------------------------------------------------------------
# embedding_weight: cosine_similarity (0–1) * this value  (default 100 → 0–100)
# english_boost:    +points when English working language is detected
# must_have_boost:  +points per matched must_have keyword
# must_have_boost_cap: max total from must_have boosts
# german_penalty:   -points if German > A2 is required
# exclude_penalty:  -points if soft exclude signals linger
#
# Tip: if Excel is empty, lower min_fit_score in config.yaml or raise boosts.
# ---------------------------------------------------------------------------

MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"

EN_SNIPPET = re.compile(
    r".{0,40}(english|working\s*language).{0,40}",
    re.I,
)
DOMAIN_SNIPPET = re.compile(
    r".{0,30}(machine learning|deep learning|data science|computer vision|"
    r"nlp|llm|generative ai|mlops|devops|artificial intelligence|"
    r"python|pytorch|tensorflow).{0,30}",
    re.I,
)
ROLE_SNIPPET = re.compile(
    r".{0,20}(internship|praktikum|praktikant|werkstudent|working student|"
    r"research assistant|hilfskraft|hiwi|shk).{0,20}",
    re.I,
)
DE_SNIPPET = re.compile(
    r".{0,30}(verhandlungssicher|flie[sß]end|deutschkenntnisse|german\s*(b2|c1|c2)).{0,30}",
    re.I,
)


def _load_model():
    from sentence_transformers import SentenceTransformer

    log.info("Loading embedding model %s ...", MODEL_NAME)
    return SentenceTransformer(MODEL_NAME)


def _cosine(a: np.ndarray, b: np.ndarray) -> float:
    na = np.linalg.norm(a)
    nb = np.linalg.norm(b)
    if na == 0 or nb == 0:
        return 0.0
    return float(np.dot(a, b) / (na * nb))


def _find_snippet(pattern: re.Pattern[str], text: str) -> str:
    m = pattern.search(text.replace("\n", " "))
    if not m:
        return ""
    return m.group(0).strip()


def _matched_must_haves(job: JobRecord, must_have: list[str]) -> list[str]:
    blob = f"{job.title} {job.description}".lower()
    return [m for m in must_have if m.lower() in blob]


def _build_reason(
    job: JobRecord,
    *,
    sim: float,
    english: bool,
    matched: list[str],
    german_hit: bool,
) -> str:
    """One-line reason grounded in real posting text."""
    blob = f"{job.title}. {job.description}"
    parts: list[str] = []

    role = _find_snippet(ROLE_SNIPPET, blob)
    if role:
        parts.append(f"Role cue: «{role}»")

    domain = _find_snippet(DOMAIN_SNIPPET, blob)
    if domain:
        parts.append(f"Domain: «{domain}»")

    if english:
        en = _find_snippet(EN_SNIPPET, blob)
        if en:
            parts.append(f"English: «{en}»")
        else:
            parts.append("English working language detected in posting")

    if matched:
        # Quote first match occurrence
        first = matched[0]
        idx = blob.lower().find(first.lower())
        if idx >= 0:
            start = max(0, idx - 20)
            end = min(len(blob), idx + len(first) + 20)
            quote = blob[start:end].replace("\n", " ").strip()
            parts.append(f"Keyword: «{quote}»")
        else:
            parts.append(f"Keywords: {', '.join(matched[:4])}")

    if german_hit:
        de = _find_snippet(DE_SNIPPET, blob)
        if de:
            parts.append(f"German bar (penalty): «{de}»")

    parts.append(f"embedding sim={sim:.2f}")
    reason = " · ".join(parts)
    return reason[:400]


def rank_jobs(jobs: list[JobRecord], cfg: dict[str, Any]) -> list[JobRecord]:
    if not jobs:
        log.info("Rank: nothing to score")
        return []

    scoring = cfg.get("scoring") or {}
    embedding_weight = float(scoring.get("embedding_weight", 100))
    english_boost = float(scoring.get("english_boost", 5))
    must_have_boost = float(scoring.get("must_have_boost", 3))
    must_have_cap = float(scoring.get("must_have_boost_cap", 12))
    german_penalty = float(scoring.get("german_penalty", 20))
    exclude_penalty = float(scoring.get("exclude_penalty", 15))
    prefer_english = bool(cfg.get("prefer_english", True))
    must_have = cfg.get("must_have_any") or []
    exclude_words = cfg.get("exclude_words") or []
    min_score = float(cfg.get("min_fit_score") or 45)
    max_per_day = int(cfg.get("max_per_day") or 50)
    profile = (cfg.get("profile_text") or "").strip()
    if not profile:
        profile = "AI ML data science internship student Berlin Python"

    # Lazy import / model load (heavy)
    model = _load_model()
    profile_emb = model.encode(profile, normalize_embeddings=True)

    texts = [
        f"{j.title}\n{j.company}\n{j.location}\n{j.description[:3000]}" for j in jobs
    ]
    job_embs = model.encode(texts, normalize_embeddings=True, show_progress_bar=False)

    from src.filter import german_requirement_too_high

    scored: list[JobRecord] = []
    for job, emb in zip(jobs, job_embs):
        sim = _cosine(np.asarray(profile_emb), np.asarray(emb))
        # Cosine is typically ~0.2–0.7 for related postings → *100 lands mid-range
        sim_score = max(0.0, min(1.0, sim)) * embedding_weight

        boost = 0.0
        english = job.language == "en" or bool(
            re.search(r"english", f"{job.title} {job.description}", re.I)
        )
        if prefer_english and english:
            boost += english_boost

        matched = _matched_must_haves(job, must_have)
        boost += min(must_have_cap, must_have_boost * len(matched))

        penalty = 0.0
        german_hit = german_requirement_too_high(
            job, cfg.get("german_max_level") or "A2"
        )
        if german_hit:
            penalty += german_penalty

        blob_l = f"{job.title} {job.description}".lower()
        soft_ex = sum(1 for w in exclude_words if w.lower() in blob_l)
        if soft_ex:
            penalty += exclude_penalty

        score = sim_score + boost - penalty
        score = max(0.0, min(100.0, score))

        job.fit_score = round(score, 1)
        job.fit_reason = _build_reason(
            job,
            sim=sim,
            english=english and prefer_english,
            matched=matched,
            german_hit=german_hit,
        )
        scored.append(job)

    scored.sort(key=lambda j: j.fit_score, reverse=True)
    if scored:
        top = ", ".join(f"{j.fit_score:.0f}" for j in scored[:10])
        log.info("Rank score sample (top 10): %s", top)
    above = [j for j in scored if j.fit_score >= min_score]
    final = above[:max_per_day]

    log.info(
        "Rank: scored=%s | above min_fit_score(%s)=%s | kept top %s",
        len(scored),
        min_score,
        len(above),
        len(final),
    )
    return final
