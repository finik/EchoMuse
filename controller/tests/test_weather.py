"""Outside weather line for the Spot clock (em_weather)."""
import em_weather as w


def test_clear_sky_is_a_short_line():
    assert w.format_line(64.4, "F", 0) == "64\u00b0F  Clear"


def test_unknown_code_is_just_the_temperature():
    assert w.format_line(3.2, "C", 999) == "3\u00b0C"


def test_forecast_without_a_current_block_is_nothing():
    assert w.from_forecast({}) is None
    assert w.from_forecast({"current": {"temperature_2m": 10}}) is None


def test_forecast_reading():
    assert w.from_forecast({"current": {"temperature_2m": 18.6, "weather_code": 61}}) == (18.6, 61)


def test_ip_lookup_needs_a_success_flag():
    assert w.from_ipwho({"success": False}) is None
    assert w.from_ipwho({"success": True, "latitude": 47.6, "longitude": -122.3, "country_code": "US"}) == (
        47.6, -122.3, "US",
    )


def test_us_uses_fahrenheit():
    assert w.unit_for("US") == "F"
    assert w.unit_for("GB") == "C"


def test_picture_is_coarser_than_the_word():
    assert w.kind(0) == "sun"
    assert w.kind(1) == "fair"
    assert w.kind(3) == "cloud"
    assert w.kind(45) == "fog"
    assert w.kind(63) == "rain"
    assert w.kind(73) == "snow"
    assert w.kind(95) == "storm"
    assert w.kind(999) == "cloud"
