"""Data-collection tools for the Market Pulse agent.

Every module here is standard-library only and free of ADK imports, so the
data layer can be imported, tested, and debugged without the agent runtime.
"""

from .events import get_coverage_tone_trend, get_current_events
from .http_util import clear_cache
from .market import compare_to_benchmark, get_price_history, get_stock_quote
from .news import get_news_headlines
from .weather import geocode_location, get_weather_snapshot

__all__ = [
    "geocode_location",
    "get_weather_snapshot",
    "get_news_headlines",
    "get_current_events",
    "get_coverage_tone_trend",
    "get_stock_quote",
    "get_price_history",
    "compare_to_benchmark",
    "clear_cache",
]
