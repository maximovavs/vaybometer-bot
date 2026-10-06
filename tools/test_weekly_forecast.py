#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Regression checks for Cyprus weekly VayboMeter forecast."""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import urllib.parse
from datetime import date
from html.parser import HTMLParser
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from PIL import Image  # type: ignore  # noqa: E402
import send_weekly_forecast as weekly_module  # noqa: E402
import weather as weather_module  # noqa: E402
import weekly_snapshot as snapshot_module  # noqa: E402
from weekly_cover import RENDERER_VERSION as WEEKLY_COVER_VERSION, render_weekly_cover  # noqa: E402
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
    ("Paralimni", (35.039, 33.982)),
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
    assert "🏭 Воздух сейчас" in text
    assert "Текущий снимок воздуха:" in text
    assert "🧲 Космопогода сейчас" in text
    assert "это текущий снимок, а не прогноз на всю неделю" in text
    assert "🌙 Луна и астроритм (интерпретация)" in text
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
    calls: list[tuple[float, float, str, str, str]] = []

    def fake_get_weekly_weather(
        lat: float,
        lon: float,
        *,
        start_date: str,
        end_date: str,
        tz_name: str,
    ) -> dict:
        calls.append((lat, lon, start_date, end_date, tz_name))
        return WEATHER

    weather_module = ModuleType("weather")
    weather_module.get_weekly_weather = fake_get_weekly_weather
    payload = _with_module(
        "weather",
        weather_module,
        lambda: _fetch_weather(date(2026, 7, 1)),
    )
    assert [(lat, lon) for lat, lon, _start, _end, _tz in calls] == [
        coords for _city, coords in EXPECTED_ISLAND_POINTS
    ]
    assert all(start == "2026-07-01" for _lat, _lon, start, _end, _tz in calls)
    assert all(end == "2026-07-07" for _lat, _lon, _start, end, _tz in calls)
    assert all(tz == "Asia/Nicosia" for _lat, _lon, _start, _end, tz in calls)
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



def test_weekly_cover_is_high_contrast_factual_projection() -> None:
    text = _base_text()
    with tempfile.TemporaryDirectory() as tmp:
        metadata = render_weekly_cover(
            text,
            start=date(2026, 7, 1),
            output_path=Path(tmp) / "weekly.png",
        )
        assert metadata["renderer_version"] == WEEKLY_COVER_VERSION
        assert metadata["main_fact"] in text
        assert metadata["weather_fact"] in text
        assert metadata["sea_fact"] in text
        with Image.open(metadata["path"]) as image:
            assert image.size == (1080, 1350)
            assert image.format == "PNG"
            assert image.info["renderer_version"] == WEEKLY_COVER_VERSION
            assert image.info["week_start"] == "2026-07-01"
            image.verify()



def _run_weekly_send_case(*, render_fails: bool = False, image_fails: bool = False) -> list[str]:
    events: list[str] = []
    old_token = os.environ.get("TELEGRAM_TOKEN")
    old_telegram = sys.modules.get("telegram")
    old_renderer = weekly_module.render_weekly_cover

    class _ParseMode:
        HTML = "HTML"

    class _Constants:
        ParseMode = _ParseMode

    class _FakeBot:
        def __init__(self, token: str):
            assert token == "weekly-test-token"

        async def send_photo(self, **kwargs):
            events.append("photo")
            assert kwargs["chat_id"] == -100123
            assert kwargs["caption"] == "Вайб недели: 01–07 июля"
            if image_fails:
                raise RuntimeError("synthetic image send failure")
            return object()

        async def send_message(self, **kwargs):
            events.append("text")
            assert kwargs["chat_id"] == -100123
            assert kwargs["text"] == "weekly text"
            assert kwargs["parse_mode"] == "HTML"
            return object()

    telegram_module = ModuleType("telegram")
    telegram_module.Bot = _FakeBot
    telegram_module.constants = _Constants

    with tempfile.TemporaryDirectory() as tmp:
        def fake_render(text: str, *, start: date, output_path: str | Path):
            events.append("cover")
            assert text == "weekly text"
            assert start == date(2026, 7, 1)
            if render_fails:
                raise RuntimeError("synthetic cover render failure")
            path = Path(tmp) / "weekly-cover.png"
            path.write_bytes(b"offline-cover")
            return {"path": str(path)}

        try:
            os.environ["TELEGRAM_TOKEN"] = "weekly-test-token"
            sys.modules["telegram"] = telegram_module
            weekly_module.render_weekly_cover = fake_render
            asyncio.run(weekly_module._send("weekly text", "-100123", date(2026, 7, 1)))
        finally:
            weekly_module.render_weekly_cover = old_renderer
            if old_telegram is None:
                sys.modules.pop("telegram", None)
            else:
                sys.modules["telegram"] = old_telegram
            if old_token is None:
                os.environ.pop("TELEGRAM_TOKEN", None)
            else:
                os.environ["TELEGRAM_TOKEN"] = old_token
    return events


def test_weekly_send_orders_cover_before_text_without_network() -> None:
    assert _run_weekly_send_case() == ["cover", "photo", "text"]


def test_weekly_send_survives_cover_render_failure() -> None:
    assert _run_weekly_send_case(render_fails=True) == ["cover", "text"]


def test_weekly_send_survives_image_send_failure() -> None:
    assert _run_weekly_send_case(image_fails=True) == ["cover", "photo", "text"]


def _weekly_api_fixture(dates: list[str]) -> dict:
    count = len(dates)
    return {
        "daily": {
            "time": dates,
            "temperature_2m_max": [30 + idx for idx in range(count)],
            "temperature_2m_min": [20 + idx for idx in range(count)],
            "weather_code": [0] * count,
            "precipitation_probability_max": [10] * count,
            "precipitation_sum": [0.0] * count,
            "wind_speed_10m_max": [5.0] * count,
            "wind_gusts_10m_max": [8.0] * count,
            "uv_index_max": [7.0] * count,
        },
        "daily_units": {
            "temperature_2m_max": "°C",
            "temperature_2m_min": "°C",
            "precipitation_probability_max": "%",
            "precipitation_sum": "mm",
            "wind_speed_10m_max": "m/s",
            "wind_gusts_10m_max": "m/s",
        },
    }


def test_weekly_source_requests_exact_range_and_units_without_network() -> None:
    captured: list[str] = []
    old_http = weather_module._http_get_json
    dates = [f"2026-07-{day:02d}" for day in range(6, 13)]

    def fake_http(url: str, timeout_sec: float) -> dict:
        assert timeout_sec == weather_module.TIMEOUT_SEC
        captured.append(url)
        return _weekly_api_fixture(dates)

    try:
        weather_module._http_get_json = fake_http
        payload = weather_module.get_weekly_weather(
            34.707,
            33.022,
            start_date="2026-07-06",
            end_date="2026-07-12",
            tz_name="Asia/Nicosia",
        )
    finally:
        weather_module._http_get_json = old_http

    assert len(captured) == 1
    parsed = urllib.parse.urlparse(captured[0])
    query = urllib.parse.parse_qs(parsed.query)
    assert query["start_date"] == ["2026-07-06"]
    assert query["end_date"] == ["2026-07-12"]
    assert "forecast_days" not in query
    assert query["wind_speed_unit"] == ["ms"]
    assert query["precipitation_unit"] == ["mm"]
    assert query["temperature_unit"] == ["celsius"]
    assert "precipitation_sum" in query["daily"][0]
    assert payload["_weekly_meta"]["coverage_complete"] is True
    assert payload["_weekly_meta"]["coverage_days"] == 7
    assert payload["_weekly_meta"]["normalized_units"]["wind_speed"] == "m/s"
    assert payload["_weekly_meta"]["normalized_units"]["precipitation"] == "mm"
    assert payload["_weekly_meta"]["source_daily_units"]["wind_speed_10m_max"] == "m/s"


def test_weekly_source_rejects_six_eight_and_wrong_dates() -> None:
    old_http = weather_module._http_get_json
    variants = [
        [f"2026-07-{day:02d}" for day in range(6, 12)],
        [f"2026-07-{day:02d}" for day in range(5, 13)],
        [f"2026-07-{day:02d}" for day in range(7, 14)],
    ]
    try:
        for dates in variants:
            weather_module._http_get_json = lambda url, timeout_sec, dates=dates: _weekly_api_fixture(dates)
            payload = weather_module.get_weekly_weather(
                34.707,
                33.022,
                start_date="2026-07-06",
                end_date="2026-07-12",
                tz_name="Asia/Nicosia",
            )
            assert payload["_weekly_meta"]["coverage_complete"] is False
    finally:
        weather_module._http_get_json = old_http


def test_weekly_partial_city_cannot_make_island_complete() -> None:
    dates = [f"2026-07-{day:02d}" for day in range(6, 13)]
    complete = _weekly_api_fixture(dates)
    complete["_weekly_meta"] = {
        "coverage_complete": True,
        "weather_code_system": "wmo",
        "normalized_units": {"wind_speed": "m/s", "precipitation": "mm"},
    }
    partial = _weekly_api_fixture(dates[:-1])
    partial["_weekly_meta"] = {
        "coverage_complete": False,
        "weather_code_system": "wmo",
        "normalized_units": {"wind_speed": "m/s", "precipitation": "mm"},
    }
    payload = {name: complete for name, _coords in EXPECTED_ISLAND_POINTS}
    payload["Troodos"] = partial
    metrics = weekly_module._weather_metrics_for_payload(payload, date(2026, 7, 6))
    assert metrics["coverage_complete"] is False
    text = build_weekly_forecast(
        date(2026, 7, 6),
        weather_payload=payload,
        air_data=AIR,
        sea_temps=[27.2, 28.1, 27.6],
        kp_tuple=KP,
        lunar_data=LUNAR,
        astro_events_paths=[Path("__missing_astro_events.json")],
    )
    assert "Полный прогноз на все 7 дней пока не собран" in text


def test_weekly_rows_do_not_fabricate_dates_without_daily_time() -> None:
    synthetic = {
        "daily": {
            "temperature_2m_max": [31, 31],
            "temperature_2m_min": [22, 22],
            "weathercode": [500, 500],
        }
    }
    assert weekly_module._daily_rows(synthetic, date(2026, 7, 6)) == []


def _snapshot_days_fixture(*, temp_shift: float = 0.0, rainy_days: int = 2, windy_days: int = 2, gust_peak: float = 12.0) -> list[dict]:
    from datetime import timedelta
    return [
        {"date":(date(2026,7,6)+timedelta(days=idx)).isoformat(),"tmax":30.0+temp_shift,"tmin":20.0+temp_shift,
         "wind":5.0,"gust":gust_peak if idx < windy_days else 7.0,"rainy":idx < rainy_days,"precip_sum":None}
        for idx in range(7)
    ]

def _snapshot_candidate_fixture(**kwargs) -> dict:
    return snapshot_module.build_snapshot_candidate(region="cyprus",week_start=date(2026,7,6),weather_days=_snapshot_days_fixture(**kwargs),
        weather_coverage_days=7,weather_coverage_complete=True,sea_temps=[27.0]*len(weekly_module.SEA_POINTS),
        expected_sea_samples=len(weekly_module.SEA_POINTS),generated_at_utc="2026-07-04T19:00:00Z")

def _authoritative_snapshot(candidate: dict, message_id: int = 101) -> dict:
    return snapshot_module.finalize_snapshot(candidate,message_id,published_at_utc="2026-07-04T19:05:00Z")

def _previous_candidate(**kwargs) -> dict:
    from datetime import timedelta
    days=[dict(day,date=(date(2026,6,29)+timedelta(days=i)).isoformat()) for i,day in enumerate(_snapshot_days_fixture(**kwargs))]
    return snapshot_module.build_snapshot_candidate(region="cyprus",week_start=date(2026,6,29),weather_days=days,
        weather_coverage_days=7,weather_coverage_complete=True,sea_temps=[26.0]*len(weekly_module.SEA_POINTS),
        expected_sea_samples=len(weekly_module.SEA_POINTS),generated_at_utc="2026-06-27T19:00:00Z")

def test_weekly_snapshot_serialization_and_validation() -> None:
    authoritative=_authoritative_snapshot(_snapshot_candidate_fixture())
    with tempfile.TemporaryDirectory() as tmp:
        path=Path(tmp)/"snapshot.json"; snapshot_module.write_snapshot_atomic(path,authoritative)
        loaded=snapshot_module.load_snapshot_file(path,region="cyprus",expected_week_start="2026-07-06")
    assert loaded==authoritative and loaded["thermal_midpoint_c"]==25.0

def test_weekly_snapshot_rejects_corrupt_wrong_region_week_and_incomplete() -> None:
    auth=_authoritative_snapshot(_snapshot_candidate_fixture())
    with tempfile.TemporaryDirectory() as tmp:
        path=Path(tmp)/"snapshot.json"; path.write_text("{broken",encoding="utf-8")
        assert snapshot_module.load_snapshot_file(path,region="cyprus",expected_week_start="2026-07-06") is None
    assert not snapshot_module.validate_snapshot(dict(auth,region="kld"),region="cyprus",expected_week_start="2026-07-06")
    assert not snapshot_module.validate_snapshot(auth,region="cyprus",expected_week_start="2026-06-29")
    assert not snapshot_module.validate_snapshot(dict(auth,weather_coverage_complete=False,weather_coverage_days=6),region="cyprus",expected_week_start="2026-07-06")

def test_weekly_snapshot_selects_exact_previous_and_earliest_canonical() -> None:
    first=_authoritative_snapshot(_previous_candidate(),201); late=_authoritative_snapshot(_previous_candidate(),202)
    selected=snapshot_module.select_earliest_valid_snapshot([
        {"created_at":"2026-07-04T19:00:00Z","payload":_authoritative_snapshot(_snapshot_candidate_fixture(),303)},
        {"created_at":"2026-06-28T10:00:00Z","payload":late},
        {"created_at":"2026-06-27T19:00:00Z","payload":first}],region="cyprus",expected_week_start="2026-06-29")
    assert selected["production_text_message_id"]==201

def test_weekly_delta_thresholds_and_conflict_rules() -> None:
    prev=_authoritative_snapshot(_previous_candidate())
    assert snapshot_module.derive_delta(_snapshot_candidate_fixture(temp_shift=2.0),prev,region="cyprus")["temperature"]["direction"]=="warmer"
    assert snapshot_module.derive_delta(_snapshot_candidate_fixture(temp_shift=-2.0),prev,region="cyprus")["temperature"]["direction"]=="colder"
    assert "temperature" not in snapshot_module.derive_delta(_snapshot_candidate_fixture(temp_shift=1.9),prev,region="cyprus")
    assert snapshot_module.derive_delta(_snapshot_candidate_fixture(rainy_days=4),prev,region="cyprus")["rain"]["direction"]=="wetter"
    assert snapshot_module.derive_delta(_snapshot_candidate_fixture(rainy_days=2),_authoritative_snapshot(_previous_candidate(rainy_days=4),402),region="cyprus")["rain"]["direction"]=="drier"
    assert snapshot_module.derive_delta(_snapshot_candidate_fixture(windy_days=4),prev,region="cyprus")["wind"]["direction"]=="windier"
    assert snapshot_module.derive_delta(_snapshot_candidate_fixture(windy_days=2,gust_peak=15.0),prev,region="cyprus")["wind"]["direction"]=="windier"
    conflict=snapshot_module.derive_delta(_snapshot_candidate_fixture(windy_days=2,gust_peak=16.0),_authoritative_snapshot(_previous_candidate(windy_days=4,gust_peak=12.0),403),region="cyprus")
    assert conflict["wind"]["direction"]=="mixed"

def test_weekly_delta_terminal_threshold_matrix() -> None:
    prev=_authoritative_snapshot(_previous_candidate())

    # +1 rainy day is below the material-change threshold.
    rain_neutral=snapshot_module.derive_delta(_snapshot_candidate_fixture(rainy_days=3),prev,region="cyprus")
    assert "rain" not in rain_neutral

    # Two fewer windy days is a deterministic calmer signal.
    wind_count_prev=_authoritative_snapshot(_previous_candidate(windy_days=4,gust_peak=12.0),406)
    wind_count=snapshot_module.derive_delta(_snapshot_candidate_fixture(windy_days=2,gust_peak=12.0),wind_count_prev,region="cyprus")
    assert wind_count["wind"]["direction"]=="calmer"

    # With the windy-day count unchanged, -3 m/s max gust is the fallback calmer threshold.
    gust_prev=_authoritative_snapshot(_previous_candidate(windy_days=2,gust_peak=15.0),407)
    gust=snapshot_module.derive_delta(_snapshot_candidate_fixture(windy_days=2,gust_peak=12.0),gust_prev,region="cyprus")
    assert gust["wind"]["direction"]=="calmer"

    # Complete sea-point coverage permits the <= -1.0 C colder direction.
    sea_prev=_authoritative_snapshot(_previous_candidate(),408)
    sea_colder=dict(_snapshot_candidate_fixture(),sea_mean_c=25.0,sea_min_c=25.0,sea_max_c=25.0)
    assert snapshot_module.derive_delta(sea_colder,sea_prev,region="cyprus")["sea"]["direction"]=="colder"


def test_weekly_sea_delta_requires_complete_point_coverage() -> None:
    prev=_authoritative_snapshot(_previous_candidate(),501); cur=_snapshot_candidate_fixture()
    assert snapshot_module.derive_delta(cur,prev,region="cyprus")["sea"]["direction"]=="warmer"
    assert "sea" not in snapshot_module.derive_delta(dict(cur,sea_sample_count=len(weekly_module.SEA_POINTS)-1),prev,region="cyprus")

def test_weekly_incomplete_current_is_not_snapshot_authority() -> None:
    partial=json.loads(json.dumps(WEATHER)); partial["daily"]["time"]=partial["daily"]["time"][:-1]
    for key,values in list(partial["daily"].items()):
        if key!="time" and isinstance(values,list): partial["daily"][key]=values[:-1]
    payload={city:partial for city,_coords in EXPECTED_ISLAND_POINTS}
    assert weekly_module._build_snapshot_candidate(date(2026,7,1),payload,[27.0]*len(weekly_module.SEA_POINTS)) is None

def _run_snapshot_authority_case(*, cover_fails=False, text_fails=False, production=True) -> bool:
    old_token=os.environ.get("TELEGRAM_TOKEN"); old_telegram=sys.modules.get("telegram"); old_renderer=weekly_module.render_weekly_cover
    class Message: message_id=777
    class ParseMode: HTML="HTML"
    class Constants: pass
    Constants.ParseMode=ParseMode
    class Bot:
        def __init__(self,token): pass
        async def send_photo(self,**kwargs): return object()
        async def send_message(self,**kwargs):
            if text_fails: raise RuntimeError("synthetic text failure")
            return Message()
    module=ModuleType("telegram"); module.Bot=Bot; module.constants=Constants
    with tempfile.TemporaryDirectory() as tmp:
        out=Path(tmp)/"snapshot.json"
        def fake_render(text: str, *, start: date, output_path: str | Path):
            if cover_fails: raise RuntimeError("synthetic cover failure")
            path=Path(tmp)/"cover.png"; path.write_bytes(b"fixture"); return {"path":str(path)}
        try:
            os.environ["TELEGRAM_TOKEN"]="test"; sys.modules["telegram"]=module; weekly_module.render_weekly_cover=fake_render
            try:
                asyncio.run(weekly_module._send("weekly text","-100123",date(2026,7,6),snapshot_candidate=_snapshot_candidate_fixture(),
                    production_snapshot_out=out,production_chat_id="-100123" if production else "-100999"))
            except RuntimeError: pass
            return out.exists()
        finally:
            weekly_module.render_weekly_cover=old_renderer
            if old_telegram is None: sys.modules.pop("telegram",None)
            else: sys.modules["telegram"]=old_telegram
            if old_token is None: os.environ.pop("TELEGRAM_TOKEN",None)
            else: os.environ["TELEGRAM_TOKEN"]=old_token

def test_weekly_snapshot_authority_follows_production_text_success() -> None:
    assert _run_snapshot_authority_case(cover_fails=True) is True
    assert _run_snapshot_authority_case(text_fails=True) is False
    assert _run_snapshot_authority_case(production=False) is False

def test_weekly_first_run_without_history_has_no_delta_block() -> None:
    text=build_weekly_forecast(date(2026,7,1),weather_payload=WEATHER,air_data=AIR,sea_temps=[27.2,28.1,27.6],kp_tuple=KP,lunar_data=LUNAR,
        astro_events_paths=[Path("__missing_astro_events.json")],delta_lines=[])
    assert "↔️ К прошлому недельному прогнозу" not in text

def test_weekly_delta_block_is_compact_and_forecast_labeled() -> None:
    text=build_weekly_forecast(date(2026,7,1),weather_payload=WEATHER,air_data=AIR,sea_temps=[27.2,28.1,27.6],kp_tuple=KP,lunar_data=LUNAR,
        astro_events_paths=[Path("__missing_astro_events.json")],delta_lines=["Температура: новый недельный прогноз заметно теплее.","Осадки: новый прогноз заметно суше."])
    assert "↔️ К прошлому недельному прогнозу" in text and "фактически была" not in text

def test_weekly_snapshot_workflow_contract_is_production_only_and_30_days() -> None:
    workflow=(ROOT/".github"/"workflows"/"weekly_forecast.yml").read_text("utf-8")
    assert 'if [ "${DRY_RUN:-}" = "true" ]; then' in workflow
    assert 'if [ "${labels[$i]}" = "production" ]' in workflow
    assert "--production-snapshot-out" in workflow and "retention-days: 30" in workflow
    assert "weekly-snapshot-cy-" in workflow and "canonical-exists" in workflow


def main() -> None:
    checks = (
        test_weekly_snapshot_serialization_and_validation,
        test_weekly_snapshot_rejects_corrupt_wrong_region_week_and_incomplete,
        test_weekly_snapshot_selects_exact_previous_and_earliest_canonical,
        test_weekly_delta_thresholds_and_conflict_rules,
        test_weekly_delta_terminal_threshold_matrix,
        test_weekly_sea_delta_requires_complete_point_coverage,
        test_weekly_incomplete_current_is_not_snapshot_authority,
        test_weekly_snapshot_authority_follows_production_text_success,
        test_weekly_first_run_without_history_has_no_delta_block,
        test_weekly_delta_block_is_compact_and_forecast_labeled,
        test_weekly_snapshot_workflow_contract_is_production_only_and_30_days,
        test_weekly_source_requests_exact_range_and_units_without_network,
        test_weekly_source_rejects_six_eight_and_wrong_dates,
        test_weekly_partial_city_cannot_make_island_complete,
        test_weekly_rows_do_not_fabricate_dates_without_daily_time,
        test_weekly_forecast_structure_without_optional_config,
        test_weekly_forecast_includes_curated_astro_events,
        test_weekly_forecast_keeps_stronger_kite_warning_for_high_gusts,
        test_weekly_weather_fetches_exact_island_points,
        test_weekly_weather_preserves_island_extremes,
        test_weekly_air_fetches_exact_island_points,
        test_weekly_air_preserves_worst_island_values,
        test_weekly_cover_is_high_contrast_factual_projection,
        test_weekly_send_orders_cover_before_text_without_network,
        test_weekly_send_survives_cover_render_failure,
        test_weekly_send_survives_image_send_failure,
    )
    for check in checks:
        check()
        print(f"PASS {check.__name__}")
    print(f"OK: {len(checks)} Cyprus weekly forecast checks passed")


if __name__ == "__main__":
    main()
