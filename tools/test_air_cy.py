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
        "Ayia Napa": {"src": "cy_official", "clean_label": "🟢 чисто"},
    }
    try:
        line = post_common._air_by_city_line(
            [
                ("Limassol", (34.707, 33.022)),
                ("Ayia Napa", (34.988, 34.012)),
                ("Nicosia", (35.170, 33.360)),
                ("Troodos", (34.916, 32.823)),
            ]
        )
        assert_true("city_summary", isinstance(line, str))
        assert_true("city_summary", "Воздух по городам:" in line)
        assert_true("city_summary", "Лимассол 🟢" in line)
        assert_true("city_summary", "Никосия 🟡 (PM₁₀ 42)" in line)
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
    print("OK: Cyprus air-quality offline checks passed")


if __name__ == "__main__":
    main()
