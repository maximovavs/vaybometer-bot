#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Regression checks for Cyprus weekly VayboMeter forecast."""
from __future__ import annotations

import json
import sys
import tempfile
from datetime import date
from html.parser import HTMLParser
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from send_weekly_forecast import (  # noqa: E402
    _aggregate_air_data,
    _fetch_air,
    _fetch_weather,
    _weather_metrics_for_payload,
    build_weekly_forecast,
)


WEATHER = {
    "daily": {
        "time": [
            "2026-07-01",
            "2026-07-02",
            "2026-07-03",
            "2026-07-04",
            "2026-07-05",
            "2026-07-06",
            "2026-07-07",
        ],
        "temperature_2m_max": [32, 34, 36, 35, 33, 31, 30],
        "temperature_2m_min": [24, 25, 26, 25, 24, 23, 23],
        "wind_speed_10m_max": [5, 6, 7, 6, 5, 5, 4],
        "wind_gusts_10m_max": [8, 9, 10, 8, 7, 7, 6],
        "precipitation_probability_max": [0, 5, 10, 10, 0, 0, 0],
        "weathercode": [0, 1, 1, 2, 1, 0, 0],
        "uv_index_max": [8, 9, 9, 8, 7, 7, 7],
    }
}

AIR = {"aqi": 125, "pm25": 20, "pm10": 69}
KP = (2.3, "спокойно", 123456, "fixture")
LUNAR = {
    "days": {
        "2026-07-01": {
            "phase_name": "Полнолуние",
            "percent": 99,
            "void_of_course": {"start": "01.07 19:13", "end": "01.07 21:33"},
        },
        "2026-07-03": {
            "phase_name": "Убывающая Луна",
            "percent": 92,
            "void_of_course": {"start": "03.07 16:15", "end": "04.07 00:00"},
        },
        "2026-07-07": {"phase_name": "Убывающая Луна", "percent": 75},
    }
}

FORBIDDEN = ("аварии", "чрезвычайные ситуации", "операции лучше отложить", "воздушном пространстве")
EXPECTED_ISLAND_POINTS = [
    ("Limassol", (34.707, 33.022)),
    ("Pafos", (34.776, 32.424)),
    ("Ayia Napa", (34.988, 34.012)),
    ("Larnaca", (34.916, 33.624)),
    ("Nicosia", (35.170, 33.360)),
    ("Troodos", (34.916, 32.823)),
]


class _Parser(HTMLParser):
    pass


def _with_module(name: str, module: ModuleType, callback):
    missing = object()
    previous = sys.modules.get(name, missing)
    try:
        sys.modules[name] = module
        return callback()
    finally:
        if previous is missing:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = previous


def _base_text(extra_paths: list[Path] | None = None) -> str:
    return build_weekly_forecast(
        date(2026, 7, 1),
        weather_payload=WEATHER,
        air_data=AIR,
        sea_temps=[27.2, 28.1, 27.6],
        kp_tuple=KP,
        lunar_data=LUNAR,
        astro_events_paths=extra_paths or [Path("__missing_astro_events.json")],
    )


def test_weekly_forecast_structure_without_optional_config() -> None:
    text = _base_text()
    assert "🗓 Вайб недели" in text
    assert "✨ Главный фон недели" in text
    assert "🌿 Смысл недели" in text
    assert text.index("✨ Главный фон недели") < text.index("🌿 Смысл недели") < text.index("🌦 Погода")
    assert "🌦 Погода" in text
    assert "🌊 Море" in text
    assert "Море: средняя вода" not in text
    assert "Средняя вода" in text
    assert "🏄 Вода и спорт" in text
    assert "SUP:" in text
    assert "Кайт/винг:" in text
    assert "Серф:" in text
    assert "SUP: короткие утренние окна в защищённых бухтах." in text
    assert "идеально" not in text.lower()
    assert "Кайт/винг: рабочие окна только для уверенных; порывы проверять по споту." in text
    assert "Серф: зависит от фактической волны; скорее не главный сценарий недели." in text
    assert "🏭 Воздух" in text
    assert "🧲 Космопогода" in text
    assert "сильных бурь не видно" in text
    assert "🌙 Луна" in text
    assert "✅ Как прожить неделю" in text
    assert "Воздух неидеален" in text
    assert "🌕" in text and "Полнолуние" in text
    assert "01.07 01.07" not in text
    assert "03.07 03.07" not in text
    assert "01.07 19:13–21:33" in text
    assert "03.07 16:15–04.07 00:00" in text
    assert "море планировать утром или ближе к закату." in text
    assert text.splitlines()[-1] == "#Кипр #вайбнедели #погода #море #астропогода"
    assert not any(phrase in text.lower() for phrase in FORBIDDEN)
    _Parser().feed(text)


def test_weekly_forecast_includes_curated_astro_events() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "astro_events_monthly.json"
        path.write_text(
            json.dumps(
                [
                    {
                        "date": "2026-07-07",
                        "title": "Нептун разворачивается ретроградно",
                        "tone": "эмоциональная чувствительность, переоценка целей",
                        "advice": "не спешить с обещаниями, проверять факты",
                    }
                ],
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        text = _base_text([path])
    assert "Нептун разворачивается ретроградно" in text
    assert "проверять факты" in text


def test_weekly_forecast_keeps_stronger_kite_warning_for_high_gusts() -> None:
    weather = json.loads(json.dumps(WEATHER))
    weather["daily"]["wind_gusts_10m_max"] = [16, 17, 18, 16, 17, 18, 16]
    text = build_weekly_forecast(
        date(2026, 7, 1),
        weather_payload=weather,
        air_data=AIR,
        sea_temps=[27.2, 28.1, 27.6],
        kp_tuple=KP,
        lunar_data=LUNAR,
        astro_events_paths=[Path("__missing_astro_events.json")],
    )
    assert "Кайт/винг: только опытным; порывы могут быть резкими." in text
    assert text.splitlines()[-1] == "#Кипр #вайбнедели #погода #море #астропогода"


def test_weekly_weather_fetches_exact_island_points() -> None:
    calls: list[tuple[float, float]] = []

    def fake_get_weather(lat: float, lon: float) -> dict:
        calls.append((lat, lon))
        return WEATHER

    weather_module = ModuleType("weather")
    weather_module.get_weather = fake_get_weather
    payload = _with_module("weather", weather_module, _fetch_weather)
    assert calls == [coords for _city, coords in EXPECTED_ISLAND_POINTS]
    assert list(payload) == [city for city, _coords in EXPECTED_ISLAND_POINTS]


def test_weekly_weather_preserves_island_extremes() -> None:
    def city_weather(*, tmax=30, tmin=22, wind=5, gust=8, uv=5, rain=0, code=0) -> dict:
        return {
            "daily": {
                "time": [f"2026-07-{day:02d}" for day in range(1, 8)],
                "temperature_2m_max": [tmax] * 7,
                "temperature_2m_min": [tmin] * 7,
                "wind_speed_10m_max": [wind] * 7,
                "wind_gusts_10m_max": [gust] * 7,
                "precipitation_probability_max": [rain] * 7,
                "weathercode": [code] * 7,
                "uv_index_max": [uv] * 7,
            }
        }

    payload = {
        "Limassol": city_weather(tmax=41),
        "Pafos": city_weather(tmin=16),
        "Ayia Napa": city_weather(wind=17),
        "Larnaca": city_weather(gust=24),
        "Nicosia": city_weather(uv=12),
        "Troodos": city_weather(code=61),
    }
    metrics = _weather_metrics_for_payload(payload, date(2026, 7, 1))
    assert metrics["tmax_max"] == 41
    assert metrics["tmin_min"] == 16
    assert metrics["wind_max"] == 17
    assert metrics["gust_max"] == 24
    assert metrics["uv_max"] == 12
    assert metrics["rain"] is True


def test_weekly_air_fetches_exact_island_points() -> None:
    calls: list[list[tuple[str, tuple[float, float]]]] = []

    def fake_get_air_for_cities(points):
        calls.append(list(points))
        return {"Limassol": AIR}

    air_module = ModuleType("air")
    air_module.get_air_for_cities = fake_get_air_for_cities
    payload = _with_module("air", air_module, _fetch_air)
    assert calls == [EXPECTED_ISLAND_POINTS]
    assert payload == {"Limassol": AIR}


def test_weekly_air_preserves_worst_island_values() -> None:
    aggregated = _aggregate_air_data(
        {
            "Limassol": {"aqi": 145, "pm25": 5, "pm10": 8},
            "Pafos": {"aqi": 20, "pm25": 37, "pm10": 9},
            "Nicosia": {"aqi": 30, "pm25": 7, "pm10": 91},
            "Troodos": {"aqi": "н/д", "pm25": None, "pm10": None},
        }
    )
    assert aggregated == {"aqi": 145.0, "pm25": 37.0, "pm10": 91.0}


def main() -> None:
    checks = (
        test_weekly_forecast_structure_without_optional_config,
        test_weekly_forecast_includes_curated_astro_events,
        test_weekly_forecast_keeps_stronger_kite_warning_for_high_gusts,
        test_weekly_weather_fetches_exact_island_points,
        test_weekly_weather_preserves_island_extremes,
        test_weekly_air_fetches_exact_island_points,
        test_weekly_air_preserves_worst_island_values,
    )
    for check in checks:
        check()
        print(f"PASS {check.__name__}")
    print(f"OK: {len(checks)} Cyprus weekly forecast checks passed")


if __name__ == "__main__":
    main()
