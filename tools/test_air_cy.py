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
    old_cache = air._CY_AIRQUALITY_CACHE
    air._CY_AIRQUALITY_CACHE = (time.time(), rows)
    try:
        official = air._src_cy_airquality_official(34.988, 34.012, city="Ayia Napa")
        assert_true("official_priority", official is not None)
        assert_true("official_priority", official["station"].startswith("Paralimni"))
        merged = air.merge_air_sources(
            official,
            {"aqi": 10, "pm10": 10, "src": "iqair"},
            {"aqi": 20, "pm10": 20, "src": "openmeteo"},
        )
        assert_true("official_priority", merged["src"] == "cy_official")
        assert_true("official_priority", merged["src_icon"] == "🇨🇾 AirQuality CY")
    finally:
        air._CY_AIRQUALITY_CACHE = old_cache
    print("PASS official_priority_and_city_mapping")


def test_fallback_when_official_fails() -> None:
    old_official = air._src_cy_airquality_official
    old_iqair = air._src_iqair
    old_openmeteo = air._src_openmeteo
    air._src_cy_airquality_official = lambda lat, lon, city=None: None
    air._src_iqair = lambda lat, lon: None
    air._src_openmeteo = lambda lat, lon: {"aqi": 33.0, "pm25": 6.0, "pm10": 18.0, "src": "openmeteo"}
    try:
        result = air.get_air(34.707, 33.022)
        assert_true("fallback", result["src"] == "openmeteo")
        assert_true("fallback", result["aqi"] == 33.0)
    finally:
        air._src_cy_airquality_official = old_official
        air._src_iqair = old_iqair
        air._src_openmeteo = old_openmeteo
    print("PASS fallback_when_official_fails")


def test_city_summary_formatting() -> None:
    sys.modules.setdefault("imghdr", types.SimpleNamespace(what=lambda *_args, **_kwargs: None))
    import post_common

    old_env = os.environ.get("CY_AIR_BY_CITY")
    old_get = post_common.get_air_for_cities
    os.environ["CY_AIR_BY_CITY"] = "1"
    post_common.get_air_for_cities = lambda pairs: {
        "Limassol": {"src": "cy_official", "clean_label": "🟢 чисто"},
        "Nicosia": {"src": "cy_official", "clean_label": "🟡 PM₁₀"},
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
        assert_true("city_summary", "Лимассол 🟢 чисто" in line)
        assert_true("city_summary", "Никосия 🟡 PM₁₀" in line)
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
            "<b>Кипр: погода на сегодня (25.06.2026)</b>",
            "Доброе утро! Теплее всего — Никосия (37°), прохладнее — Пафос (28°).",
            "☀️ <b>УФ-индекс 9 (Very High)</b>: SPF 50",
            "🏭 AQI 25 (низкий) • PM₂.₅ 8 / PM₁₀ 28",
            "🏙 Воздух по городам: Лимассол 🟢 чисто; Никосия 🟡 PM₁₀.",
            "✅ Сегодня: вода и завтрак.",
            "#Кипр #погода #здоровье",
        ]
    )
    out = build_format_v2("Кипр", "morning", legacy)
    assert_true("format_v2_city_air", "🏙 Воздух по городам:" in out)
    assert_true("format_v2_city_air", "Лимассол 🟢 чисто" in out)
    print("PASS format_v2_keeps_city_air_line")


def main() -> None:
    test_official_parse()
    test_official_priority_and_city_mapping()
    test_fallback_when_official_fails()
    test_city_summary_formatting()
    test_format_v2_keeps_city_air_line()
    print("OK: Cyprus air-quality offline checks passed")


if __name__ == "__main__":
    main()
