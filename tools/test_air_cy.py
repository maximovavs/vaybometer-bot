#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Offline checks for Cyprus official air-quality integration."""
from __future__ import annotations

import os
import sys
import time
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import air  # noqa: E402


SAMPLE_OFFICIAL_HTML = """
<section>
  <h4>Limassol - Traffic Station</h4>
  <p>PM₂.₅: 12 μg/m³</p>
  <p>PM₁₀: 68 μg/m³</p>
  <p>NO₂: 32 μg/m³</p>
  <p>O₃: 78 μg/m³</p>
  <p>Updated on: 25/06/2026 08:00</p>
</section>
<section>
  <h4>Nicosia - Traffic Station</h4>
  <p>PM₂.₅: 10 μg/m³</p>
  <p>PM₁₀: 49 μg/m³</p>
  <p>NO₂: 28 μg/m³</p>
  <p>Updated on: 25/06/2026 08:00</p>
</section>
<section>
  <h4>Paralimni - Traffic Station</h4>
  <p>PM₂.₅: 8 μg/m³</p>
  <p>PM₁₀: 21 μg/m³</p>
  <p>O₃: 90 μg/m³</p>
  <p>Updated on: 25/06/2026 08:00</p>
</section>
"""


def assert_true(name: str, cond: bool, detail: str = "") -> None:
    if not cond:
        raise AssertionError(f"{name}: {detail or 'assertion failed'}")


def test_official_parse() -> None:
    rows = air._parse_cy_airquality_official_html(SAMPLE_OFFICIAL_HTML)
    assert_true("official_parse", len(rows) == 3, f"expected 3 stations, got {len(rows)}")
    limassol = next(row for row in rows if row["station"].startswith("Limassol"))
    assert_true("official_parse", limassol["src"] == "cy_official")
    assert_true("official_parse", limassol["pm10"] == 68.0)
    assert_true("official_parse", limassol["dominant_pollutant"] == "PM₁₀")
    assert_true("official_parse", limassol["clean_label"].startswith("🟠"))
    print("PASS official_parse")


def test_official_priority_and_city_mapping() -> None:
    rows = air._parse_cy_airquality_official_html(SAMPLE_OFFICIAL_HTML)
    for row in rows:
        row["fresh_min"] = 5
        row["observation_status"] = "fresh"
    old_cache = air._CY_AIRQUALITY_CACHE
    air._CY_AIRQUALITY_CACHE = (time.time(), rows)
    try:
        official = air._src_cy_airquality_official(34.988, 34.012, city="Ayia Napa")
        assert_true("official_priority", official is not None)
        assert_true("official_priority", official["station"].startswith("Paralimni"))
        merged = air.merge_air_sources(
            official,
            {"aqi": 10, "pm10": 10, "src": "iqair", "observed_at": "2026-08-08T08:00:00Z", "fresh_min": 5},
            {"aqi": 20, "pm10": 20, "src": "openmeteo", "observed_at": "2026-08-08T08:00:00Z", "fresh_min": 5},
        )
        assert_true("official_priority", merged["src"] == "cy_official")
        assert_true("official_priority", merged["src_icon"] == "🇨🇾 AirQuality CY")
        assert_true("official_priority", merged["aqi"] == 10.0)
        assert_true("official_priority", merged["aqi_src"] == "iqair")
    finally:
        air._CY_AIRQUALITY_CACHE = old_cache
    print("PASS official_priority_and_city_mapping")


def test_fallback_when_official_fails() -> None:
    old_official = air._src_cy_airquality_official
    old_iqair = air._src_iqair
    old_openmeteo = air._src_openmeteo
    air._src_cy_airquality_official = lambda lat, lon, city=None: None
    air._src_iqair = lambda lat, lon: None
    air._src_openmeteo = lambda lat, lon: {
        "aqi": 33.0,
        "pm25": 6.0,
        "pm10": 18.0,
        "src": "openmeteo",
        "observed_at": "2026-08-08T08:00:00Z",
        "fresh_min": 5,
    }
    try:
        result = air.get_air(34.707, 33.022)
        assert_true("fallback", result["src"] == "openmeteo")
        assert_true("fallback", result["aqi"] == 33.0)
    finally:
        air._src_cy_airquality_official = old_official
        air._src_iqair = old_iqair
        air._src_openmeteo = old_openmeteo
    print("PASS fallback_when_official_fails")


def test_official_levels_are_not_fabricated_aqi() -> None:
    html = "\n".join(
        f"<section><h4>{station}</h4><p>PM₁₀: {pm10} μg/m³</p><p>Updated on: 08/08/2026 10:00</p></section>"
        for station, pm10 in (
            ("Limassol - Traffic Station", 40),
            ("Nicosia - Traffic Station", 48),
            ("Larnaca - Traffic Station", 68),
            ("Paphos - Traffic Station", 120),
        )
    )
    rows = air._parse_cy_airquality_official_html(html)
    assert_true("official_levels", [row["pollution_level"] for row in rows] == [1, 2, 3, 4])
    assert_true("official_levels", [row["pollution_category"] for row in rows] == ["низкий", "умеренный", "высокий", "очень высокий"])
    assert_true("official_levels", all(row["aqi"] is None for row in rows))
    assert_true("official_levels", not any(row.get("aqi") in (25, 75, 125, 175) for row in rows))
    print("PASS official_levels_are_not_fabricated_aqi")


def test_stale_and_missing_observations_are_deterministic() -> None:
    stale_official = {
        "src": "cy_official",
        "station": "Limassol - Traffic Station",
        "pm10": 90.0,
        "pollution_level": 3,
        "pollution_category": "высокий",
        "observed_at": "2026-08-08T04:00:00+03:00",
        "fresh_min": air.CY_AIR_OBSERVATION_MAX_AGE_MIN + 1,
    }
    fresh_fallback = {
        "src": "openmeteo",
        "aqi": 42.0,
        "pm25": 8.0,
        "pm10": 18.0,
        "observed_at": "2026-08-08T08:00:00Z",
        "fresh_min": 5,
    }
    fallback = air.merge_air_sources(stale_official, fresh_fallback)
    assert_true("stale_fallback", fallback["src"] == "openmeteo")
    assert_true("stale_fallback", fallback["aqi"] == 42.0)
    assert_true("stale_fallback", fallback["pm10"] == 18.0, "stale official PM must not leak into fresh fallback")

    stale_only = air.merge_air_sources(stale_official)
    assert_true("stale_only", stale_only["observation_status"] == "stale")
    assert_true("stale_only", stale_only["aqi"] == "н/д" and stale_only["pm10"] is None)

    missing_time = dict(stale_official, fresh_min=None, observed_at=None)
    missing_only = air.merge_air_sources(missing_time)
    assert_true("missing_time", missing_only["observation_status"] == "time_missing")
    assert_true("missing_time", missing_only["aqi"] == "н/д" and missing_only["pm10"] is None)
    print("PASS stale_and_missing_observations_are_deterministic")


def test_official_line_keeps_category_metrics_source_city_and_time() -> None:
    sys.modules.setdefault("imghdr", types.SimpleNamespace(what=lambda *_args, **_kwargs: None))
    import post_common

    official = {
        "src": "cy_official",
        "station": "Limassol - Traffic Station",
        "pm25": 12.0,
        "pm10": 68.0,
        "dominant_pollutant": "PM₁₀",
        "pollution_level": 3,
        "pollution_category": "высокий",
        "clean_label": "🟠 PM₁₀",
        "observed_at": "2026-08-08T08:00:00+03:00",
        "fresh_min": 5,
    }
    numeric = {
        "src": "openmeteo",
        "aqi": 42.0,
        "pm25": 8.0,
        "pm10": 18.0,
        "observed_at": "2026-08-08T05:00:00Z",
        "fresh_min": 5,
    }
    line = post_common._air_quality_line_from_data(air.merge_air_sources(official, numeric)) or ""
    assert_true("official_line", "официальный уровень 3/4 (высокий)" in line)
    assert_true("official_line", "PM₂.₅ 12 / PM₁₀ 68" in line)
    assert_true("official_line", "AQI 42" in line and "🛰 OM" in line)
    assert_true("official_line", "наблюдение в Лимассоле" in line)
    assert_true("official_line", "🇨🇾 AirQuality CY" in line and "данные на 08:00" in line)
    assert_true("official_line", "AQI 125" not in line)
    print("PASS official_line_keeps_category_metrics_source_city_and_time")


def test_current_air_does_not_become_tomorrow_guidance() -> None:
    from format_v2 import build_evening_format_v2, build_morning_format_v2

    evening = """<b>Кипр: погода на завтра (09.08.2026)</b>
✨ VayboMeter завтра: 7.0/10 — обычный день.
🏖 <b>Морские города</b>
Лимассол: 31/24 °C • ясно
———
💨 Ветер: 3 м/с • 1012 гПа
🏭 Воздух сейчас: официальный уровень 3/4 (высокий) • PM₂.₅ 12 / PM₁₀ 68 • наблюдение в Лимассоле • 🇨🇾 AirQuality CY • данные на 08:00
#Кипр #погода
"""
    evening_out = build_evening_format_v2("Кипр", evening)
    assert_true("current_vs_forecast", "официальный уровень 3/4" in evening_out)
    assert_true("current_vs_forecast", "пыль/дымка влияют" not in evening_out)
    assert_true("current_vs_forecast", "прогноз воздуха требует" not in evening_out)
    assert_true("current_vs_forecast", "😷" not in evening_out and "окна лучше держать" not in evening_out)

    morning = evening.replace("погода на завтра", "погода на сегодня").replace("✨ VayboMeter завтра: 7.0/10 — обычный день.\n", "")
    morning_out = build_morning_format_v2("Кипр", morning)
    assert_true("current_city_scope", "В Лимассоле воздух неидеален" in morning_out)
    assert_true("current_city_scope", "окна лучше держать" not in morning_out)
    print("PASS current_air_does_not_become_tomorrow_guidance")


def test_forecast_air_and_dust_evidence_are_separate() -> None:
    from format_v2 import build_evening_format_v2

    forecast = """<b>Кипр: погода на завтра (09.08.2026)</b>
✨ VayboMeter завтра: 7.0/10 — обычный день.
🏖 <b>Морские города</b>
Лимассол: 31/24 °C • ясно
———
💨 Ветер: 3 м/с • 1012 гПа
🏭 Прогноз воздуха: AQI 130 • PM₂.₅ 32 • PM₁₀ 68
#Кипр #погода
"""
    without_dust = build_evening_format_v2("Кипр", forecast)
    assert_true("forecast_air", "прогноз воздуха требует" in without_dust)
    assert_true("forecast_air", "😷 По прогнозу воздух неидеален" in without_dust)
    assert_true("forecast_air", "пыль/дымка влияют" not in without_dust)
    assert_true("forecast_air", "окна лучше держать" not in without_dust)

    with_dust = build_evening_format_v2(
        "Кипр",
        forecast.replace(
            "🏭 Прогноз воздуха:",
            "🌫 Видимость: завтра утром в Лимассоле (прогноз на 07:00) возможна сухая пылевая дымка; ориентируйтесь на фактическую дальность обзора.\n🏭 Прогноз воздуха:",
        ),
    )
    assert_true("forecast_dust", "пыль/дымка влияют" in with_dust)
    assert_true("forecast_dust", "В часы подтверждённой пылевой дымки окна лучше держать закрытыми" in with_dust)
    print("PASS forecast_air_and_dust_evidence_are_separate")


def test_city_summary_formatting() -> None:
    sys.modules.setdefault("imghdr", types.SimpleNamespace(what=lambda *_args, **_kwargs: None))
    import post_common

    old_env = os.environ.get("CY_AIR_BY_CITY")
    old_get = post_common.get_air_for_cities
    os.environ["CY_AIR_BY_CITY"] = "1"
    post_common.get_air_for_cities = lambda pairs: {
        "Limassol": {"src": "cy_official", "clean_label": "🟢 чисто"},
        "Nicosia": {"src": "cy_official", "clean_label": "🟡 PM₁₀", "pm25": 18, "pm10": 42, "dominant_pollutant": "PM10"},
        "Paralimni": {"src": "cy_official", "clean_label": "🟢 чисто"},
    }
    try:
        line = post_common._air_by_city_line(
            [
                ("Limassol", (34.707, 33.022)),
                ("Paralimni", (35.039, 33.982)),
                ("Nicosia", (35.170, 33.360)),
                ("Troodos", (34.916, 32.823)),
            ]
        )
        assert_true("city_summary", isinstance(line, str))
        assert_true("city_summary", "Воздух по городам:" in line)
        assert_true("city_summary", "Лимассол 🟢" in line)
        assert_true("city_summary", "Никосия 🟡 (PM₁₀ 42)" in line)
        assert_true("city_summary", "Паралимни 🟢" in line)
        assert_true("city_summary", "Troodos" not in line)
    finally:
        post_common.get_air_for_cities = old_get
        if old_env is None:
            os.environ.pop("CY_AIR_BY_CITY", None)
        else:
            os.environ["CY_AIR_BY_CITY"] = old_env
    print("PASS city_summary_formatting")


def test_format_v2_keeps_city_air_line() -> None:
    from format_v2 import build_format_v2

    legacy = "\n".join(
        [
            "<b>Кипр: погода на завтра (25.06.2026)</b>",
            "✨ VayboMeter завтра: 7.4/10 — хорошо.",
            "🏖 <b>Морские города</b>",
            "Лимассол: 30/24 °C • ясно",
            "———",
            "🏭 Воздух: AQI 25 (низкий) • PM₂.₅ 8 / PM₁₀ 28",
            "🏭 Воздух по городам: Лимассол 🟢 · Никосия 🟡 PM₂.₅ 18.",
            "🌅 Рассвет завтра: 05:37",
            "🌙 Растущая Луна в ♐ — спокойный ритм.",
            "#Кипр #погода #здоровье",
        ]
    )
    out = build_format_v2("Кипр", "evening", legacy)
    assert_true("format_v2_city_air", "🏭 Воздух по городам:" in out)
    assert_true("format_v2_city_air", "Лимассол 🟢 · Никосия 🟡 (PM₂.₅ 18)" in out)
    print("PASS format_v2_keeps_city_air_line")



# ───────────── NOAA SWPC observed 3-hour Kp source contract (offline) ─────────────
import json  # noqa: E402
import math  # noqa: E402
import tempfile  # noqa: E402
from datetime import datetime, timedelta, timezone  # noqa: E402

KP_NOW = datetime(2026, 10, 10, 12, 0, tzinfo=timezone.utc)
KP_NOW_TS = int(KP_NOW.timestamp())


def _kp_tag(hours_ago: float, *, minutes: int = 0) -> str:
    return (KP_NOW - timedelta(hours=hours_ago) + timedelta(minutes=minutes)).strftime("%Y-%m-%dT%H:%M:%S")


def _kp_rows(*pairs) -> list:
    """Current NOAA schema: objects with time_tag (3-hour interval start, UTC) and Kp."""
    return [{"time_tag": tag, "Kp": kp, "a_running": 7, "station_count": 8} for tag, kp in pairs]


class KpAirWorld:
    """Fixed clock, temp cache file and scripted air HTTP. No network, no real cache."""

    def __init__(self, responses=None, now_ts: int = KP_NOW_TS):
        self.responses = dict(responses or {})
        self.now_ts = now_ts
        self.urls: list = []

    def __enter__(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._saved = (air._now_ts, air._fetch_kp_data, air.KP_CACHE)
        air._now_ts = lambda: self.now_ts
        air.KP_CACHE = Path(self._tmp.name) / "kp.json"

        def fake_fetch(url, *args, **kwargs):
            self.urls.append(url)
            return self.responses.get(url)

        air._fetch_kp_data = fake_fetch
        return self

    def __exit__(self, *_exc):
        air._now_ts, air._fetch_kp_data, air.KP_CACHE = self._saved
        self._tmp.cleanup()


def test_kp_observed_values_are_exact_and_unrounded() -> None:
    for value, g_scale in ((0.0, None), (6.67, "G2"), (7.0, "G3"), (8.67, "G4")):
        obs = air.parse_noaa_kp_observed_3h(_kp_rows((_kp_tag(6), 2.0), (_kp_tag(3), value)), now_ts=KP_NOW_TS)
        assert_true("kp_exact", obs is not None and obs["kp"] == value, str(obs))
        assert_true("kp_src", obs["src"] == "swpc_observed_3h" and obs["age_min"] == 180, str(obs))
        assert_true("kp_g_scale", obs["g_scale"] == g_scale, str(obs))
    for value, g_scale in {4.99: None, 5.0: "G1", 6.0: "G2", 6.99: "G2", 8.0: "G4", 8.99: "G4", 9.0: "G5"}.items():
        assert_true("kp_g_boundary", air.kp_g_scale(value) == g_scale, f"{value} -> {air.kp_g_scale(value)}")
    print("PASS kp_observed_values_are_exact_and_unrounded")


def test_kp_invalid_values_and_timestamps_are_rejected() -> None:
    for bad in (float("nan"), "NaN", float("inf"), "Infinity", -0.1, 9.01, True, None, "", "abc", [7]):
        assert_true("kp_bad_value", air.parse_noaa_kp_observed_3h(_kp_rows((_kp_tag(3), bad)), now_ts=KP_NOW_TS) is None, repr(bad))
    newer_bad = _kp_rows((_kp_tag(3), 2.33), (_kp_tag(0), 12.0), (_kp_tag(0, minutes=-1), float("nan")))
    obs = air.parse_noaa_kp_observed_3h(newer_bad, now_ts=KP_NOW_TS)
    assert_true("kp_bad_never_clamped", obs is not None and obs["kp"] == 2.33, str(obs))
    for tag in (None, "", "   ", "not-a-date", 12345, "2026-13-40T99:00:00"):
        rows = [{"time_tag": tag, "Kp": 7.0}]
        assert_true("kp_bad_time", air._parse_noaa_time_tag(tag) is None, repr(tag))
        assert_true("kp_bad_time_fixed_clock", air.parse_noaa_kp_observed_3h(rows, now_ts=KP_NOW_TS) is None, repr(tag))
        # Against the real wall clock too: substituting "now" would make this row look fresh.
        assert_true("kp_bad_time_real_clock", air.parse_noaa_kp_observed_3h(rows) is None, repr(tag))
    for raw in ("2026-10-10T09:00:00", "2026-10-10 09:00:00.000", "2026-10-10T09:00:00Z", "2026-10-10T12:00:00+03:00"):
        assert_true("kp_time_utc", air._parse_noaa_time_tag(raw) == KP_NOW_TS - 3 * 3600, raw)
    assert_true("kp_zero_is_value", air._finite_kp(0) == 0.0 and not math.isnan(air._finite_kp("0.0")))
    print("PASS kp_invalid_values_and_timestamps_are_rejected")


def test_kp_future_stale_and_unsorted_rows() -> None:
    assert_true("kp_skew", air.KP_FUTURE_SKEW_SEC == 600)
    assert_true("kp_future", air.parse_noaa_kp_observed_3h(_kp_rows((_kp_tag(0, minutes=11), 7.0)), now_ts=KP_NOW_TS) is None)
    skewed = air.parse_noaa_kp_observed_3h(_kp_rows((_kp_tag(0, minutes=5), 7.0)), now_ts=KP_NOW_TS)
    assert_true("kp_skew_age_zero", skewed is not None and skewed["age_min"] == 0, str(skewed))
    assert_true("kp_max_age", air.KP_OBSERVED_MAX_AGE_SEC == 6 * 3600 + 30 * 60)
    assert_true("kp_stale", air.parse_noaa_kp_observed_3h(_kp_rows((_kp_tag(6, minutes=-31), 7.0)), now_ts=KP_NOW_TS) is None)
    fresh = air.parse_noaa_kp_observed_3h(_kp_rows((_kp_tag(6, minutes=-29), 7.0)), now_ts=KP_NOW_TS)
    assert_true("kp_fresh_edge", fresh is not None and fresh["age_min"] == 389, str(fresh))
    unsorted = _kp_rows((_kp_tag(3), 7.0), (_kp_tag(9), 2.0), (_kp_tag(6), 4.33))
    obs = air.parse_noaa_kp_observed_3h(unsorted, now_ts=KP_NOW_TS)
    assert_true("kp_newest_by_time", obs is not None and obs["kp"] == 7.0, str(obs))
    # Legacy table schema is read by header name, never by the last column (station_count).
    table = [["time_tag", "Kp", "a_running", "station_count"], [_kp_tag(3).replace("T", " "), "6.67", "80", "8"]]
    legacy = air.parse_noaa_kp_observed_3h(table, now_ts=KP_NOW_TS)
    assert_true("kp_legacy_header", legacy is not None and legacy["kp"] == 6.67, str(legacy))
    for data in (None, "text", 7, {"Kp": 7}, [1, 2], [], [{"time_tag": _kp_tag(3), "kp_index": 7}], [["time_tag", "a_running"], [_kp_tag(3), "7"]]):
        assert_true("kp_wrong_schema", air.parse_noaa_kp_observed_3h(data, now_ts=KP_NOW_TS) is None, repr(data))
    print("PASS kp_future_stale_and_unsorted_rows")


def test_kp_estimated_1m_keeps_its_own_provenance() -> None:
    estimate = [
        {"time_tag": _kp_tag(0, minutes=-2), "kp_index": 0, "estimated_kp": 0.0},
        {"time_tag": _kp_tag(0, minutes=-1), "kp_index": 7, "estimated_kp": 7.33},
    ]
    obs = air.parse_noaa_kp_estimated_1m(estimate, now_ts=KP_NOW_TS)
    assert_true("kp_est", obs is not None and obs["kp"] == 7.33 and obs["src"] == "swpc_estimated_1m", str(obs))
    zero = air.parse_noaa_kp_estimated_1m(estimate[:1], now_ts=KP_NOW_TS)
    assert_true("kp_est_zero", zero is not None and zero["kp"] == 0.0, str(zero))
    with KpAirWorld({air.KP_ESTIMATED_1M_URL: estimate}):
        result = air.get_kp()
        assert_true("kp_est_public_tuple", result == (7.33, "буря", KP_NOW_TS - 60, "swpc_estimated_1m"), str(result))
        assert_true("kp_est_not_observed", air.get_kp_observed_3h() is None)
        assert_true("kp_est_not_observed_cache", air.get_kp_observed_3h(live=False) is None)
    print("PASS kp_estimated_1m_keeps_its_own_provenance")


def test_kp_cache_provenance_newest_and_freshness() -> None:
    with KpAirWorld({air.KP_OBSERVED_3H_URL: _kp_rows((_kp_tag(3), 7.0))}) as world:
        assert_true("kp_get", air.get_kp() == (7.0, "буря", KP_NOW_TS - 3 * 3600, "swpc_observed_3h"))
        cached = json.loads(air.KP_CACHE.read_text(encoding="utf-8"))
        assert_true("kp_cache_shape", cached == {"schema": 2, "swpc_observed_3h": {"kp": 7.0, "ts": KP_NOW_TS - 3 * 3600}}, str(cached))
        world.responses = {}
        world.urls.clear()
        world.now_ts = KP_NOW_TS + 3600
        obs = air.get_kp_observed_3h(live=False)
        assert_true("kp_cache_only_no_fetch", world.urls == [], str(world.urls))
        assert_true("kp_cache_reuse", obs is not None and obs["src"] == "cache:swpc_observed_3h" and obs["age_min"] == 240, str(obs))
        # Newest observation wins; an older record never replaces it.
        older = {"kp": 2.0, "ts": KP_NOW_TS - 6 * 3600, "src": "swpc_observed_3h"}
        assert_true("kp_remember_older", air.remember_kp_observed_3h(older) is True)
        assert_true("kp_newest_kept", json.loads(air.KP_CACHE.read_text(encoding="utf-8"))["swpc_observed_3h"]["kp"] == 7.0)
        world.now_ts = KP_NOW_TS - 3 * 3600 + air.KP_OBSERVED_MAX_AGE_SEC + 60
        assert_true("kp_cache_expired", air.get_kp_observed_3h(live=False) is None)
        assert_true("kp_cache_expired_get", air.get_kp() == (None, "н/д", None, "n/d"))
    for refused in (
        None,
        {"kp": 7.0, "ts": KP_NOW_TS, "src": "swpc_estimated_1m"},
        {"kp": 7.0, "ts": KP_NOW_TS, "src": "cache:swpc_observed_3h"},
        {"kp": float("nan"), "ts": KP_NOW_TS, "src": "swpc_observed_3h"},
        {"kp": 7.0, "ts": True, "src": "swpc_observed_3h"},
    ):
        with KpAirWorld():
            assert_true("kp_remember_refused", air.remember_kp_observed_3h(refused) is False, repr(refused))
            assert_true("kp_remember_no_file", not air.KP_CACHE.exists(), repr(refused))
    with KpAirWorld():
        air.KP_CACHE.write_text(json.dumps({"kp": 1.0, "ts": KP_NOW_TS - 60, "src": "swpc_table"}), encoding="utf-8")
        assert_true("kp_legacy_cache_ignored", air.get_kp() == (None, "н/д", None, "n/d"))
    print("PASS kp_cache_provenance_newest_and_freshness")


def main() -> None:
    test_official_parse()
    test_official_levels_are_not_fabricated_aqi()
    test_official_priority_and_city_mapping()
    test_fallback_when_official_fails()
    test_stale_and_missing_observations_are_deterministic()
    test_official_line_keeps_category_metrics_source_city_and_time()
    test_city_summary_formatting()
    test_format_v2_keeps_city_air_line()
    test_current_air_does_not_become_tomorrow_guidance()
    test_forecast_air_and_dust_evidence_are_separate()
    test_kp_observed_values_are_exact_and_unrounded()
    test_kp_invalid_values_and_timestamps_are_rejected()
    test_kp_future_stale_and_unsorted_rows()
    test_kp_estimated_1m_keeps_its_own_provenance()
    test_kp_cache_provenance_newest_and_freshness()
    print("OK: Cyprus air-quality offline checks passed")


if __name__ == "__main__":
    main()
