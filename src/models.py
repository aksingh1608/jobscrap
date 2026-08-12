"""Shared job record schema used across all pipeline stages."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from typing import Any, Optional


@dataclass
class JobRecord:
    """Normalized job posting — one shape for every source."""

    title: str
    company: str
    location: str
    source: str
    url: str
    description: str = ""
    language: str = ""  # working language if detected: en / de / unknown
    role_type: str = ""  # internship, werkstudent, hiwi, ...
    posted_date: Optional[str] = None  # ISO date YYYY-MM-DD
    fit_score: float = 0.0
    fit_reason: str = ""
    first_seen: Optional[str] = None
    last_seen: Optional[str] = None
    status: str = "new"  # new | applied | dismissed | expired
    id: Optional[int] = None
    # Internal / raw helpers (not always persisted)
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    def fingerprint_text(self) -> str:
        """Text used for fuzzy dedup: company + title + location."""
        return f"{self.company} | {self.title} | {self.location}".lower().strip()

    def to_db_row(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "company": self.company,
            "location": self.location,
            "source": self.source,
            "url": self.url,
            "description": self.description,
            "language": self.language,
            "role_type": self.role_type,
            "posted_date": self.posted_date,
            "fit_score": self.fit_score,
            "fit_reason": self.fit_reason,
            "first_seen": self.first_seen,
            "last_seen": self.last_seen,
            "status": self.status,
        }

    @classmethod
    def from_db_row(cls, row: dict[str, Any] | Any) -> "JobRecord":
        if hasattr(row, "keys"):
            data = {k: row[k] for k in row.keys()}
        else:
            data = dict(row)
        return cls(
            id=data.get("id"),
            title=data.get("title") or "",
            company=data.get("company") or "",
            location=data.get("location") or "",
            source=data.get("source") or "",
            url=data.get("url") or "",
            description=data.get("description") or "",
            language=data.get("language") or "",
            role_type=data.get("role_type") or "",
            posted_date=data.get("posted_date"),
            fit_score=float(data.get("fit_score") or 0),
            fit_reason=data.get("fit_reason") or "",
            first_seen=data.get("first_seen"),
            last_seen=data.get("last_seen"),
            status=data.get("status") or "new",
        )

    def is_new_today(self) -> bool:
        return bool(self.first_seen) and self.first_seen == today_iso()

    def as_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d.pop("raw", None)
        return d


def today_iso() -> str:
    return date.today().isoformat()


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")
