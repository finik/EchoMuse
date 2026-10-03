"""
Outside weather for the Spot's clock.

The panel draws the picture. This module turns an Open-Meteo code into
the picture name, and turns an IP-geolocation reply into a place. The
network calls live in the controller, so tests never leave the machine.
"""

from __future__ import annotations

# WMO weather interpretation codes, shortened to what fits under a clock.
_LABELS = {
    0: "Clear",
    1: "Fair",
    2: "Cloudy",
    3: "Cloudy",
    45: "Fog",
    48: "Fog",
    51: "Drizzle",
    53: "Drizzle",
    55: "Drizzle",
    56: "Drizzle",
    57: "Drizzle",
    61: "Rain",
    63: "Rain",
    65: "Rain",
    66: "Rain",
    67: "Rain",
    71: "Snow",
    73: "Snow",
    75: "Snow",
    77: "Snow",
    80: "Showers",
    81: "Showers",
    82: "Showers",
    85: "Snow",
    86: "Snow",
    95: "Storm",
    96: "Storm",
    99: "Storm",
}


def label(code: int) -> str:
    return _LABELS.get(int(code), "")


# Picture the Spot draws. Coarser than the words: a clock only needs to
# say sun, cloud, or rain at a glance.
_KINDS = {
    0: "sun",
    1: "fair",
    2: "cloud",
    3: "cloud",
    45: "fog",
    48: "fog",
    51: "rain",
    53: "rain",
    55: "rain",
    56: "rain",
    57: "rain",
    61: "rain",
    63: "rain",
    65: "rain",
    66: "rain",
    67: "rain",
    71: "snow",
    73: "snow",
    75: "snow",
    77: "snow",
    80: "rain",
    81: "rain",
    82: "rain",
    85: "snow",
    86: "snow",
    95: "storm",
    96: "storm",
    99: "storm",
}


def kind(code: int) -> str:
    return _KINDS.get(int(code), "cloud")


def format_line(temp: float, unit: str, code: int) -> str:
    """
    One line for the panel. The number is already in `unit` — Open-Meteo
    converts when asked, so this does not convert again.
    """
    mark = "F" if str(unit).upper().startswith("F") else "C"
    word = label(code)
    text = f"{int(round(float(temp)))}\u00b0{mark}"
    if word:
        text += "  " + word
    return text


def from_forecast(body: dict) -> tuple[float, int] | None:
    cur = body.get("current") if isinstance(body, dict) else None
    if not isinstance(cur, dict):
        return None
    if "temperature_2m" not in cur or "weather_code" not in cur:
        return None
    try:
        return float(cur["temperature_2m"]), int(cur["weather_code"])
    except (TypeError, ValueError):
        return None


def from_ipwho(body: dict) -> tuple[float, float, str] | None:
    """Latitude, longitude, country code. None when the lookup failed."""
    if not isinstance(body, dict) or not body.get("success"):
        return None
    try:
        return (
            float(body["latitude"]),
            float(body["longitude"]),
            str(body.get("country_code") or ""),
        )
    except (KeyError, TypeError, ValueError):
        return None


def unit_for(country_code: str) -> str:
    return "F" if country_code.upper() == "US" else "C"
