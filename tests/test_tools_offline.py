"""Offline unit tests for the data layer.

These run with no network, no API key, and no ADK installed -- the tools
package deliberately imports nothing beyond the standard library. Run with:

    python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from market_pulse_agent.tools import events, market, news, weather  # noqa: E402
from market_pulse_agent.tools.http_util import build_url, pct_change  # noqa: E402

RSS_FIXTURE = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
  <item>
    <title>FedEx shares plunge after guidance cut and warning on demand - Reuters</title>
    <link>https://example.com/a</link>
    <pubDate>{recent}</pubDate>
    <description>&lt;p&gt;The company warned on weak volumes.&lt;/p&gt;</description>
    <source url="https://reuters.com">Reuters</source>
  </item>
  <item>
    <title>FedEx wins record contract, expands air hub - Bloomberg</title>
    <link>https://example.com/b</link>
    <pubDate>{recent}</pubDate>
    <description>A new partnership was announced.</description>
    <source url="https://bloomberg.com">Bloomberg</source>
  </item>
  <item>
    <title>Ancient history nobody cares about - Old Times</title>
    <link>https://example.com/c</link>
    <pubDate>Tue, 01 Jan 2019 10:00:00 GMT</pubDate>
    <description>Stale.</description>
    <source url="https://old.com">Old Times</source>
  </item>
</channel></rss>
"""


def _recent_rfc822():
    import email.utils

    return email.utils.formatdate(usegmt=True)


class TestHttpHelpers(unittest.TestCase):
    def test_build_url_skips_none_and_encodes(self):
        url = build_url("https://x.test/api", {"q": "port strike", "n": None, "c": 5})
        self.assertIn("q=port%20strike", url)
        self.assertIn("c=5", url)
        self.assertNotIn("n=", url)

    def test_build_url_respects_existing_query(self):
        self.assertTrue(build_url("https://x.test/api?a=1", {"b": "2"}).endswith("?a=1&b=2"))

    def test_pct_change_edges(self):
        self.assertEqual(pct_change(110, 100), 10.0)
        self.assertEqual(pct_change(90, 100), -10.0)
        self.assertIsNone(pct_change(100, 0))
        self.assertIsNone(pct_change(None, 100))


class TestNewsParsing(unittest.TestCase):
    def setUp(self):
        self.xml = RSS_FIXTURE.replace("{recent}", _recent_rfc822())

    def test_filters_stale_items(self):
        items = news.parse_news_feed(self.xml, window_hours=48)
        self.assertEqual(len(items), 2)
        self.assertNotIn("Ancient", items[0]["headline"])

    def test_extracts_source_and_strips_suffix(self):
        items = news.parse_news_feed(self.xml, window_hours=48)
        self.assertEqual(items[0]["source"], "Reuters")
        self.assertNotIn(" - Reuters", items[0]["headline"])

    def test_heuristic_lean_and_categories(self):
        items = news.parse_news_feed(self.xml, window_hours=48)
        self.assertEqual(items[0]["heuristic_lean"], "negative")
        self.assertEqual(items[1]["heuristic_lean"], "positive")
        self.assertIn("financial_guidance", items[0]["risk_categories"])
        self.assertIn("competitive_strategic", items[1]["risk_categories"])

    def test_strips_html_from_description(self):
        items = news.parse_news_feed(self.xml, window_hours=48)
        self.assertNotIn("<p>", items[0]["snippet"])

    def test_max_items_cap(self):
        self.assertEqual(len(news.parse_news_feed(self.xml, window_hours=48, max_items=1)), 1)

    def test_malformed_xml_returns_empty_not_raise(self):
        self.assertEqual(news.parse_news_feed("<not xml", window_hours=48), [])

    def test_empty_query_rejected(self):
        self.assertEqual(news.get_news_headlines("  ")["status"], "error")


class TestWeatherLogic(unittest.TestCase):
    def test_flags_severe_day(self):
        flags = weather._flag_day(
            {
                "precipitation_mm": 70.0,
                "snowfall_cm": 25.0,
                "wind_gusts_max_kmh": 95.0,
                "temp_max_c": 41.0,
                "temp_min_c": -20.0,
                "weather_code": 99,
            }
        )
        for expected in (
            "extreme_rainfall",
            "heavy_snow",
            "damaging_wind",
            "extreme_heat",
            "hard_freeze",
            "severe_conditions",
        ):
            self.assertIn(expected, flags)

    def test_calm_day_has_no_flags(self):
        self.assertEqual(
            weather._flag_day(
                {
                    "precipitation_mm": 0.4,
                    "snowfall_cm": 0.0,
                    "wind_gusts_max_kmh": 18.0,
                    "temp_max_c": 21.0,
                    "temp_min_c": 9.0,
                    "weather_code": 2,
                }
            ),
            [],
        )

    def test_missing_fields_do_not_raise(self):
        self.assertEqual(weather._flag_day({}), [])

    def test_snapshot_shape_with_stubbed_network(self):
        calls = []

        def fake_fetch_json(url, **kwargs):
            calls.append(url)
            if "geocoding" in url:
                return {
                    "status": "ok",
                    "data": {
                        "results": [
                            {
                                "name": "Memphis",
                                "country": "United States",
                                "admin1": "Tennessee",
                                "latitude": 35.1,
                                "longitude": -90.0,
                                "timezone": "America/Chicago",
                            }
                        ]
                    },
                }
            return {
                "status": "ok",
                "data": {
                    "timezone": "America/Chicago",
                    "current": {
                        "time": "2026-10-09T22:00",
                        "temperature_2m": 18.0,
                        "apparent_temperature": 17.0,
                        "precipitation": 0.0,
                        "wind_speed_10m": 12.0,
                        "wind_gusts_10m": 20.0,
                        "weather_code": 3,
                    },
                    "daily": {
                        "time": ["2026-10-10", "2026-10-11"],
                        "weather_code": [95, 1],
                        "temperature_2m_max": [24.0, 22.0],
                        "temperature_2m_min": [12.0, 10.0],
                        "precipitation_sum": [40.0, 0.0],
                        "snowfall_sum": [0.0, 0.0],
                        "wind_speed_10m_max": [30.0, 15.0],
                        "wind_gusts_10m_max": [65.0, 25.0],
                    },
                },
            }

        original = weather.fetch_json
        weather.fetch_json = fake_fetch_json
        try:
            out = weather.get_weather_snapshot("Memphis, Tennessee", forecast_days=2)
        finally:
            weather.fetch_json = original

        self.assertEqual(out["status"], "ok")
        self.assertEqual(out["location"]["resolved_name"], "Memphis, Tennessee, United States")
        self.assertEqual(out["current"]["conditions"], "overcast")
        self.assertEqual(out["disruption_summary"]["flagged_day_count"], 1)
        day_one = out["daily"][0]
        self.assertIn("heavy_rainfall", day_one["flags"])
        self.assertIn("high_wind", day_one["flags"])
        self.assertIn("severe_conditions", day_one["flags"])
        self.assertEqual(out["daily"][1]["flags"], [])
        self.assertEqual(len(calls), 2)

    def test_geocode_rejects_empty(self):
        self.assertEqual(weather.geocode_location("")["status"], "error")


class TestMarketMath(unittest.TestCase):
    def test_price_stats_basic(self):
        closes = [100.0 + i for i in range(70)]
        volumes = [1_000_000.0] * 70
        stats = market.compute_price_stats(closes, volumes)
        self.assertEqual(stats["last_close"], 169.0)
        self.assertEqual(stats["period_high"], 169.0)
        self.assertEqual(stats["period_low"], 100.0)
        self.assertEqual(stats["sessions_in_series"], 70)
        self.assertEqual(stats["change_1d_pct"], pct_change(169.0, 168.0))
        self.assertEqual(stats["relative_volume"], 1.0)

    def test_volatility_zero_for_flat_series(self):
        self.assertEqual(market.realized_volatility([100.0] * 40), 0.0)

    def test_volatility_higher_for_choppy_series(self):
        calm = [100.0 + (0.1 if i % 2 else -0.1) for i in range(60)]
        wild = [100.0 + (6.0 if i % 2 else -6.0) for i in range(60)]
        self.assertLess(market.realized_volatility(calm), market.realized_volatility(wild))

    def test_volatility_none_on_short_series(self):
        self.assertIsNone(market.realized_volatility([100.0, 101.0]))

    def test_empty_series_returns_error_not_raise(self):
        self.assertIn("error", market.compute_price_stats([], []))

    def test_clean_series_drops_nulls(self):
        rows = market._clean_series([1, 2, 3], [10.0, None, 12.0], [5, 6, 7])
        self.assertEqual([r[1] for r in rows], [10.0, 12.0])

    def test_empty_ticker_rejected(self):
        self.assertEqual(market.get_stock_quote("")["status"], "error")
        self.assertEqual(market.get_price_history("")["status"], "error")


class TestEventsLogic(unittest.TestCase):
    def _timeline(self, values):
        return {"timeline": [{"data": [{"date": str(i), "value": v} for i, v in enumerate(values)]}]}

    def test_deteriorating_tone(self):
        out = events.summarize_tone_timeline(self._timeline([2.0, 2.0, -3.0, -3.0]))
        self.assertEqual(out["direction"], "deteriorating")
        self.assertEqual(out["change"], -5.0)

    def test_improving_tone(self):
        self.assertEqual(
            events.summarize_tone_timeline(self._timeline([-4.0, -4.0, 1.0, 1.0]))["direction"],
            "improving",
        )

    def test_flat_tone(self):
        self.assertEqual(
            events.summarize_tone_timeline(self._timeline([1.0, 1.1, 1.0, 1.05]))["direction"],
            "flat",
        )

    def test_empty_timeline_is_unknown(self):
        self.assertEqual(events.summarize_tone_timeline({})["direction"], "unknown")

    def test_timespan_normalization(self):
        self.assertEqual(events._normalize_timespan("banana"), "3d")
        self.assertEqual(events._normalize_timespan("24H"), "24h")
        self.assertEqual(events._normalize_timespan(None), "3d")

    def test_empty_query_rejected(self):
        self.assertEqual(events.get_current_events("")["status"], "error")
        self.assertEqual(events.get_coverage_tone_trend("")["status"], "error")


if __name__ == "__main__":
    unittest.main(verbosity=2)
