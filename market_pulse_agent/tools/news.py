"""Headline retrieval via the public Google News RSS feed (no API key).

Returns raw headlines plus *deterministic* heuristic tags (risk category,
crude sentiment). The tags exist so the LLM has a consistent scaffold to
reason over -- they are keyword counts, not a sentiment model, and the tool
labels them as such in its own output.
"""

from __future__ import annotations

import email.utils
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from .http_util import build_url, fetch_text

GOOGLE_NEWS_RSS = "https://news.google.com/rss/search"

NEGATIVE_TERMS = [
    "lawsuit", "sued", "probe", "investigation", "recall", "strike", "layoff",
    "shutdown", "halt", "delay", "outage", "breach", "hack", "fraud", "fine",
    "penalty", "downgrade", "cuts", "cut", "miss", "misses", "plunge", "slump",
    "drop", "fall", "falls", "warn", "warning", "loss", "losses", "bankruptcy",
    "subpoena", "antitrust", "tariff", "sanction", "boycott", "resign",
    "shortage", "disruption", "defect", "crash", "protest", "weak", "slowdown",
]
POSITIVE_TERMS = [
    "beat", "beats", "record", "surge", "soar", "rally", "upgrade", "raises",
    "raise", "expands", "expansion", "launch", "wins", "win", "awarded",
    "contract", "partnership", "approval", "approved", "buyback", "dividend",
    "growth", "profit", "strong", "outperform", "milestone", "breakthrough",
    "acquires", "acquisition", "demand",
]

RISK_CATEGORIES = {
    "regulatory_legal": [
        "lawsuit", "sued", "antitrust", "regulator", "regulatory", "probe",
        "investigation", "subpoena", "fine", "penalty", "sec ", "doj", "ftc",
        "compliance", "settlement", "court", "ruling",
    ],
    "operational": [
        "recall", "outage", "shutdown", "halt", "delay", "disruption", "defect",
        "plant", "factory", "hub", "logistics", "supply chain", "shortage",
        "backlog", "capacity",
    ],
    "labor": ["strike", "union", "layoff", "layoffs", "workers", "hiring", "walkout", "staffing"],
    "financial_guidance": [
        "earnings", "guidance", "forecast", "revenue", "margin", "profit",
        "loss", "eps", "quarter", "outlook", "downgrade", "upgrade",
        "price target", "buyback", "dividend",
    ],
    "macro_policy": [
        "tariff", "sanction", "inflation", "fed", "interest rate", "rates",
        "recession", "gdp", "stimulus", "election", "policy", "trade war",
        "export control",
    ],
    "weather_climate": [
        "hurricane", "storm", "flood", "wildfire", "drought", "blizzard",
        "tornado", "heat wave", "freeze", "typhoon", "earthquake", "cyclone",
    ],
    "competitive_strategic": [
        "merger", "acquisition", "acquires", "partnership", "launch", "rival",
        "competitor", "market share", "stake", "ipo", "spin-off",
    ],
}


def _strip_html(text: str) -> str:
    return re.sub(r"<[^>]+>", " ", text or "").replace("&nbsp;", " ").strip()


def _parse_pubdate(raw: Optional[str]) -> Optional[datetime]:
    if not raw:
        return None
    try:
        dt = email.utils.parsedate_to_datetime(raw)
    except (TypeError, ValueError, IndexError):
        return None
    if dt is not None and dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _tag(text: str) -> Dict[str, Any]:
    low = " " + (text or "").lower() + " "
    neg = sorted({t for t in NEGATIVE_TERMS if t in low})
    pos = sorted({t for t in POSITIVE_TERMS if t in low})
    cats = sorted(
        {name for name, terms in RISK_CATEGORIES.items() if any(t in low for t in terms)}
    )
    if len(neg) > len(pos):
        lean = "negative"
    elif len(pos) > len(neg):
        lean = "positive"
    else:
        lean = "neutral"
    return {
        "heuristic_lean": lean,
        "negative_terms": neg,
        "positive_terms": pos,
        "risk_categories": cats,
    }


def parse_news_feed(xml_text: str, window_hours: int = 48, max_items: int = 20) -> List[Dict[str, Any]]:
    """Parse a Google News RSS document into tagged headline dicts.

    Split out from the network call so it can be unit-tested against fixtures.
    """
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []

    cutoff = datetime.now(timezone.utc) - timedelta(hours=max(1, int(window_hours or 48)))
    items: List[Dict[str, Any]] = []

    for node in root.iter("item"):
        title = (node.findtext("title") or "").strip()
        if not title:
            continue
        published = _parse_pubdate(node.findtext("pubDate"))
        if published is not None and published < cutoff:
            continue

        source = node.findtext("source") or ""
        if not source and " - " in title:
            source = title.rsplit(" - ", 1)[1]
        headline = title.rsplit(" - ", 1)[0] if (" - " in title and source and title.endswith(source)) else title

        summary = _strip_html(node.findtext("description") or "")
        entry = {
            "headline": headline,
            "source": source or "unknown",
            "published": published.isoformat() if published else None,
            "age_hours": (
                round((datetime.now(timezone.utc) - published).total_seconds() / 3600.0, 1)
                if published
                else None
            ),
            "link": (node.findtext("link") or "").strip(),
            "snippet": summary[:280],
        }
        entry.update(_tag(headline + " " + summary))
        items.append(entry)
        if len(items) >= max(1, int(max_items or 20)):
            break

    return items


def get_news_headlines(query: str, window_hours: int = 48, max_items: int = 20) -> Dict[str, Any]:
    """Recent news headlines for a company, ticker, sector, or topic.

    Args:
        query: What to search, e.g. "FedEx" or "semiconductor export controls".
            Quote multi-word phrases for exact matching.
        window_hours: Only keep items published within this many hours. Default 48.
        max_items: Maximum headlines to return. Default 20.

    Returns:
        A dict with ``items`` (headline, source, published, age_hours, link,
        heuristic tags) and an aggregate ``heuristic_rollup``. Treat the tags as
        a keyword scaffold -- read the headlines themselves before concluding
        anything.
    """
    if not query or not query.strip():
        return {"status": "error", "message": "query must be a non-empty string"}

    hours = max(1, int(window_hours or 48))
    when = "{0}h".format(hours) if hours <= 48 else "{0}d".format(max(1, hours // 24))
    url = build_url(
        GOOGLE_NEWS_RSS,
        {
            "q": "{0} when:{1}".format(query.strip(), when),
            "hl": "en-US",
            "gl": "US",
            "ceid": "US:en",
        },
    )
    result = fetch_text(url, ttl=600.0)
    if result["status"] != "ok":
        return result

    items = parse_news_feed(result["body"], window_hours=hours, max_items=max_items)
    if not items:
        return {
            "status": "ok",
            "query": query,
            "items": [],
            "heuristic_rollup": {"total": 0},
            "note": "No headlines matched in the requested window. Widen window_hours or loosen the query.",
            "source": "Google News RSS",
        }

    leans = [i["heuristic_lean"] for i in items]
    cats: Dict[str, int] = {}
    for item in items:
        for c in item["risk_categories"]:
            cats[c] = cats.get(c, 0) + 1

    return {
        "status": "ok",
        "query": query,
        "window_hours": hours,
        "items": items,
        "heuristic_rollup": {
            "total": len(items),
            "negative": leans.count("negative"),
            "neutral": leans.count("neutral"),
            "positive": leans.count("positive"),
            "risk_category_counts": dict(sorted(cats.items(), key=lambda kv: -kv[1])),
            "note": "Keyword-count heuristic only. Not a sentiment model; do not quote as sentiment data.",
        },
        "source": "Google News RSS (news.google.com)",
    }
