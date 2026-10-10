"""Current-events and global-coverage signals via GDELT 2.0 (no API key).

GDELT indexes worldwide news coverage and scores each article's tone. Two
things it gives us that a headline feed does not:

* **Coverage volume** -- how much of the world's news is about this right now,
  which is a proxy for how much attention an event is getting.
* **Tone trend** -- whether that coverage is getting more or less negative,
  which is more informative than a single snapshot.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from .http_util import build_url, fetch_json

GDELT_DOC_API = "https://api.gdeltproject.org/api/v2/doc/doc"

VALID_TIMESPANS = {"1h", "3h", "6h", "12h", "24h", "2d", "3d", "7d", "14d", "30d"}


def _normalize_timespan(timespan: Optional[str]) -> str:
    ts = (timespan or "3d").strip().lower()
    return ts if ts in VALID_TIMESPANS else "3d"


def get_current_events(query: str, timespan: str = "3d", max_records: int = 25) -> Dict[str, Any]:
    """Worldwide news coverage of an event or topic, with per-article tone.

    Use this for macro and geopolitical context that a company-name search
    would miss: port closures, conflicts, policy shifts, commodity shocks,
    natural disasters.

    Args:
        query: Search terms. Wrap exact phrases in double quotes, e.g.
            '"port strike" OR "dock workers"'.
        timespan: Lookback window -- one of 1h, 3h, 6h, 12h, 24h, 2d, 3d, 7d,
            14d, 30d. Default "3d".
        max_records: Maximum articles to return (1-100). Default 25.

    Returns:
        A dict with ``articles`` (title, domain, country, language, seen_at,
        url) and a ``coverage`` rollup of the top sources and countries.
    """
    if not query or not query.strip():
        return {"status": "error", "message": "query must be a non-empty string"}

    url = build_url(
        GDELT_DOC_API,
        {
            "query": query.strip(),
            "mode": "artlist",
            "maxrecords": max(1, min(int(max_records or 25), 100)),
            "timespan": _normalize_timespan(timespan),
            "sort": "hybridrel",
            "format": "json",
        },
    )
    result = fetch_json(url, ttl=900.0)
    if result["status"] != "ok":
        return result

    data = result["data"] or {}
    raw = data.get("articles") or []
    articles: List[Dict[str, Any]] = [
        {
            "title": a.get("title"),
            "domain": a.get("domain"),
            "country": a.get("sourcecountry"),
            "language": a.get("language"),
            "seen_at": a.get("seendate"),
            "url": a.get("url"),
        }
        for a in raw
    ]

    domains: Dict[str, int] = {}
    countries: Dict[str, int] = {}
    for a in articles:
        if a["domain"]:
            domains[a["domain"]] = domains.get(a["domain"], 0) + 1
        if a["country"]:
            countries[a["country"]] = countries.get(a["country"], 0) + 1

    return {
        "status": "ok",
        "query": query,
        "timespan": _normalize_timespan(timespan),
        "article_count": len(articles),
        "articles": articles,
        "coverage": {
            "top_domains": dict(sorted(domains.items(), key=lambda kv: -kv[1])[:10]),
            "top_countries": dict(sorted(countries.items(), key=lambda kv: -kv[1])[:10]),
            "note": "Article count is capped by max_records, so use get_coverage_tone_trend for true volume.",
        },
        "source": "GDELT 2.0 DOC API (gdeltproject.org)",
    }


def summarize_tone_timeline(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Reduce a GDELT timeline payload to a direction + magnitude.

    Separated from the fetch so the maths is unit-testable offline.
    """
    series = (payload or {}).get("timeline") or []
    points = series[0].get("data") if series else []
    values = [
        {"date": p.get("date"), "value": p.get("value")}
        for p in (points or [])
        if p.get("value") is not None
    ]
    if not values:
        return {"points": [], "direction": "unknown", "note": "No timeline data returned."}

    nums = [v["value"] for v in values]
    first_half = nums[: max(1, len(nums) // 2)]
    second_half = nums[max(1, len(nums) // 2) :] or nums[-1:]
    avg_first = sum(first_half) / len(first_half)
    avg_second = sum(second_half) / len(second_half)
    delta = avg_second - avg_first

    if delta <= -0.5:
        direction = "deteriorating"
    elif delta >= 0.5:
        direction = "improving"
    else:
        direction = "flat"

    return {
        "points": values[-24:],
        "average_tone": round(sum(nums) / len(nums), 3),
        "latest_tone": round(nums[-1], 3),
        "earlier_half_avg": round(avg_first, 3),
        "recent_half_avg": round(avg_second, 3),
        "change": round(delta, 3),
        "direction": direction,
        "scale_note": (
            "GDELT tone runs roughly -100 (extremely negative) to +100 (extremely "
            "positive); most real coverage sits between -10 and +10."
        ),
    }


def get_coverage_tone_trend(query: str, timespan: str = "7d") -> Dict[str, Any]:
    """Whether media tone on a topic is getting better or worse over time.

    A single tone reading is noise. The *direction* of tone across a window is
    the signal worth reasoning about.

    Args:
        query: Search terms, same syntax as ``get_current_events``.
        timespan: Lookback window, e.g. "24h", "7d", "30d". Default "7d".

    Returns:
        A dict with ``tone_trend``: average/latest tone, the change between the
        earlier and more recent half of the window, and a direction label of
        improving / flat / deteriorating.
    """
    if not query or not query.strip():
        return {"status": "error", "message": "query must be a non-empty string"}

    url = build_url(
        GDELT_DOC_API,
        {
            "query": query.strip(),
            "mode": "timelinetone",
            "timespan": _normalize_timespan(timespan),
            "format": "json",
        },
    )
    result = fetch_json(url, ttl=900.0)
    if result["status"] != "ok":
        return result

    return {
        "status": "ok",
        "query": query,
        "timespan": _normalize_timespan(timespan),
        "tone_trend": summarize_tone_timeline(result["data"]),
        "source": "GDELT 2.0 DOC API, timelinetone mode",
    }
