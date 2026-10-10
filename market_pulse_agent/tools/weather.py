"""Weather signals via Open-Meteo (no API key required).

The point of weather in this agent is not the forecast itself -- it is the
*operational disruption* a forecast implies for a company's facilities:
hubs, plants, stores, farms, rigs, data centres, shipping lanes.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from .http_util import build_url, fetch_json

GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"

# WMO weather interpretation codes -> plain English.
WMO_CODES = {
    0: "clear sky",
    1: "mainly clear",
    2: "partly cloudy",
    3: "overcast",
    45: "fog",
    48: "depositing rime fog",
    51: "light drizzle",
    53: "moderate drizzle",
    55: "dense drizzle",
    56: "light freezing drizzle",
    57: "dense freezing drizzle",
    61: "slight rain",
    63: "moderate rain",
    65: "heavy rain",
    66: "light freezing rain",
    67: "heavy freezing rain",
    71: "slight snowfall",
    73: "moderate snowfall",
    75: "heavy snowfall",
    77: "snow grains",
    80: "slight rain showers",
    81: "moderate rain showers",
    82: "violent rain showers",
    85: "slight snow showers",
    86: "heavy snow showers",
    95: "thunderstorm",
    96: "thunderstorm with slight hail",
    99: "thunderstorm with heavy hail",
}

# Thresholds that flip a forecast from "weather" into "operational risk".
# Units: mm precipitation/day, cm snow/day, km/h gusts, degrees Celsius.
THRESHOLDS = {
    "heavy_precip_mm": 25.0,
    "extreme_precip_mm": 60.0,
    "snow_cm": 5.0,
    "heavy_snow_cm": 20.0,
    "high_wind_kmh": 60.0,
    "damaging_wind_kmh": 90.0,
    "heat_c": 35.0,
    "extreme_heat_c": 40.0,
    "freeze_c": -10.0,
    "hard_freeze_c": -18.0,
}

DISRUPTIVE_CODES = {65, 67, 75, 82, 86, 95, 96, 99}


def geocode_location(location: str) -> Dict[str, Any]:
    """Resolve a place name to coordinates.

    Use this when you have a facility description like "Memphis hub" and need
    latitude/longitude before pulling a forecast.

    Args:
        location: Free-text place name, e.g. "Memphis, Tennessee" or "Rotterdam".

    Returns:
        A dict with ``status`` and, on success, ``matches``: a list of
        ``{name, country, admin1, latitude, longitude, timezone, population}``.
    """
    if not location or not location.strip():
        return {"status": "error", "message": "location must be a non-empty string"}

    url = build_url(GEOCODE_URL, {"name": location.strip(), "count": 3, "format": "json"})
    result = fetch_json(url, ttl=86400.0)
    if result["status"] != "ok":
        return result

    raw = result["data"].get("results") or []
    if not raw:
        return {
            "status": "error",
            "error_type": "not_found",
            "message": "No coordinates found for '{0}'. Try a city plus country.".format(location),
        }

    matches = [
        {
            "name": item.get("name"),
            "country": item.get("country"),
            "admin1": item.get("admin1"),
            "latitude": item.get("latitude"),
            "longitude": item.get("longitude"),
            "timezone": item.get("timezone"),
            "population": item.get("population"),
        }
        for item in raw
    ]
    return {"status": "ok", "query": location, "matches": matches}


def _flag_day(day: Dict[str, Any]) -> List[str]:
    """Return the list of disruption flags a single forecast day earns."""
    flags: List[str] = []
    precip = day.get("precipitation_mm")
    snow = day.get("snowfall_cm")
    gust = day.get("wind_gusts_max_kmh")
    tmax = day.get("temp_max_c")
    tmin = day.get("temp_min_c")
    code = day.get("weather_code")

    if precip is not None and precip >= THRESHOLDS["extreme_precip_mm"]:
        flags.append("extreme_rainfall")
    elif precip is not None and precip >= THRESHOLDS["heavy_precip_mm"]:
        flags.append("heavy_rainfall")

    if snow is not None and snow >= THRESHOLDS["heavy_snow_cm"]:
        flags.append("heavy_snow")
    elif snow is not None and snow >= THRESHOLDS["snow_cm"]:
        flags.append("snow")

    if gust is not None and gust >= THRESHOLDS["damaging_wind_kmh"]:
        flags.append("damaging_wind")
    elif gust is not None and gust >= THRESHOLDS["high_wind_kmh"]:
        flags.append("high_wind")

    if tmax is not None and tmax >= THRESHOLDS["extreme_heat_c"]:
        flags.append("extreme_heat")
    elif tmax is not None and tmax >= THRESHOLDS["heat_c"]:
        flags.append("heat")

    if tmin is not None and tmin <= THRESHOLDS["hard_freeze_c"]:
        flags.append("hard_freeze")
    elif tmin is not None and tmin <= THRESHOLDS["freeze_c"]:
        flags.append("freeze")

    if code in DISRUPTIVE_CODES:
        flags.append("severe_conditions")

    return flags


def get_weather_snapshot(location: str, forecast_days: int = 7) -> Dict[str, Any]:
    """Current conditions plus a multi-day outlook, with disruption flags.

    Call this for each location that materially affects the company's
    operations (distribution hubs, factories, growing regions, ports).

    Args:
        location: Place name, e.g. "Memphis, Tennessee".
        forecast_days: How many days ahead to return, 1-16. Defaults to 7.

    Returns:
        A dict with ``current``, ``daily`` (each day carrying a ``flags`` list),
        and ``disruption_summary`` naming the days that breach operational
        thresholds. Flags are deterministic threshold checks, not predictions.
    """
    geo = geocode_location(location)
    if geo["status"] != "ok":
        return geo
    place = geo["matches"][0]

    days = max(1, min(int(forecast_days or 7), 16))
    url = build_url(
        FORECAST_URL,
        {
            "latitude": place["latitude"],
            "longitude": place["longitude"],
            "current": "temperature_2m,apparent_temperature,precipitation,wind_speed_10m,wind_gusts_10m,weather_code",
            "daily": (
                "weather_code,temperature_2m_max,temperature_2m_min,"
                "precipitation_sum,snowfall_sum,wind_speed_10m_max,wind_gusts_10m_max"
            ),
            "forecast_days": days,
            "timezone": "auto",
            "wind_speed_unit": "kmh",
        },
    )
    result = fetch_json(url, ttl=1800.0)
    if result["status"] != "ok":
        return result

    data = result["data"]
    cur = data.get("current") or {}
    current = {
        "observed_at": cur.get("time"),
        "temp_c": cur.get("temperature_2m"),
        "feels_like_c": cur.get("apparent_temperature"),
        "precipitation_mm": cur.get("precipitation"),
        "wind_kmh": cur.get("wind_speed_10m"),
        "wind_gusts_kmh": cur.get("wind_gusts_10m"),
        "conditions": WMO_CODES.get(cur.get("weather_code"), "unknown"),
    }

    d = data.get("daily") or {}
    dates = d.get("time") or []
    daily: List[Dict[str, Any]] = []
    for i, date in enumerate(dates):

        def at(key: str) -> Optional[float]:
            series = d.get(key) or []
            return series[i] if i < len(series) else None

        day = {
            "date": date,
            "weather_code": at("weather_code"),
            "conditions": WMO_CODES.get(at("weather_code"), "unknown"),
            "temp_max_c": at("temperature_2m_max"),
            "temp_min_c": at("temperature_2m_min"),
            "precipitation_mm": at("precipitation_sum"),
            "snowfall_cm": at("snowfall_sum"),
            "wind_max_kmh": at("wind_speed_10m_max"),
            "wind_gusts_max_kmh": at("wind_gusts_10m_max"),
        }
        day["flags"] = _flag_day(day)
        daily.append(day)

    flagged = [
        {"date": day["date"], "flags": day["flags"], "conditions": day["conditions"]}
        for day in daily
        if day["flags"]
    ]

    return {
        "status": "ok",
        "location": {
            "resolved_name": ", ".join(
                [p for p in [place.get("name"), place.get("admin1"), place.get("country")] if p]
            ),
            "latitude": place["latitude"],
            "longitude": place["longitude"],
            "timezone": data.get("timezone"),
        },
        "current": current,
        "daily": daily,
        "disruption_summary": {
            "flagged_day_count": len(flagged),
            "flagged_days": flagged,
            "thresholds_used": THRESHOLDS,
            "note": (
                "Flags are deterministic threshold breaches on a public forecast, "
                "not a forecast of business impact."
            ),
        },
        "source": "Open-Meteo (open-meteo.com)",
    }
