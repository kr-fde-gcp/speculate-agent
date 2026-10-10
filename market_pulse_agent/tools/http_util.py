"""Tiny stdlib-only HTTP helper shared by every data tool.

Design rules for this package:

* Tools NEVER raise. They return a dict with ``status`` set to ``"ok"`` or
  ``"error"`` so the LLM can see the failure and recover (retry, try another
  source, or report the gap) instead of blowing up the invocation.
* Tools depend on the standard library only. That keeps the data layer
  importable -- and unit-testable -- without installing ADK or any SDK.
* Every outbound call is cached in-process with a short TTL so a multi-agent
  fan-out that asks for the same quote five times only hits the network once.
"""

from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, Optional, Tuple

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36 market-pulse-agent/1.0"
)

DEFAULT_TIMEOUT = 15.0
DEFAULT_TTL = 300.0  # seconds

_CACHE: Dict[str, Tuple[float, Any]] = {}
_CACHE_LOCK = threading.Lock()


def _cache_get(key: str, ttl: float) -> Optional[Any]:
    with _CACHE_LOCK:
        hit = _CACHE.get(key)
        if not hit:
            return None
        stamped_at, value = hit
        if (time.time() - stamped_at) > ttl:
            _CACHE.pop(key, None)
            return None
        return value


def _cache_put(key: str, value: Any) -> None:
    with _CACHE_LOCK:
        _CACHE[key] = (time.time(), value)


def clear_cache() -> None:
    """Drop every cached response. Useful in tests and long-lived sessions."""
    with _CACHE_LOCK:
        _CACHE.clear()


def build_url(base: str, params: Optional[Dict[str, Any]] = None) -> str:
    """Join a base URL with query params, skipping ``None`` values."""
    if not params:
        return base
    clean = {k: v for k, v in params.items() if v is not None}
    sep = "&" if "?" in base else "?"
    return base + sep + urllib.parse.urlencode(clean, quote_via=urllib.parse.quote)


def fetch_text(
    url: str,
    timeout: float = DEFAULT_TIMEOUT,
    ttl: float = DEFAULT_TTL,
    headers: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """GET a URL and return ``{"status": "ok", "body": "..."}`` or an error dict."""
    cached = _cache_get(url, ttl)
    if cached is not None:
        return {"status": "ok", "body": cached, "cached": True, "url": url}

    request_headers = {"User-Agent": USER_AGENT, "Accept": "*/*"}
    if headers:
        request_headers.update(headers)

    req = urllib.request.Request(url, headers=request_headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
        body = raw.decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        return {
            "status": "error",
            "error_type": "http_error",
            "http_status": exc.code,
            "message": "Upstream returned HTTP {0} for {1}".format(exc.code, url),
            "url": url,
        }
    except urllib.error.URLError as exc:
        return {
            "status": "error",
            "error_type": "network_error",
            "message": "Could not reach {0}: {1}".format(url, exc.reason),
            "url": url,
        }
    except Exception as exc:  # pragma: no cover - defensive catch-all
        return {
            "status": "error",
            "error_type": "unexpected",
            "message": "{0}: {1}".format(type(exc).__name__, exc),
            "url": url,
        }

    _cache_put(url, body)
    return {"status": "ok", "body": body, "cached": False, "url": url}


def fetch_json(
    url: str,
    timeout: float = DEFAULT_TIMEOUT,
    ttl: float = DEFAULT_TTL,
    headers: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """GET a URL and parse JSON. Returns ``{"status": "ok", "data": ...}``."""
    result = fetch_text(url, timeout=timeout, ttl=ttl, headers=headers)
    if result["status"] != "ok":
        return result
    try:
        data = json.loads(result["body"])
    except ValueError as exc:
        return {
            "status": "error",
            "error_type": "bad_json",
            "message": "Response from {0} was not valid JSON: {1}".format(url, exc),
            "url": url,
        }
    return {"status": "ok", "data": data, "cached": result.get("cached", False), "url": url}


def pct_change(new: Optional[float], old: Optional[float]) -> Optional[float]:
    """Percent change from ``old`` to ``new``, rounded, or ``None`` if undefined."""
    if new is None or old in (None, 0):
        return None
    try:
        return round(((new - old) / old) * 100.0, 2)
    except (TypeError, ZeroDivisionError):
        return None
