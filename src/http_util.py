"""HTTP helpers with polite retries on 429 / transient errors."""

from __future__ import annotations

import logging
import time
from typing import Any, Optional

import requests
from tenacity import (
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

log = logging.getLogger(__name__)

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9,de;q=0.8",
}


class RateLimitError(Exception):
    pass


def _raise_for_status(resp: requests.Response) -> None:
    if resp.status_code == 429:
        retry_after = resp.headers.get("Retry-After")
        wait_s = int(retry_after) if retry_after and retry_after.isdigit() else 30
        log.warning("HTTP 429 — sleeping %ss then retrying", wait_s)
        time.sleep(wait_s)
        raise RateLimitError("rate limited")
    # Do not retry other 4xx — fail fast
    resp.raise_for_status()


def _is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, RateLimitError):
        return True
    if isinstance(exc, (requests.Timeout, requests.ConnectionError)):
        return True
    if isinstance(exc, requests.HTTPError) and exc.response is not None:
        return exc.response.status_code >= 500
    return False


@retry(
    retry=retry_if_exception(_is_retryable),
    wait=wait_exponential(multiplier=2, min=2, max=60),
    stop=stop_after_attempt(5),
    reraise=True,
)
def get_json(
    url: str,
    *,
    headers: Optional[dict[str, str]] = None,
    params: Optional[dict[str, Any]] = None,
    timeout: int = 45,
) -> Any:
    hdrs = {**DEFAULT_HEADERS, **(headers or {})}
    resp = requests.get(url, headers=hdrs, params=params, timeout=timeout)
    _raise_for_status(resp)
    return resp.json()


@retry(
    retry=retry_if_exception(_is_retryable),
    wait=wait_exponential(multiplier=2, min=2, max=60),
    stop=stop_after_attempt(5),
    reraise=True,
)
def get_text(
    url: str,
    *,
    headers: Optional[dict[str, str]] = None,
    timeout: int = 45,
) -> str:
    hdrs = {**DEFAULT_HEADERS, **(headers or {})}
    resp = requests.get(url, headers=hdrs, timeout=timeout)
    _raise_for_status(resp)
    return resp.text
