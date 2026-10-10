"""Price and volatility context from key-free public endpoints.

Primary source is the Yahoo Finance chart endpoint; Stooq CSV is the fallback
when Yahoo rate-limits or demands a consent cookie. The agent needs this layer
for one reason: *you cannot speculate about impact without knowing what is
already priced in.* A storm that moved the stock 6% yesterday is old news.
"""

from __future__ import annotations

import csv
import io
import math
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from .http_util import build_url, fetch_json, fetch_text, pct_change

YAHOO_CHART = "https://query1.finance.yahoo.com/v8/finance/chart/{0}"
STOOQ_CSV = "https://stooq.com/q/d/l/"

TRADING_DAYS_PER_YEAR = 252


def _clean_series(timestamps: List[Any], closes: List[Any], volumes: List[Any]):
    """Drop nulls Yahoo leaves in for halted/holiday sessions."""
    rows = []
    for i, ts in enumerate(timestamps or []):
        close = closes[i] if i < len(closes or []) else None
        if ts is None or close is None:
            continue
        vol = volumes[i] if i < len(volumes or []) else None
        rows.append((ts, float(close), float(vol) if vol is not None else None))
    return rows


def realized_volatility(closes: List[float], window: int = 20) -> Optional[float]:
    """Annualized realized volatility (%) from the last ``window`` daily closes."""
    series = [c for c in closes if c]
    if len(series) < max(3, window // 2):
        return None
    tail = series[-(window + 1) :]
    rets = []
    for prev, cur in zip(tail, tail[1:]):
        if prev > 0 and cur > 0:
            rets.append(math.log(cur / prev))
    if len(rets) < 2:
        return None
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
    return round(math.sqrt(var) * math.sqrt(TRADING_DAYS_PER_YEAR) * 100.0, 2)


def _lookback(closes: List[float], n: int) -> Optional[float]:
    return closes[-(n + 1)] if len(closes) > n else None


def compute_price_stats(closes: List[float], volumes: List[Optional[float]]) -> Dict[str, Any]:
    """Derive the move/volatility picture from a close+volume series.

    Pure function, no network -- unit-tested directly.
    """
    closes = [c for c in closes if c is not None]
    if not closes:
        return {"error": "empty price series"}

    last = closes[-1]
    vols = [v for v in volumes if v]
    avg_vol_20 = round(sum(vols[-20:]) / len(vols[-20:])) if vols else None
    latest_vol = vols[-1] if vols else None

    return {
        "last_close": round(last, 4),
        "change_1d_pct": pct_change(last, _lookback(closes, 1)),
        "change_5d_pct": pct_change(last, _lookback(closes, 5)),
        "change_21d_pct": pct_change(last, _lookback(closes, 21)),
        "change_63d_pct": pct_change(last, _lookback(closes, 63)),
        "period_high": round(max(closes), 4),
        "period_low": round(min(closes), 4),
        "pct_below_period_high": pct_change(last, max(closes)),
        "realized_vol_20d_annualized_pct": realized_volatility(closes, 20),
        "realized_vol_60d_annualized_pct": realized_volatility(closes, 60),
        "avg_volume_20d": avg_vol_20,
        "latest_volume": round(latest_vol) if latest_vol else None,
        "relative_volume": (
            round(latest_vol / avg_vol_20, 2) if (latest_vol and avg_vol_20) else None
        ),
        "sessions_in_series": len(closes),
    }


def _yahoo_chart(ticker: str, range_: str, interval: str) -> Dict[str, Any]:
    url = build_url(
        YAHOO_CHART.format(ticker.strip().upper()),
        {"range": range_, "interval": interval, "includePrePost": "false"},
    )
    result = fetch_json(url, ttl=300.0)
    if result["status"] != "ok":
        return result

    chart = (result["data"] or {}).get("chart") or {}
    if chart.get("error"):
        return {
            "status": "error",
            "error_type": "symbol_error",
            "message": "Yahoo rejected '{0}': {1}".format(ticker, chart["error"]),
        }
    results = chart.get("result") or []
    if not results:
        return {"status": "error", "error_type": "empty", "message": "No chart data for " + ticker}
    return {"status": "ok", "result": results[0]}


def _stooq_fallback(ticker: str) -> Dict[str, Any]:
    """Daily OHLCV CSV from Stooq. US tickers need a '.us' suffix."""
    symbol = ticker.strip().lower()
    if "." not in symbol:
        symbol += ".us"
    url = build_url(STOOQ_CSV, {"s": symbol, "i": "d"})
    result = fetch_text(url, ttl=900.0)
    if result["status"] != "ok":
        return result

    body = result["body"].strip()
    if not body or body.lower().startswith("no data"):
        return {"status": "error", "error_type": "not_found", "message": "Stooq has no data for " + ticker}

    closes: List[float] = []
    volumes: List[Optional[float]] = []
    dates: List[str] = []
    for row in csv.DictReader(io.StringIO(body)):
        try:
            closes.append(float(row["Close"]))
            dates.append(row["Date"])
            volumes.append(float(row["Volume"]) if row.get("Volume") else None)
        except (KeyError, TypeError, ValueError):
            continue
    if not closes:
        return {"status": "error", "error_type": "parse", "message": "Could not parse Stooq CSV for " + ticker}

    return {
        "status": "ok",
        "ticker": ticker.upper(),
        "as_of": dates[-1],
        "stats": compute_price_stats(closes[-260:], volumes[-260:]),
        "source": "Stooq daily CSV (fallback; Yahoo unavailable)",
        "data_quality_note": "Fallback source. End-of-day only -- no intraday or live quote.",
    }


def get_stock_quote(ticker: str) -> Dict[str, Any]:
    """Latest price, session move, and 52-week position for one ticker.

    Args:
        ticker: Exchange symbol, e.g. "FDX", "AAPL", "SHEL.L".

    Returns:
        A dict with ``quote`` (price, previous close, day range, 52-week range,
        market state) and the data source used. Falls back to end-of-day data
        if the live source is unavailable -- always check ``source``.
    """
    if not ticker or not ticker.strip():
        return {"status": "error", "message": "ticker must be a non-empty string"}

    chart = _yahoo_chart(ticker, "5d", "1d")
    if chart["status"] != "ok":
        fb = _stooq_fallback(ticker)
        if fb["status"] == "ok":
            fb["note"] = "Live quote unavailable ({0}); returning end-of-day stats.".format(
                chart.get("message")
            )
        return fb

    meta = chart["result"].get("meta") or {}
    price = meta.get("regularMarketPrice")
    prev = meta.get("chartPreviousClose") or meta.get("previousClose")

    return {
        "status": "ok",
        "ticker": (meta.get("symbol") or ticker).upper(),
        "quote": {
            "name": meta.get("longName") or meta.get("shortName"),
            "price": price,
            "currency": meta.get("currency"),
            "exchange": meta.get("fullExchangeName") or meta.get("exchangeName"),
            "previous_close": prev,
            "change_pct": pct_change(price, prev),
            "day_high": meta.get("regularMarketDayHigh"),
            "day_low": meta.get("regularMarketDayLow"),
            "fifty_two_week_high": meta.get("fiftyTwoWeekHigh"),
            "fifty_two_week_low": meta.get("fiftyTwoWeekLow"),
            "pct_from_52w_high": pct_change(price, meta.get("fiftyTwoWeekHigh")),
            "volume": meta.get("regularMarketVolume"),
            "market_state": meta.get("marketState"),
            "quote_time_utc": (
                datetime.fromtimestamp(meta["regularMarketTime"], tz=timezone.utc).isoformat()
                if meta.get("regularMarketTime")
                else None
            ),
        },
        "source": "Yahoo Finance chart endpoint (unofficial, no key)",
        "data_quality_note": "Unofficial endpoint; may be delayed. Do not treat as an execution-grade feed.",
    }


def get_price_history(ticker: str, period: str = "3mo", interval: str = "1d") -> Dict[str, Any]:
    """Historical closes with derived move, volatility, and volume stats.

    Use this to judge what is already priced in: a stock that fell 8% last week
    on the same story has absorbed much of the news.

    Args:
        ticker: Exchange symbol, e.g. "FDX".
        period: Lookback window -- 5d, 1mo, 3mo, 6mo, 1y, 2y, 5y. Default "3mo".
        interval: Bar size -- 1d, 1wk, 1mo. Default "1d".

    Returns:
        A dict with ``stats``: trailing returns over 1/5/21/63 sessions,
        annualized 20d and 60d realized volatility, average and relative
        volume, and the high/low of the period.
    """
    if not ticker or not ticker.strip():
        return {"status": "error", "message": "ticker must be a non-empty string"}

    chart = _yahoo_chart(ticker, period or "3mo", interval or "1d")
    if chart["status"] != "ok":
        return _stooq_fallback(ticker)

    res = chart["result"]
    meta = res.get("meta") or {}
    quote_block = ((res.get("indicators") or {}).get("quote") or [{}])[0]
    rows = _clean_series(
        res.get("timestamp") or [], quote_block.get("close") or [], quote_block.get("volume") or []
    )
    if not rows:
        return _stooq_fallback(ticker)

    closes = [r[1] for r in rows]
    volumes = [r[2] for r in rows]

    return {
        "status": "ok",
        "ticker": (meta.get("symbol") or ticker).upper(),
        "name": meta.get("longName") or meta.get("shortName"),
        "currency": meta.get("currency"),
        "period": period,
        "interval": interval,
        "first_session": datetime.fromtimestamp(rows[0][0], tz=timezone.utc).date().isoformat(),
        "last_session": datetime.fromtimestamp(rows[-1][0], tz=timezone.utc).date().isoformat(),
        "stats": compute_price_stats(closes, volumes),
        "source": "Yahoo Finance chart endpoint (unofficial, no key)",
    }


def compare_to_benchmark(ticker: str, benchmark: str = "SPY", period: str = "1mo") -> Dict[str, Any]:
    """Relative performance vs a benchmark, to separate stock news from market drift.

    If the whole market is down 3%, a 3% drop in the stock is not a story.

    Args:
        ticker: Exchange symbol to evaluate.
        benchmark: Comparison symbol. Default "SPY" (S&P 500 ETF).
        period: Lookback window, e.g. "5d", "1mo", "3mo". Default "1mo".

    Returns:
        A dict with each symbol's return over the period and the ``excess_return_pct``
        (ticker minus benchmark).
    """
    a = get_price_history(ticker, period=period)
    b = get_price_history(benchmark, period=period)
    if a["status"] != "ok":
        return a
    if b["status"] != "ok":
        return b

    def total_return(payload: Dict[str, Any]) -> Optional[float]:
        stats = payload.get("stats") or {}
        for key in ("change_63d_pct", "change_21d_pct", "change_5d_pct", "change_1d_pct"):
            if stats.get(key) is not None:
                return stats[key]
        return None

    ra, rb = total_return(a), total_return(b)
    return {
        "status": "ok",
        "period": period,
        "ticker": {"symbol": a["ticker"], "return_pct": ra},
        "benchmark": {"symbol": b["ticker"], "return_pct": rb},
        "excess_return_pct": round(ra - rb, 2) if (ra is not None and rb is not None) else None,
        "note": "Simple price return difference, not a beta-adjusted alpha.",
    }
