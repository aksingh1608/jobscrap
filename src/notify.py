"""Free alerts via Telegram bot or SMTP email."""

from __future__ import annotations

import logging
import os
import smtplib
from email.message import EmailMessage
from typing import Any

import requests

log = logging.getLogger(__name__)


def notify_run(cfg: dict[str, Any], *, new_count: int, total_open: int, excel_path: str) -> None:
    msg = (
        f"Internship aggregator: {new_count} new job(s) today. "
        f"{total_open} open in dashboard. Excel: {excel_path}"
    )
    notify_cfg = cfg.get("notify") or {}
    if notify_cfg.get("telegram"):
        _telegram(msg)
    if notify_cfg.get("email"):
        _email(msg)


def _telegram(text: str) -> None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        log.warning("Telegram enabled but TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID not set")
        return
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    try:
        resp = requests.post(
            url,
            json={"chat_id": chat_id, "text": text},
            timeout=30,
        )
        resp.raise_for_status()
        log.info("Telegram alert sent")
    except Exception:
        log.exception("Telegram alert failed")


def _email(text: str) -> None:
    host = os.environ.get("SMTP_HOST", "").strip()
    port = int(os.environ.get("SMTP_PORT", "587"))
    user = os.environ.get("SMTP_USER", "").strip()
    password = os.environ.get("SMTP_PASSWORD", "").strip()
    to_addr = os.environ.get("NOTIFY_EMAIL_TO", "").strip()
    from_addr = os.environ.get("NOTIFY_EMAIL_FROM", user).strip()
    if not all([host, user, password, to_addr]):
        log.warning("Email enabled but SMTP env vars incomplete")
        return
    msg = EmailMessage()
    msg["Subject"] = "Internship aggregator — daily run"
    msg["From"] = from_addr
    msg["To"] = to_addr
    msg.set_content(text)
    try:
        with smtplib.SMTP(host, port, timeout=30) as smtp:
            smtp.starttls()
            smtp.login(user, password)
            smtp.send_message(msg)
        log.info("Email alert sent to %s", to_addr)
    except Exception:
        log.exception("Email alert failed")
