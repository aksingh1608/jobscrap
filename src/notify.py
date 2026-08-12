"""Free alerts via Telegram bot or SMTP email.

The daily message carries the top matches with clickable links, so the sheet is
optional — you can triage from your phone.
"""

from __future__ import annotations

import html
import logging
import os
import smtplib
from email.message import EmailMessage
from typing import Any, Optional

import requests

from src.models import JobRecord

log = logging.getLogger(__name__)

TELEGRAM_LIMIT = 4000  # API caps a message at 4096 chars; leave headroom


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _headline(new_count: int, total_open: int) -> str:
    if new_count:
        return f"{new_count} new internship match(es) today · {total_open} open in total"
    return f"No new matches today · {total_open} still open"


def _telegram_body(jobs: list[JobRecord], new_count: int, total_open: int) -> str:
    lines = [f"<b>{html.escape(_headline(new_count, total_open))}</b>"]
    for i, job in enumerate(jobs, start=1):
        title = html.escape(job.title[:90])
        company = html.escape(job.company[:50])
        location = html.escape(job.location[:40])
        flag = "🆕 " if job.is_new_today() else ""
        lines.append(
            f'\n{i}. {flag}<b>{job.fit_score:.0f}</b> · <a href="{html.escape(job.url)}">'
            f"{title}</a>\n    {company} — {location}"
        )
    text = "\n".join(lines)
    if len(text) > TELEGRAM_LIMIT:
        text = text[:TELEGRAM_LIMIT].rsplit("\n", 1)[0] + "\n…"
    return text


def _email_body(jobs: list[JobRecord], new_count: int, total_open: int, excel_path: str) -> str:
    rows = []
    for job in jobs:
        flag = "NEW " if job.is_new_today() else ""
        rows.append(
            "<tr>"
            f"<td align='right'><b>{job.fit_score:.0f}</b></td>"
            f"<td>{flag}<a href=\"{html.escape(job.url)}\">{html.escape(job.title[:110])}</a></td>"
            f"<td>{html.escape(job.company[:60])}</td>"
            f"<td>{html.escape(job.location[:40])}</td>"
            "</tr>"
        )
    return (
        f"<p><b>{html.escape(_headline(new_count, total_open))}</b></p>"
        "<table cellpadding='6' cellspacing='0' border='0'>"
        "<tr><th align='right'>Fit</th><th align='left'>Role</th>"
        "<th align='left'>Company</th><th align='left'>Location</th></tr>"
        + "".join(rows)
        + "</table>"
        f"<p style='color:#666;font-size:12px'>Full sheet: {html.escape(excel_path)}</p>"
    )


def notify_run(
    cfg: dict[str, Any],
    *,
    new_count: int,
    total_open: int,
    excel_path: str,
    top_jobs: Optional[list[JobRecord]] = None,
) -> None:
    notify_cfg = cfg.get("notify") or {}
    top_n = int(notify_cfg.get("top_n", 10))
    # Today's finds lead the message — otherwise the same high scorers repeat
    # every morning and the new ones sink below the fold.
    jobs = sorted(
        top_jobs or [],
        key=lambda j: (not j.is_new_today(), -j.fit_score),
    )[:top_n]

    if notify_cfg.get("skip_when_empty") and not new_count:
        log.info("Notify: no new jobs today and skip_when_empty is set — staying quiet")
        return

    if notify_cfg.get("telegram"):
        _telegram(_telegram_body(jobs, new_count, total_open))
    if notify_cfg.get("email"):
        _email(
            subject=f"Internships: {new_count} new today",
            html_body=_email_body(jobs, new_count, total_open, excel_path),
            attachment=excel_path if notify_cfg.get("attach_excel") else None,
        )


def notify_failure(cfg: dict[str, Any], error: str) -> None:
    """Tell the user when a scheduled run dies — silence must not look like 'no jobs'."""
    notify_cfg = cfg.get("notify") or {}
    text = f"⚠️ Internship aggregator run FAILED:\n{html.escape(error[:1500])}"
    if notify_cfg.get("telegram"):
        _telegram(text)
    if notify_cfg.get("email"):
        _email(subject="Internship aggregator — run failed", html_body=f"<pre>{text}</pre>")


def _telegram(text: str) -> None:
    token = _env("TELEGRAM_BOT_TOKEN")
    chat_id = _env("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        log.warning("Telegram enabled but TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID not set")
        return
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    try:
        resp = requests.post(
            url,
            json={
                "chat_id": chat_id,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            },
            timeout=30,
        )
        resp.raise_for_status()
        log.info("Telegram alert sent")
    except Exception:
        log.exception("Telegram alert failed")


def _email(*, subject: str, html_body: str, attachment: Optional[str] = None) -> None:
    host = _env("SMTP_HOST")
    port = int(_env("SMTP_PORT", "587") or 587)
    user = _env("SMTP_USER")
    password = _env("SMTP_PASSWORD")
    to_addr = _env("NOTIFY_EMAIL_TO")
    from_addr = _env("NOTIFY_EMAIL_FROM") or user
    if not all([host, user, password, to_addr]):
        log.warning("Email enabled but SMTP env vars incomplete")
        return

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = from_addr
    msg["To"] = to_addr
    # Plain-text fallback for clients that refuse HTML
    msg.set_content("HTML message — see the attached sheet or open in an HTML client.")
    msg.add_alternative(html_body, subtype="html")

    if attachment:
        try:
            with open(attachment, "rb") as f:
                msg.add_attachment(
                    f.read(),
                    maintype="application",
                    subtype="vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    filename=os.path.basename(attachment),
                )
        except OSError:
            log.warning("Could not attach %s", attachment, exc_info=True)

    try:
        with smtplib.SMTP(host, port, timeout=30) as smtp:
            smtp.starttls()
            smtp.login(user, password)
            smtp.send_message(msg)
        log.info("Email alert sent to %s", to_addr)
    except Exception:
        log.exception("Email alert failed")
