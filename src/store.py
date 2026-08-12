"""SQLite persistence for ranked jobs."""

from __future__ import annotations

import logging
import sqlite3
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Optional

from src.models import JobRecord, today_iso

log = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    company TEXT NOT NULL,
    location TEXT NOT NULL,
    source TEXT NOT NULL,
    url TEXT NOT NULL UNIQUE,
    description TEXT,
    language TEXT,
    role_type TEXT,
    posted_date TEXT,
    fit_score REAL,
    fit_reason TEXT,
    first_seen TEXT NOT NULL,
    last_seen TEXT,
    status TEXT NOT NULL DEFAULT 'new'
);

-- The last_seen index is created in _migrate(), not here: on a database from
-- an older version CREATE TABLE IF NOT EXISTS is a no-op, so the column does
-- not exist yet and indexing it would fail before the migration can add it.
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
CREATE INDEX IF NOT EXISTS idx_jobs_fit ON jobs(fit_score DESC);
CREATE INDEX IF NOT EXISTS idx_jobs_first_seen ON jobs(first_seen);

CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT
);
"""

# Statuses the user has acted on — never resurrect these into the daily sheet.
HANDLED = ("applied", "dismissed")


class JobStore:
    def __init__(self, db_path: str):
        self.db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript(SCHEMA)
            self._migrate(conn)

    def _migrate(self, conn: sqlite3.Connection) -> None:
        """Add columns introduced after the first release, in place."""
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(jobs)")}
        if "last_seen" not in cols:
            log.info("Migrating jobs table: adding last_seen")
            conn.execute("ALTER TABLE jobs ADD COLUMN last_seen TEXT")
            conn.execute("UPDATE jobs SET last_seen = first_seen WHERE last_seen IS NULL")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_jobs_last_seen ON jobs(last_seen)")

    def set_meta(self, key: str, value: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO meta(key, value) VALUES(?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value),
            )

    def get_meta(self, key: str, default: str = "") -> str:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT value FROM meta WHERE key=?", (key,)
            ).fetchone()
            return row["value"] if row else default

    def upsert_ranked(self, jobs: list[JobRecord]) -> dict[str, int]:
        """Insert new URLs as status=new; refresh score for still-open jobs.

        Applied/dismissed jobs are never resurrected into the active list.
        A job seen again after expiring is reopened, since it is live once more.
        Returns counts: inserted, updated, reopened, skipped_handled.
        """
        today = today_iso()
        inserted = updated = reopened = skipped = 0
        with self._connect() as conn:
            for job in jobs:
                existing = conn.execute(
                    "SELECT id, status, first_seen FROM jobs WHERE url=?",
                    (job.url,),
                ).fetchone()
                if existing:
                    if existing["status"] in HANDLED:
                        skipped += 1
                        continue
                    was_expired = existing["status"] == "expired"
                    conn.execute(
                        """
                        UPDATE jobs SET
                            title=?, company=?, location=?, source=?,
                            description=?, language=?, role_type=?,
                            posted_date=?, fit_score=?, fit_reason=?,
                            last_seen=?, status='new'
                        WHERE id=?
                        """,
                        (
                            job.title,
                            job.company,
                            job.location,
                            job.source,
                            job.description,
                            job.language,
                            job.role_type,
                            job.posted_date,
                            job.fit_score,
                            job.fit_reason,
                            today,
                            existing["id"],
                        ),
                    )
                    if was_expired:
                        reopened += 1
                    else:
                        updated += 1
                else:
                    conn.execute(
                        """
                        INSERT INTO jobs (
                            title, company, location, source, url, description,
                            language, role_type, posted_date, fit_score,
                            fit_reason, first_seen, last_seen, status
                        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                        """,
                        (
                            job.title,
                            job.company,
                            job.location,
                            job.source,
                            job.url,
                            job.description,
                            job.language,
                            job.role_type,
                            job.posted_date,
                            job.fit_score,
                            job.fit_reason,
                            today,
                            today,
                            "new",
                        ),
                    )
                    inserted += 1
        log.info(
            "Store upsert: inserted=%s updated=%s reopened=%s skipped_handled=%s",
            inserted,
            updated,
            reopened,
            skipped,
        )
        return {
            "inserted": inserted,
            "updated": updated,
            "reopened": reopened,
            "skipped_handled": skipped,
        }

    def expire_stale(self, expire_days: int) -> int:
        """Retire jobs not seen in the last `expire_days` runs' worth of days.

        Without this the sheet only ever grows: postings that vanished from
        every board months ago would still be exported as open.
        """
        if expire_days <= 0:
            return 0
        cutoff = (date.today() - timedelta(days=expire_days)).isoformat()
        with self._connect() as conn:
            cur = conn.execute(
                "UPDATE jobs SET status='expired' "
                "WHERE status='new' AND COALESCE(last_seen, first_seen) < ?",
                (cutoff,),
            )
            count = cur.rowcount or 0
        if count:
            log.info("Store: expired %s job(s) unseen since %s", count, cutoff)
        return count

    def prune(self, keep_days: int) -> int:
        """Delete expired rows older than `keep_days` to bound the DB size.

        Applied/dismissed history is kept forever so those jobs never reappear.
        """
        if keep_days <= 0:
            return 0
        cutoff = (date.today() - timedelta(days=keep_days)).isoformat()
        with self._connect() as conn:
            cur = conn.execute(
                "DELETE FROM jobs WHERE status='expired' "
                "AND COALESCE(last_seen, first_seen) < ?",
                (cutoff,),
            )
            count = cur.rowcount or 0
        if count:
            log.info("Store: pruned %s expired row(s) older than %s", count, cutoff)
        return count

    def set_status(self, job_id: int, status: str) -> None:
        assert status in ("new", "applied", "dismissed", "expired")
        with self._connect() as conn:
            conn.execute("UPDATE jobs SET status=? WHERE id=?", (status, job_id))

    def list_active(
        self,
        *,
        source: Optional[str] = None,
        city: Optional[str] = None,
        language: Optional[str] = None,
        new_today: bool = False,
        min_score: float = 0,
        limit: Optional[int] = None,
    ) -> list[JobRecord]:
        clauses = ["status='new'"]
        params: list[Any] = []
        if source:
            clauses.append("source LIKE ?")
            params.append(f"%{source}%")
        if city:
            clauses.append("location LIKE ?")
            params.append(f"%{city}%")
        if language:
            clauses.append("language=?")
            params.append(language)
        if new_today:
            clauses.append("first_seen=?")
            params.append(today_iso())
        if min_score:
            clauses.append("fit_score>=?")
            params.append(min_score)
        sql = (
            "SELECT * FROM jobs WHERE "
            + " AND ".join(clauses)
            + " ORDER BY fit_score DESC"
        )
        if limit:
            sql += " LIMIT ?"
            params.append(int(limit))
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [JobRecord.from_db_row(r) for r in rows]

    def count_new_today(self) -> int:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS c FROM jobs WHERE status='new' AND first_seen=?",
                (today_iso(),),
            ).fetchone()
            return int(row["c"])

    def count_open(self) -> int:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS c FROM jobs WHERE status='new'"
            ).fetchone()
            return int(row["c"])

    def active_for_export(self, limit: Optional[int] = None) -> list[JobRecord]:
        return self.list_active(limit=limit)
