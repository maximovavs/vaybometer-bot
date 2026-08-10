#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Regression checks for Cyprus morning FORMAT_V2 post polish."""
from __future__ import annotations

import asyncio
import datetime as dt
import json
import os
import re
import shutil
import sys
import tempfile
import types
from datetime import date
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

telegram_stub = types.ModuleType("telegram")
telegram_stub.Bot = object
telegram_stub.constants = types.SimpleNamespace(ParseMode=types.SimpleNamespace(HTML="HTML"))
sys.modules.setdefault("telegram", telegram_stub)

pendulum_stub = types.ModuleType("pendulum")
pendulum_stub.DateTime = object
sys.modules.setdefault("pendulum", pendulum_stub)

imghdr_stub = types.ModuleType("imghdr")
imghdr_stub.what = lambda *args, **kwargs: None
sys.modules.setdefault("imghdr", imghdr_stub)

import format_v2 as format_v2_module  # noqa: E402
from format_v2 import build_evening_format_v2, build_format_v2, build_morning_format_v2  # noqa: E402
import cyprus_visual_dedup  # noqa: E402
import image_prompt_cy_scene as cy_scene_prompt  # noqa: E402
import post_common as post_common_module  # noqa: E402
import safe_test_post as safe_module  # noqa: E402
import utils as utils_module  # noqa: E402
import weather as weather_module  # noqa: E402
from image_prompt_cy_scene import build_cyprus_scene_prompt_with_metadata  # noqa: E402
from post_safety import sanitize_post_text  # noqa: E402
from safe_test_post import (  # noqa: E402
    _apply_astro_cleanup,
    _apply_cyprus_morning_raw_context,
    _apply_cyprus_sensor_cleanup,
    _build_safe_test_image,
    _cy_image_receipt_path,
    _cy_image_caption,
    _cy_text_receipt_path,
    _cy_write_image_diagnostics,
    _send_telegram_text_chunks,
    cy_morning_delivery_path,
    cy_morning_has_valid_production_receipt,
    cy_morning_image_phase_for_result,
    cy_morning_load_delivery_receipt,
    cy_morning_maybe_write_delivery_receipt,
    cy_morning_target_date,
    has_valid_cy_text_delivery,
    is_valid_cy_image_receipt,
    is_valid_cy_text_receipt,
    finalize_hashtags_at_end,
    _cyprus_main_nuance,
    _cyprus_evening_score_line,
    _cyprus_feels_line,
    _cyprus_score_line,
    _cyprus_smart_plan_line,
    _inject_morning_score,
    _inject_morning_smart_plan,
    _insert_main_nuance,
)
from visibility_context import (  # noqa: E402
    build_cyprus_visibility_line,
    classify_visibility_values,
    dew_point_spread_c,
    get_cyprus_visibility_context,
    normalize_visibility_m,
    visibility_air_penalty,
    visibility_condition_from_text,
)
from weather import save_cyprus_visibility_diagnostics  # noqa: E402


MORNING_WITH_SEA = """<b>Кипр: погода, море, бури, Луна (27.06.2026)</b>
👋 Доброе утро! Теплее всего — Никосия (37°), прохладнее — Пафос (30°).
☀️ <b>УФ-индекс 9 (Very High)</b>: тень 11–16.
🏭 AQI 58 (умеренный) • PM₂.₅ 14 / PM₁₀ 31 • 📟 0.08 μSv/h • 🌿 пыльца: низко
💨 Ветер: 3.0 м/с • 🔹 1009 гПа →
Море у Ларнаки: вода 28°C, волна спокойная.
🧲 Космопогода: Kp 2.0 (спокойно) • 🌬️ v 420 км/с
🌇 Закат сегодня: 20:05
✅ Сегодня: вода, SPF, тень.
#Кипр #погода #здоровье
"""


MORNING_NO_SEA = """<b>Кипр: погода на сегодня (27.06.2026)</b>
👋 Доброе утро! Теплее всего — Никосия (37°), прохладнее — Пафос (30°).
🏭 AQI 42 (низкий) • PM₂.₅ 9 / PM₁₀ 18
💨 Ветер: 3.0 м/с • 🔹 1009 гПа →
✅ Сегодня: вода, SPF.
#Кипр #погода #здоровье
"""

MORNING_WITH_COASTAL_ROWS = """<b>Кипр: погода, море, бури, Луна (27.06.2026)</b>
👋 Доброе утро! Теплее всего — Никосия (37°), прохладнее — Пафос (30°).
☀️ <b>УФ-индекс 9 (Very High)</b>: тень 11–16.
🏭 AQI 58 (умеренный) • PM₂.₅ 14 / PM₁₀ 31
💨 Ветер: 3.0 м/с • 🔹 1009 гПа →
Ларнака: 34/25 °C • ☀️ ясно • 🌊 28
Лимассол: 35/26 °C • ☀️ ясно • 🌊 26
Айя-Напа: 35/26 °C • ☀️ ясно • 🌊 28
Пафос: 31/23 °C • ☀️ ясно • 🌊 26
🌇 Закат сегодня: 20:05
✅ Сегодня: вода, SPF, тень.
#Кипр #погода #здоровье
"""

MORNING_NON_MARINE_NUMBERS = """<b>Кипр: погода, жара и море (27.06.2026)</b>
👋 Доброе утро! Теплее всего — Никосия (37°), прохладнее — Пафос (30°).
☀️ <b>УФ-индекс 9 (Very High)</b>: тень 11–16.
🏭 AQI 58 (умеренный) • PM₂.₅ 14 / PM₁₀ 31
💨 Ветер 6 м/с, давление 1009 гПа.
🌇 Закат сегодня: 20:05
✨ 96% освещённости — Луна яркая.
✅ Сегодня: вода, SPF, тень.
#Кипр #погода #здоровье
"""

MORNING_WINTER_WITH_SEA = """<b>Кипр: погода и море (15.01.2026)</b>
👋 Доброе утро! Теплее всего — Ларнака (19°), прохладнее — Тродос (8°).
🏭 AQI 42 (низкий) • PM₂.₅ 9 / PM₁₀ 18
💨 Ветер: 3.0 м/с • 🔹 1014 гПа →
Море у Ларнаки: вода 19°C, волна спокойная.
🌇 Закат сегодня: 17:02
✅ Сегодня: прогулка у моря, слой от ветра.
#Кипр #погода #здоровье
"""

MORNING_WINTER_NON_MARINE_NUMBERS = """<b>Кипр: погода и море (15.01.2026)</b>
👋 Доброе утро! Теплее всего — Ларнака (19°), прохладнее — Тродос (8°).
🏭 AQI 42 (низкий) • PM₂.₅ 9 / PM₁₀ 18
💨 Ветер: 3.0 м/с • 🔹 1014 гПа →
🌇 Закат сегодня: 19:05
✅ Сегодня: прогулка у моря, слой от ветра.
#Кипр #погода #здоровье
"""

REAL_RAW_MORNING_WITH_SEA_ASTRO = """<b>Кипр: погода, жара и море (27.06.2026)</b>
👋 Доброе утро! Теплее всего — Никосия (37°), прохладнее — Пафос (30°).
☀️ <b>УФ-индекс 9 (Very High)</b>: тень 11–16.
🏭 AQI 58 (умеренный) • PM₂.₅ 14 / PM₁₀ 31
💨 Ветер: 3.0 м/с • 🔹 1009 гПа →
Ларнака: 34/25 °C • ☀️ ясно • 🌊 28
Лимассол: 35/26 °C • ☀️ ясно • 🌊 26
Айя-Напа: 35/26 °C • ☀️ ясно • 🌊 28
Пафос: 31/23 °C • ☀️ ясно • 🌊 26
🌇 Закат сегодня: 20:05
🌕 Почти полная Луна в ♒ — 96% освещённости.
💚 В плюсе: планы, восстановление.
⚫️ VoC: 08:20–10:10.
✅ Сегодня: вода, SPF, тень.
#Кипр #погода #здоровье
"""

REAL_LEGACY_MORNING_WITHOUT_SEA_ASTRO = """<b>Кипр: погода, жара и море (27.06.2026)</b>
👋 Доброе утро! Теплее всего — Никосия (37°), прохладнее — Пафос (30°).
☀️ <b>УФ-индекс 9 (Very High)</b>: тень 11–16.
🏭 AQI 58 (умеренный) • PM₂.₅ 14 / PM₁₀ 31
💨 Ветер: 3.0 м/с • 🔹 1009 гПа →
🌇 Закат сегодня: 20:05
✅ Сегодня: вода, SPF, тень.
#Кипр #погода #здоровье
"""

MORNING_POOR_AIR = """<b>Кипр: погода на сегодня (27.06.2026)</b>
👋 Доброе утро! Теплее всего — Никосия (37°), прохладнее — Пафос (30°).
🏭 AQI 112 (умеренный) • PM₂.₅ 24 / PM₁₀ 63
💨 Ветер: 3.0 м/с • 🔹 1009 гПа →
✅ Сегодня: вода, SPF.
#Кипр #погода #здоровье
"""


MORNING_FULL_MOON = """<b>Кипр: погода на сегодня (27.06.2026)</b>
👋 Доброе утро! Теплее всего — Никосия (32°), прохладнее — Тродос (24°).
🏭 AQI 42 (низкий) • PM₂.₅ 9 / PM₁₀ 18
💨 Ветер: 3.0 м/с • 🔹 1009 гПа →
🌇 Закат сегодня: 20:05
Полнолуние в ♑ — пик эмоций и результатов.
✨ 100% освещённости — Луна яркая.
✅ Общий фон: благоприятный, но без перегруза.
💚 В плюсе: завершение, восстановление.
⚫️ VoC: 08:20–10:10.
✅ Сегодня: вода, SPF.
#Кипр #погода #здоровье
"""


MORNING_SOURCE_ONLY = """<b>Кипр: погода на сегодня (09.08.2026)</b>
👋 Доброе утро! Теплее всего — Никосия (29°), прохладнее — Лимассол (28°).
☀️ <b>УФ-индекс 9 (Very High)</b>: тень 11–16.
🏭 AQI 42 (низкий) • PM₂.₅ 9 / PM₁₀ 18
✅ Сегодня: вода, SPF, тень.
#Кипр #погода #здоровье
"""


def _with_temp_delivery_dir(callback) -> None:
    old_dir = os.environ.get("CY_MORNING_DELIVERY_DIR")
    old_text_dir = os.environ.get("CY_TEXT_DELIVERY_DIR")
    old_image_dir = os.environ.get("CY_IMAGE_DELIVERY_DIR")
    old_diag_dir = os.environ.get("CY_IMAGE_DIAGNOSTICS_DIR")
    old_health_dir = os.environ.get("CY_IMAGE_PROVIDER_HEALTH_DIR")
    old_run = os.environ.get("GITHUB_RUN_ID")
    old_attempt = os.environ.get("GITHUB_RUN_ATTEMPT")
    old_schedule = os.environ.get("GITHUB_EVENT_SCHEDULE")
    with tempfile.TemporaryDirectory() as tmp:
        try:
            os.environ["CY_MORNING_DELIVERY_DIR"] = tmp
            os.environ["CY_TEXT_DELIVERY_DIR"] = str(Path(tmp) / "cy_text_delivery")
            os.environ["CY_IMAGE_DELIVERY_DIR"] = str(Path(tmp) / "cy_image_delivery")
            os.environ["CY_IMAGE_DIAGNOSTICS_DIR"] = str(Path(tmp) / "cy_image_diagnostics")
            os.environ["CY_IMAGE_PROVIDER_HEALTH_DIR"] = str(Path(tmp) / "cy_image_provider_health")
            os.environ["GITHUB_RUN_ID"] = "fixture-run"
            os.environ["GITHUB_RUN_ATTEMPT"] = "2"
            callback(Path(tmp))
        finally:
            if old_dir is None:
                os.environ.pop("CY_MORNING_DELIVERY_DIR", None)
            else:
                os.environ["CY_MORNING_DELIVERY_DIR"] = old_dir
            if old_text_dir is None:
                os.environ.pop("CY_TEXT_DELIVERY_DIR", None)
            else:
                os.environ["CY_TEXT_DELIVERY_DIR"] = old_text_dir
            if old_image_dir is None:
                os.environ.pop("CY_IMAGE_DELIVERY_DIR", None)
            else:
                os.environ["CY_IMAGE_DELIVERY_DIR"] = old_image_dir
            if old_diag_dir is None:
                os.environ.pop("CY_IMAGE_DIAGNOSTICS_DIR", None)
            else:
                os.environ["CY_IMAGE_DIAGNOSTICS_DIR"] = old_diag_dir
            if old_health_dir is None:
                os.environ.pop("CY_IMAGE_PROVIDER_HEALTH_DIR", None)
            else:
                os.environ["CY_IMAGE_PROVIDER_HEALTH_DIR"] = old_health_dir
            if old_run is None:
                os.environ.pop("GITHUB_RUN_ID", None)
            else:
                os.environ["GITHUB_RUN_ID"] = old_run
            if old_attempt is None:
                os.environ.pop("GITHUB_RUN_ATTEMPT", None)
            else:
                os.environ["GITHUB_RUN_ATTEMPT"] = old_attempt
            if old_schedule is None:
                os.environ.pop("GITHUB_EVENT_SCHEDULE", None)
            else:
                os.environ["GITHUB_EVENT_SCHEDULE"] = old_schedule


def _write_fixture_receipt(target_date: str, event_schedule: str) -> Path:
    os.environ["GITHUB_EVENT_SCHEDULE"] = event_schedule
    receipt = cy_morning_maybe_write_delivery_receipt(
        target_date=target_date,
        chat_type="production",
        telegram_message_ids=[12345],
        text_chunk_count=1,
        sent=True,
        event_schedule=event_schedule,
    )
    assert receipt is not None
    return receipt


def _write_canonical_text_receipt(target_date: str, post_type: str = "morning", ids: list[int] | None = None) -> Path:
    payload = {
        "target_date": target_date,
        "post_type": post_type,
        "chat_type": "production",
        "telegram_message_ids": ids or [12345],
        "text_chunk_count": 1,
        "run_id": "fixture-run",
        "run_attempt": "2",
        "sent_at_utc": "2026-07-06T01:05:00Z",
    }
    path = _cy_text_receipt_path(target_date, post_type)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def _write_image_receipt(target_date: str, post_type: str = "morning", message_id: int = 777) -> Path:
    payload = {
        "target_date": target_date,
        "post_type": post_type,
        "chat_type": "production",
        "telegram_message_id": message_id,
        "sha256": "a" * 64,
        "perceptual_hash": "b" * 16,
        "selected_scene": "coastal_promenade",
        "style_name": "fixture",
        "cache_key": "fixture",
        "sent_at_utc": "2026-07-06T01:06:00Z",
    }
    path = _cy_image_receipt_path(target_date, post_type)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def _h3_sea_line(text: str) -> str:
    sea_lines = [line.strip() for line in str(text or "").splitlines() if line.strip().startswith("🌊 Море:")]
    assert len(sea_lines) == 1, sea_lines
    return sea_lines[0]


def _assert_h3_factual_sea_line(line: str) -> None:
    assert line.startswith("🌊 Море:")
    for marker in ("лучше", "11:00", "18:30"):
        assert marker not in line, f"actionable timing leaked into factual sea line: {line}"


def cy_morning_adds_concise_sea_block_when_available() -> None:
    text = build_morning_format_v2("Кипр", MORNING_WITH_SEA)
    sea_line = _h3_sea_line(text)
    assert sea_line == "🌊 Море: вода 28°C; волна спокойная."
    _assert_h3_factual_sea_line(sea_line)
    assert "🏭 Воздух: AQI 58 (умеренный) • PM₂.₅ 14 / PM₁₀ 31 • 🌿 пыльца: низкая" in text
    assert "📟" not in text
    assert "🌿 пыльца" in text


def cy_morning_source_rows_use_city_formatter_sst_and_preserve_evening() -> None:
    target_date = date(2026, 6, 27)
    calls: list[tuple[str, float, float, bool, date | None]] = []
    old_city_detail_line = post_common_module._city_detail_line

    def fake_city_detail_line(city, la, lo, _tz_obj, include_sst, target_date=None):
        calls.append((city, la, lo, include_sst, target_date))
        temperatures = {"Лимассол": 26.0, "Ларнака": 28.0}
        sst = temperatures[city]
        return 34.0, f"<b>{city}</b>: 34/25 °C • ясно • 🌊 {sst:.0f}"

    pairs = [
        ("Лимассол", (34.68, 33.04)),
        ("Ларнака", (34.92, 33.63)),
    ]
    try:
        post_common_module._city_detail_line = fake_city_detail_line
        rows = post_common_module._morning_sea_city_lines(
            pairs,
            types.SimpleNamespace(name="Asia/Nicosia"),
            target_date=target_date,
        )
        assert [line.split(":", 1)[0] for line in rows] == ["<b>Лимассол</b>", "<b>Ларнака</b>"]
        assert calls == [
            ("Лимассол", 34.68, 33.04, True, target_date),
            ("Ларнака", 34.92, 33.63, True, target_date),
        ]

        raw_morning = MORNING_NO_SEA.replace(
            "✅ Сегодня:",
            "\n".join(rows) + "\n✅ Сегодня:",
        )
        morning = build_morning_format_v2("Кипр", raw_morning)
        assert _h3_sea_line(morning) == "🌊 Море: средняя вода 27°C."
        _assert_h3_factual_sea_line(_h3_sea_line(morning))

        raw_evening = """<b>Кипр: погода на завтра (28.06.2026)</b>
🏖 <b>Морские города</b>
{rows}
———
✅ Завтра: море утром.
#Кипр #погода
""".format(rows="\n".join(rows))
        evening = build_evening_format_v2("Кипр", raw_evening)
        assert evening.index("<b>Лимассол</b>") < evening.index("<b>Ларнака</b>")

        post_common_module._city_detail_line = lambda *_args, **_kwargs: (None, None)
        assert post_common_module._morning_sea_city_lines(
            pairs,
            types.SimpleNamespace(name="Asia/Nicosia"),
            target_date=target_date,
        ) == []
        fallback = build_morning_format_v2("Кипр", MORNING_NO_SEA)
        assert "данные о температуре воды обновляются" in fallback
    finally:
        post_common_module._city_detail_line = old_city_detail_line


class _ForecastFixtureDateTime(dt.datetime):
    def in_tz(self, _tz):
        return self

    def format(self, pattern: str) -> str:
        translated = pattern.replace("DD", "%d").replace("MM", "%m").replace("YYYY", "%Y")
        translated = translated.replace("HH", "%H").replace("mm", "%M")
        return self.strftime(translated)

    def add(self, *, days: int = 0, hours: int = 0, minutes: int = 0):
        value = self + dt.timedelta(days=days, hours=hours, minutes=minutes)
        return _ForecastFixtureDateTime(
            value.year,
            value.month,
            value.day,
            value.hour,
            value.minute,
            value.second,
            value.microsecond,
        )


def _replace_attrs(target, replacements: dict[str, object], callback):
    missing = object()
    previous = {name: getattr(target, name, missing) for name in replacements}
    try:
        for name, value in replacements.items():
            setattr(target, name, value)
        return callback()
    finally:
        for name, value in previous.items():
            if value is missing:
                delattr(target, name)
            else:
                setattr(target, name, value)


def _with_forecast_clock(callback):
    fixed_today = _ForecastFixtureDateTime(2026, 8, 9)

    def parse(value, tz=None):
        del tz
        parsed = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is not None:
            parsed = parsed.replace(tzinfo=None)
        return _ForecastFixtureDateTime(
            parsed.year,
            parsed.month,
            parsed.day,
            parsed.hour,
            parsed.minute,
            parsed.second,
            parsed.microsecond,
        )

    def make_datetime(year, month, day, hour=0, minute=0, second=0, tz=None):
        del tz
        return _ForecastFixtureDateTime(year, month, day, hour, minute, second)

    return _replace_attrs(
        post_common_module.pendulum,
        {
            "parse": parse,
            "datetime": make_datetime,
            "today": lambda _tz=None: fixed_today,
        },
        callback,
    )


def _forecast_payload(
    *,
    today_high: float,
    today_low: float,
    tomorrow_high: float,
    tomorrow_low: float,
) -> dict:
    return {
        "daily": {
            "time": ["2026-08-10", "not-a-date", "2026-08-09"],
            "temperature_2m_max": [tomorrow_high, 99.0, today_high],
            "temperature_2m_min": [tomorrow_low, 88.0, today_low],
            "weathercode": [95, 71, 0],
            "uv_index_max": [4.0, 99.0, 9.0],
        },
        "hourly": {
            "time": [
                "2026-08-10T06:00",
                "not-an-hour",
                "2026-08-09T06:00",
                "2026-08-09T12:00",
                "2026-08-10T12:00",
            ],
            "windspeed_10m": [18.0, 360.0, 7.2, 10.8, 36.0],
            "winddirection_10m": [0.0, 270.0, 170.0, 180.0, 10.0],
            "surface_pressure": [990.0, 800.0, 1011.0, 1012.0, 989.0],
            "windgusts_10m": [50.4, 360.0, 14.4, 18.0, 72.0],
        },
        "current": {
            "windspeed": 180.0,
            "winddirection": 270.0,
            "pressure": 777.0,
        },
    }


def _build_aligned_forecast_messages() -> tuple[str, str, list[tuple[str, date]]]:
    region_weather = _forecast_payload(
        today_high=29.0,
        today_low=22.0,
        tomorrow_high=42.0,
        tomorrow_low=31.0,
    )
    city_weather = {
        (1.0, 2.0): _forecast_payload(
            today_high=28.0,
            today_low=23.0,
            tomorrow_high=40.0,
            tomorrow_low=30.0,
        ),
        (3.0, 4.0): _forecast_payload(
            today_high=29.0,
            today_low=24.0,
            tomorrow_high=42.0,
            tomorrow_low=31.0,
        ),
    }
    context_calls: list[tuple[str, date]] = []

    def fake_get_weather(lat, lon, *args, **kwargs):
        del args, kwargs
        if (lat, lon) == (post_common_module.CY_LAT, post_common_module.CY_LON):
            return region_weather
        return city_weather[(float(lat), float(lon))]

    def fake_visibility_context(_weather, *, post_type, target_date, **_kwargs):
        context_calls.append((post_type, target_date))
        return types.SimpleNamespace()

    def fake_storm_today(*_args, **_kwargs):
        context_calls.append(("storm_morning", date(2026, 8, 9)))
        return {"warning": False}

    def fake_storm_tomorrow(*_args, **_kwargs):
        context_calls.append(("storm_evening", date(2026, 8, 10)))
        return {"warning": False}

    replacements = {
        "get_weather": fake_get_weather,
        "get_fact": lambda *_args, **_kwargs: "",
        "storm_flags_for_today": fake_storm_today,
        "storm_flags_for_tomorrow": fake_storm_tomorrow,
        "get_air": lambda *_args, **_kwargs: {},
        "get_cyprus_visibility_context": fake_visibility_context,
        "build_cyprus_visibility_line": lambda *_args, **_kwargs: None,
        "save_cyprus_visibility_diagnostics": lambda *_args, **_kwargs: None,
        "sun_line_for_mode": lambda *_args, **_kwargs: None,
        "_morning_combo_air_radiation_pollen": lambda *_args, **_kwargs: None,
        "_air_by_city_line": lambda *_args, **_kwargs: None,
        "_cyprus_quake_line_for_morning": lambda *_args, **_kwargs: None,
        "get_solar_wind": lambda *_args, **_kwargs: {},
        "get_sst_cached": lambda *_args, **_kwargs: 27.0,
        "_water_highlights": lambda *_args, **_kwargs: None,
        "build_astro_section": lambda *_args, **_kwargs: "",
        "USE_WORLD_KP": False,
    }

    def build() -> tuple[str, str, list[tuple[str, date]]]:
        tz_obj = types.SimpleNamespace(name="Asia/Nicosia")
        morning = post_common_module.build_message(
            region_name="Кипр",
            sea_label="Морские города",
            sea_cities=[("Limassol", (1.0, 2.0))],
            other_label="Континентальные города",
            other_cities=[("Nicosia", (3.0, 4.0))],
            tz=tz_obj,
            mode="morning",
        )
        evening = post_common_module.build_message(
            region_name="Кипр",
            sea_label="Морские города",
            sea_cities=[("Limassol", (1.0, 2.0))],
            other_label="Континентальные города",
            other_cities=[("Nicosia", (3.0, 4.0))],
            tz=tz_obj,
            mode="evening",
        )
        return morning, evening, context_calls

    return _with_forecast_clock(
        lambda: _replace_attrs(post_common_module, replacements, build)
    )


def cy_morning_uses_today_for_raw_format_score_feels_and_plan() -> None:
    raw_morning, _raw_evening, context_calls = _build_aligned_forecast_messages()
    assert "погода на сегодня (09.08.2026)" in raw_morning
    assert "Теплее всего — Никосия (29°)" in raw_morning
    assert "прохладнее — Лимассол (28°)" in raw_morning
    assert "<b>Лимассол</b>: 28/23 °C" in raw_morning
    assert "☀️ ясно" in raw_morning
    assert "💨 3.0 м/с (Ю) • порывы 5" in raw_morning
    assert "1012 гПа ↑" in raw_morning
    assert "🌊 27" in raw_morning
    assert "УФ-индекс 9 (Very High)" in raw_morning
    assert "40/30" not in raw_morning
    assert "42/31" not in raw_morning
    assert "порывы 20" not in raw_morning
    assert ("morning", date(2026, 8, 9)) in context_calls
    assert ("storm_morning", date(2026, 8, 9)) in context_calls

    formatted = build_morning_format_v2("Кипр", raw_morning)
    assert "🌡 Теплее всего — Никосия (29°), прохладнее — Лимассол (28°)" in formatted
    assert "40/30" not in formatted and "42/31" not in formatted

    score = _cyprus_score_line(formatted)
    feels = _cyprus_feels_line(formatted)
    plan = _cyprus_smart_plan_line(formatted)
    assert "сильная жара" not in score and "жара" not in score
    assert "очень тепло в Никосии" in feels and "жарко" not in feels
    assert plan == "✅ План: SPF 50, вода с собой; полдень провести в тени; прогулка утром или ближе к закату."


def cy_evening_keeps_tomorrow_city_forecast() -> None:
    _raw_morning, raw_evening, context_calls = _build_aligned_forecast_messages()
    assert "погода на завтра (10.08.2026)" in raw_evening
    assert "<b>Лимассол</b>: 40/30 °C" in raw_evening
    assert "<b>Никосия</b>: 42/31 °C" in raw_evening
    assert "⛈ гроза" in raw_evening
    assert "💨 10.0 м/с (С) • порывы 20" in raw_evening
    assert "989 гПа ↓" in raw_evening
    assert ("evening", date(2026, 8, 10)) in context_calls


def cy_city_forecast_omits_row_when_target_daily_date_is_missing() -> None:
    payload = {
        "daily": {
            "time": ["2026-08-10"],
            "temperature_2m_max": [40.0],
            "temperature_2m_min": [30.0],
            "weathercode": [95],
        },
        "hourly": {
            "time": ["2026-08-10T12:00"],
            "windspeed_10m": [36.0],
            "surface_pressure": [989.0],
            "windgusts_10m": [72.0],
        },
        "current": {"temperature": 35.0, "windspeed": 180.0, "pressure": 777.0},
    }

    def run() -> None:
        result = _replace_attrs(
            post_common_module,
            {"get_weather": lambda *_args, **_kwargs: payload},
            lambda: post_common_module._city_detail_line(
                "Limassol",
                1.0,
                2.0,
                types.SimpleNamespace(name="Asia/Nicosia"),
                include_sst=False,
                target_date=date(2026, 8, 9),
            ),
        )
        assert result == (None, None)

    _with_forecast_clock(run)


def cy_city_forecast_does_not_shift_incomplete_or_malformed_arrays() -> None:
    payload = {
        "daily": {
            "time": ["bad-daily-time", "2026-08-09"],
            "temperature_2m_max": [99.0, 29.0],
            "temperature_2m_min": [18.0],
            "weathercode": [95, 0],
        },
        "hourly": {
            "time": ["bad-hourly-time", "2026-08-09T12:00"],
            "windspeed_10m": [360.0, 10.8],
            "winddirection_10m": [270.0],
            "surface_pressure": [800.0, 1012.0],
            "windgusts_10m": [360.0, 18.0],
        },
    }

    def run() -> None:
        _tmax, line = _replace_attrs(
            post_common_module,
            {"get_weather": lambda *_args, **_kwargs: payload},
            lambda: post_common_module._city_detail_line(
                "Limassol",
                1.0,
                2.0,
                types.SimpleNamespace(name="Asia/Nicosia"),
                include_sst=False,
                target_date=date(2026, 8, 9),
            ),
        )
        assert line is not None
        assert "29 °C" in line and "29/18" not in line
        assert "☀️ ясно" in line and "⛈ гроза" not in line
        assert "💨 3.0 м/с" in line and "270" not in line
        assert "порывы 5" in line and "порывы 100" not in line
        assert "1012 гПа" in line and "800 гПа" not in line

    _with_forecast_clock(run)


def cy_city_forecast_never_uses_current_for_missing_target_hourly_date() -> None:
    payload = {
        "daily": {
            "time": ["2026-08-09"],
            "temperature_2m_max": [29.0],
            "temperature_2m_min": [23.0],
            "weathercode": [0],
        },
        "hourly": {
            "time": ["2026-08-10T12:00"],
            "windspeed_10m": [36.0],
            "surface_pressure": [989.0],
            "windgusts_10m": [72.0],
        },
        "current": {"windspeed": 180.0, "winddirection": 270.0, "pressure": 777.0},
    }

    def run() -> None:
        _tmax, line = _replace_attrs(
            post_common_module,
            {"get_weather": lambda *_args, **_kwargs: payload},
            lambda: post_common_module._city_detail_line(
                "Limassol",
                1.0,
                2.0,
                types.SimpleNamespace(name="Asia/Nicosia"),
                include_sst=False,
                target_date=date(2026, 8, 9),
            ),
        )
        assert line == "<b>Лимассол</b>: 29/23 °C • ☀️ ясно"
        assert "180.0" not in line and "777" not in line and "10.0 м/с" not in line

    _with_forecast_clock(run)


def _format_v2_source_line(payload: dict, date_s: str = "09.08.2026") -> str:
    return _replace_attrs(
        weather_module,
        {"get_weather": lambda *_args, **_kwargs: payload},
        lambda: format_v2_module._source_wind_pressure_line(date_s),
    )


def cy_weather_attempts_request_only_sea_level_pressure() -> None:
    assert len(weather_module.ATTEMPTS) == 5
    for attempt in weather_module.ATTEMPTS:
        assert "pressure_msl" in attempt.hourly
        assert "surface_pressure" not in attempt.hourly
        if attempt.current_mode == "current":
            assert attempt.current_fields
            assert "pressure_msl" in attempt.current_fields
            assert "surface_pressure" not in attempt.current_fields


def cy_post_common_prefers_sea_level_pressure_with_surface_fallback() -> None:
    target = date(2026, 8, 9)
    tz_obj = types.SimpleNamespace(name="Asia/Nicosia")

    def metrics(payload: dict):
        return _with_forecast_clock(
            lambda: post_common_module._city_header_metrics_for_date(payload, tz_obj, target)
        )

    preferred = metrics(
        {
            "hourly": {
                "time": ["2026-08-09T06:00", "2026-08-09T12:00"],
                "pressure_msl": [1000.0, 1002.0],
                "surface_pressure": [900.0, 899.0],
            }
        }
    )
    assert preferred[2:4] == (1002, "↑")

    legacy = metrics(
        {
            "hourly": {
                "time": ["2026-08-09T06:00", "2026-08-09T12:00"],
                "surface_pressure": [1010.0, 1008.0],
            }
        }
    )
    assert legacy[2:4] == (1008, "↓")


def cy_format_v2_prefers_sea_level_pressure_with_surface_fallback() -> None:
    preferred = _format_v2_source_line(
        {
            "hourly": {
                "time": ["2026-08-09T06:00", "2026-08-09T12:00"],
                "pressure_msl": [1000.0, 1002.0],
                "surface_pressure": [900.0, 899.0],
            }
        }
    )
    assert preferred == "🔹 1002 гПа ↑"

    legacy = _format_v2_source_line(
        {
            "hourly": {
                "time": ["2026-08-09T06:00", "2026-08-09T12:00"],
                "surface_pressure": [1010.0, 1008.0],
            }
        }
    )
    assert legacy == "🔹 1008 гПа ↓"


def cy_utils_pressure_trend_supports_sea_level_and_surface_pressure() -> None:
    assert utils_module.pressure_trend(
        {
            "hourly": {
                "pressure_msl": [1000.0, 1003.0],
                "surface_pressure": [1000.0, 997.0],
            }
        }
    ) == "↑"
    assert utils_module.pressure_trend(
        {"hourly": {"surface_pressure": [1000.0, 997.0]}}
    ) == "↓"


def cy_format_v2_source_uses_only_exact_target_date_hourly_values() -> None:
    payload = {
        "hourly": {
            "time": [
                "2026-08-09T06:00",
                "not-an-hour",
                "2026-08-09T12:00",
                "2026-08-10T12:00",
            ],
            "windspeed_10m": [7.2, 360.0, 10.8, 36.0],
            "winddirection_10m": [170.0, 270.0, 180.0, 10.0],
            "surface_pressure": [1011.0, 800.0, 1012.0, 989.0],
            "windgusts_10m": [14.4, 360.0, 18.0, 72.0],
        },
        "current": {
            "windspeed": 180.0,
            "windgusts": 54.0,
            "winddirection": 270.0,
            "pressure": 777.0,
        },
    }
    line = _format_v2_source_line(payload)
    assert "💨 Ветер: 3.0 м/с (Ю)" in line
    assert "порывы до 5 м/с" in line
    assert "1012 гПа ↑" in line
    assert "10.0 м/с" not in line and "20 м/с" not in line
    assert "50.0 м/с" not in line and "15 м/с" not in line and "777 гПа" not in line
    formatted = _replace_attrs(
        weather_module,
        {"get_weather": lambda *_args, **_kwargs: payload},
        lambda: build_morning_format_v2("Кипр", MORNING_SOURCE_ONLY),
    )
    assert line in formatted


def cy_format_v2_source_missing_target_date_never_uses_tomorrow_or_current() -> None:
    payload = {
        "hourly": {
            "time": ["2026-08-10T06:00", "2026-08-10T12:00"],
            "windspeed_10m": [18.0, 36.0],
            "winddirection_10m": [0.0, 10.0],
            "surface_pressure": [990.0, 989.0],
            "windgusts_10m": [50.4, 72.0],
        },
        "current": {
            "windspeed": 180.0,
            "windgusts": 54.0,
            "winddirection": 270.0,
            "pressure": 777.0,
        },
    }
    assert _format_v2_source_line(payload) == ""
    assert _replace_attrs(
        weather_module,
        {
            "get_weather": lambda *_args, **_kwargs: (_ for _ in ()).throw(
                AssertionError("malformed title date reached weather source")
            )
        },
        lambda: format_v2_module._source_wind_pressure_line("not-a-date"),
    ) == ""


def cy_format_v2_source_current_only_omits_wind_and_pressure() -> None:
    payload = {
        "current": {
            "windspeed": 180.0,
            "windgusts": 54.0,
            "winddirection": 270.0,
            "pressure": 777.0,
        }
    }
    assert _format_v2_source_line(payload) == ""


def cy_format_v2_source_incomplete_arrays_emit_only_aligned_fields() -> None:
    payload = {
        "hourly": {
            "time": ["2026-08-09T06:00", "2026-08-09T12:00"],
            "windspeed_10m": [180.0],
            "winddirection_10m": [],
            "surface_pressure": [1011.0, 1012.0],
            "windgusts_10m": [14.4],
        },
        "current": {
            "windspeed": 180.0,
            "windgusts": 54.0,
            "winddirection": 270.0,
            "pressure": 777.0,
        },
    }
    line = _format_v2_source_line(payload)
    assert line == "💨 Порывы до 4 м/с • 🔹 1012 гПа ↑"
    assert "50.0 м/с" not in line and "15 м/с" not in line and "777 гПа" not in line


def cy_format_v2_source_malformed_timestamps_do_not_shift_arrays() -> None:
    payload = {
        "hourly": {
            "time": ["not-an-hour", "2026-08-09T12:00"],
            "windspeed_10m": [180.0, 10.8],
            "winddirection_10m": [270.0],
            "surface_pressure": [777.0, 1012.0],
            "windgusts_10m": [54.0, 18.0],
        },
        "current": {
            "windspeed": 180.0,
            "windgusts": 54.0,
            "winddirection": 270.0,
            "pressure": 777.0,
        },
    }
    line = _format_v2_source_line(payload)
    assert line == "💨 Ветер: 3.0 м/с • порывы до 5 м/с • 🔹 1012 гПа →"
    assert "(З)" not in line and "50.0 м/с" not in line and "15 м/с" not in line and "777 гПа" not in line


def cy_morning_format_v2_current_sentinels_do_not_change_downstream() -> None:
    tomorrow_hourly = {
        "time": ["2026-08-10T06:00", "2026-08-10T12:00"],
        "windspeed_10m": [18.0, 36.0],
        "winddirection_10m": [0.0, 10.0],
        "surface_pressure": [990.0, 989.0],
        "windgusts_10m": [50.4, 72.0],
    }
    with_current = {
        "hourly": tomorrow_hourly,
        "current": {
            "windspeed": 180.0,
            "windgusts": 54.0,
            "winddirection": 270.0,
            "pressure": 777.0,
        },
    }
    without_current = {"hourly": tomorrow_hourly, "current": {}}

    def build(payload: dict) -> str:
        return _replace_attrs(
            weather_module,
            {"get_weather": lambda *_args, **_kwargs: payload},
            lambda: build_morning_format_v2("Кипр", MORNING_SOURCE_ONLY),
        )

    sentinel_text = build(with_current)
    baseline_text = build(without_current)
    assert sentinel_text == baseline_text
    assert "50.0 м/с" not in sentinel_text
    assert "15 м/с" not in sentinel_text
    assert "777 гПа" not in sentinel_text
    assert _cyprus_score_line(sentinel_text) == _cyprus_score_line(baseline_text)
    assert _cyprus_feels_line(sentinel_text) == _cyprus_feels_line(baseline_text)
    assert _cyprus_smart_plan_line(sentinel_text) == _cyprus_smart_plan_line(baseline_text)
    assert _cyprus_score_line(sentinel_text)
    assert _cyprus_feels_line(sentinel_text)
    assert _cyprus_smart_plan_line(sentinel_text)


def cy_evening_format_v2_preserves_tomorrow_city_values() -> None:
    _raw_morning, raw_evening, _context_calls = _build_aligned_forecast_messages()
    formatted = build_evening_format_v2("Кипр", raw_evening)
    assert "<b>🌅 Кипр завтра (10.08.2026)</b>" in formatted
    assert "<b>Лимассол</b>: 40/30 °C" in formatted
    assert "<b>Никосия</b>: 42/31 °C" in formatted
    assert "💨 10.0 м/с (С) • порывы 20" in formatted
    assert "989 гПа ↓" in formatted
    assert "50.0 м/с" not in formatted and "15 м/с" not in formatted and "777 гПа" not in formatted


def cy_morning_averages_coastal_sea_rows() -> None:
    text = build_morning_format_v2("Кипр", MORNING_WITH_COASTAL_ROWS)
    sea_line = _h3_sea_line(text)
    assert sea_line == "🌊 Море: средняя вода 27°C."
    _assert_h3_factual_sea_line(sea_line)
    assert "🌊 Море: вода 20°C" not in text


def cy_morning_adds_sea_fallback_when_unavailable() -> None:
    text = build_morning_format_v2("Кипр", MORNING_NO_SEA)
    sea_line = _h3_sea_line(text)
    assert sea_line == "🌊 Море: данные о температуре воды обновляются."
    _assert_h3_factual_sea_line(sea_line)


def cy_morning_rejects_non_marine_numbers_for_sea() -> None:
    text = build_morning_format_v2("Кипр", MORNING_NON_MARINE_NUMBERS)
    sea_line = _h3_sea_line(text)
    assert sea_line == "🌊 Море: данные о температуре воды обновляются."
    _assert_h3_factual_sea_line(sea_line)
    assert "🌊 Море: вода 20°C" not in text
    assert "🌊 Море: вода 31°C" not in text
    assert "🌊 Море: вода 96°C" not in text


def cy_morning_accepts_winter_explicit_sea_temperature() -> None:
    text = build_morning_format_v2("Кипр", MORNING_WINTER_WITH_SEA)
    sea_line = _h3_sea_line(text)
    assert sea_line == "🌊 Море: вода 19°C; волна спокойная."
    _assert_h3_factual_sea_line(sea_line)


def cy_morning_winter_sunset_time_is_not_sea_temperature() -> None:
    text = build_morning_format_v2("Кипр", MORNING_WINTER_NON_MARINE_NUMBERS)
    sea_line = _h3_sea_line(text)
    assert sea_line == "🌊 Море: данные о температуре воды обновляются."
    _assert_h3_factual_sea_line(sea_line)
    assert "🌊 Море: вода 19°C" not in text
    assert "18:30" not in sea_line


def cy_morning_preserves_full_moon_line_without_illumination_duplicate() -> None:
    text = build_morning_format_v2("Кипр", MORNING_FULL_MOON)
    assert "🌕 Полнолуние в ♑ — 100% освещённости." in text
    assert "✨ 100% освещённости" not in text
    assert "✅ Общий фон: благоприятный, но без перегруза." in text
    assert "💚 В плюсе: завершение, восстановление." in text
    assert "⚫️ VoC: 08:20–10:10." in text
    assert text.index("🌇 Закат сегодня: 20:05") < text.index("🌕 Полнолуние")
    assert text.index("🌕 Полнолуние") < text.index("✅ План:")


def cy_morning_poor_air_adds_health_recommendation() -> None:
    text = build_morning_format_v2("Кипр", MORNING_POOR_AIR)
    assert "😷 Воздух неидеален: чувствительным людям лучше сократить интенсивную активность на улице" in text
    assert "окна лучше держать закрытыми" not in text


def cy_morning_recent_safecast_elevated_is_omitted() -> None:
    old_file = os.environ.get("CY_SAFECAST_FILE")
    old_age = os.environ.get("CY_SAFECAST_MAX_AGE_HOURS")
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "safecast_cy.json"
        path.write_text(json.dumps({"radiation_usvh": 0.23, "pm25": 9, "pm10": 18}), encoding="utf-8")
        try:
            os.environ["CY_SAFECAST_FILE"] = str(path)
            os.environ["CY_SAFECAST_MAX_AGE_HOURS"] = "24"
            text = build_morning_format_v2("Кипр", MORNING_NO_SEA)
        finally:
            if old_file is None:
                os.environ.pop("CY_SAFECAST_FILE", None)
            else:
                os.environ["CY_SAFECAST_FILE"] = old_file
            if old_age is None:
                os.environ.pop("CY_SAFECAST_MAX_AGE_HOURS", None)
            else:
                os.environ["CY_SAFECAST_MAX_AGE_HOURS"] = old_age
    assert "🧪" not in text
    assert "Частный датчик" not in text
    assert "Safecast CY" not in text


def cy_morning_hashtags_are_final_without_editorial_tail() -> None:
    built = build_morning_format_v2("Кипр", MORNING_WITH_SEA)
    assert "💬 Настрой" not in built
    assert "💬 По ощущениям" not in built
    dirty = built + "\n#Кипр #погода #здоровье #Никосия #Тродос\n\n"
    text = finalize_hashtags_at_end(
        dirty,
        canonical_hashtags="#Кипр #погода #здоровье #Никосия #Тродос",
    )
    lines = [line for line in text.splitlines() if line.strip()]
    assert lines[-1] == "#Кипр #погода #здоровье #Никосия #Тродос"
    assert text.count("#Кипр #погода #здоровье #Никосия #Тродос") == 1
    assert _cy_image_caption(
        "morning",
        "2026-07-14",
        test_label=True,
        current_date=date(2026, 7, 14),
    ) == "🧪 Визуальный вайб сегодняшнего дня на Кипре 🌊"
    assert "PM₂.₅" in text


def cy_morning_real_safe_path_restores_sea_and_astro_from_raw() -> None:
    legacy_result = sanitize_post_text(REAL_LEGACY_MORNING_WITHOUT_SEA_ASTRO)
    text = build_morning_format_v2("Кипр", legacy_result.text)
    text = _apply_astro_cleanup(text)
    text = _apply_cyprus_morning_raw_context(text, REAL_RAW_MORNING_WITH_SEA_ASTRO, legacy_result.text, "morning")
    text = _apply_cyprus_sensor_cleanup(text)
    text = sanitize_post_text(text).text

    sea_line = _h3_sea_line(text)
    assert sea_line == "🌊 Море: средняя вода 27°C."
    _assert_h3_factual_sea_line(sea_line)
    assert "🌊 Море: вода 20°C" not in text
    assert "☀️ <b>Солнце, Луна и ритм дня</b>" in text
    assert "🌇 Закат сегодня: 20:05" in text
    assert "Почти полная Луна" in text
    assert "96% освещённости" in text
    assert "💚 В плюсе: планы, восстановление." in text
    assert text.splitlines()[-1] == "#Кипр #погода #здоровье"


def cy_morning_image_failure_still_sends_text_chunks() -> None:
    async def _run() -> tuple[dict[str, object], list[dict[str, object]], list[int]]:
        world_old = sys.modules.get("world_en")
        imagegen_old = sys.modules.get("world_en.imagegen")
        world_stub = types.ModuleType("world_en")
        imagegen_stub = types.ModuleType("world_en.imagegen")

        def _fail_image(*_args, **_kwargs):
            raise RuntimeError("fixture image backend failure")

        imagegen_stub.generate_astro_image = _fail_image
        world_stub.imagegen = imagegen_stub
        sys.modules["world_en"] = world_stub
        sys.modules["world_en.imagegen"] = imagegen_stub
        try:
            image_result = await _build_safe_test_image(
                REAL_LEGACY_MORNING_WITHOUT_SEA_ASTRO,
                "morning",
                generate_image=True,
                send_image_to_test=False,
                send_image_to_chat=False,
                image_chat_id=None,
            )
        finally:
            if world_old is None:
                sys.modules.pop("world_en", None)
            else:
                sys.modules["world_en"] = world_old
            if imagegen_old is None:
                sys.modules.pop("world_en.imagegen", None)
            else:
                sys.modules["world_en.imagegen"] = imagegen_old

        class FakeBot:
            def __init__(self) -> None:
                self.messages: list[dict[str, object]] = []

            async def send_message(self, **kwargs):
                self.messages.append(kwargs)
                return types.SimpleNamespace(message_id=9000 + len(self.messages))

        bot = FakeBot()
        message_ids = await _send_telegram_text_chunks(
            bot,
            chat_id=123,
            chunks=["<b>Кипр сегодня</b>\n✅ План: текст отправлен."],
            add_test_label=False,
        )
        return image_result, bot.messages, message_ids

    image_result, messages, message_ids = asyncio.run(_run())
    assert image_result["result"] == "failed_non_fatal"
    assert image_result["error_type"] == "RuntimeError"
    assert len(messages) == 1
    assert messages[0]["chat_id"] == 123
    assert message_ids == [9001]


def cy_morning_recovery_publishes_then_delayed_primary_skips_by_receipt() -> None:
    def _case(_tmp: Path) -> None:
        target_date = "2026-07-06"
        assert not cy_morning_has_valid_production_receipt(target_date)
        receipt = _write_fixture_receipt(target_date, "15 3 * * *")
        assert receipt == cy_morning_delivery_path(target_date)
        assert cy_morning_has_valid_production_receipt(target_date)
        data = cy_morning_load_delivery_receipt(target_date)
        assert data is not None
        assert data["event_schedule"] == "15 3 * * *"
        assert data["chat_type"] == "production"

    _with_temp_delivery_dir(_case)


def cy_morning_primary_publishes_then_recovery_skips_by_receipt() -> None:
    def _case(_tmp: Path) -> None:
        target_date = "2026-07-06"
        _write_fixture_receipt(target_date, "0 1 * * *")
        assert cy_morning_has_valid_production_receipt(target_date)
        data = cy_morning_load_delivery_receipt(target_date)
        assert data is not None
        assert data["event_schedule"] == "0 1 * * *"
        assert data["telegram_message_ids"] == [12345]
        assert data["text_chunk_count"] == 1

    _with_temp_delivery_dir(_case)


def cy_morning_failed_primary_without_text_receipt_allows_recovery() -> None:
    def _case(tmp: Path) -> None:
        target_date = "2026-07-06"
        failed_attempt_path = tmp / f"{target_date}.partial.json"
        failed_attempt_path.write_text(
            json.dumps({"target_date": target_date, "chat_type": "production"}),
            encoding="utf-8",
        )
        assert not cy_morning_has_valid_production_receipt(target_date)
        _write_fixture_receipt(target_date, "15 3 * * *")
        assert cy_morning_has_valid_production_receipt(target_date)

    _with_temp_delivery_dir(_case)


def cy_morning_dry_run_does_not_create_delivery_receipt() -> None:
    def _case(_tmp: Path) -> None:
        target_date = "2026-07-06"
        receipt = cy_morning_maybe_write_delivery_receipt(
            target_date=target_date,
            chat_type="production",
            telegram_message_ids=[1],
            text_chunk_count=1,
            sent=False,
            event_schedule="0 1 * * *",
        )
        assert receipt is None
        assert not cy_morning_delivery_path(target_date).exists()
        assert not cy_morning_has_valid_production_receipt(target_date)

    _with_temp_delivery_dir(_case)


def cy_morning_test_channel_send_does_not_create_production_receipt() -> None:
    def _case(_tmp: Path) -> None:
        target_date = "2026-07-06"
        receipt = cy_morning_maybe_write_delivery_receipt(
            target_date=target_date,
            chat_type="test",
            telegram_message_ids=[1],
            text_chunk_count=1,
            sent=True,
            event_schedule="0 1 * * *",
        )
        assert receipt is None
        assert not cy_morning_delivery_path(target_date).exists()
        assert not cy_morning_has_valid_production_receipt(target_date)

    _with_temp_delivery_dir(_case)


def cy_morning_image_failure_plus_successful_text_creates_receipt() -> None:
    def _case(_tmp: Path) -> None:
        target_date = "2026-07-06"
        image_result = {"result": "failed_non_fatal", "error_type": "RuntimeError"}
        assert image_result["result"] == "failed_non_fatal"
        receipt = cy_morning_maybe_write_delivery_receipt(
            target_date=target_date,
            chat_type="production",
            telegram_message_ids=[5001],
            text_chunk_count=1,
            sent=True,
            event_schedule="0 1 * * *",
        )
        assert receipt is not None
        assert cy_morning_has_valid_production_receipt(target_date)

    _with_temp_delivery_dir(_case)


def cy_morning_successful_github_job_without_receipt_does_not_suppress_recovery() -> None:
    def _case(_tmp: Path) -> None:
        target_date = "2026-07-06"
        successful_job_seen = True
        assert successful_job_seen
        assert not cy_morning_has_valid_production_receipt(target_date)

    _with_temp_delivery_dir(_case)


def cy_morning_receipt_uses_asia_nicosia_target_date() -> None:
    def _case(_tmp: Path) -> None:
        assert cy_morning_target_date("2026-07-06", "Asia/Nicosia") == "2026-07-06"
        target_date = cy_morning_target_date("2026-07-06", "Asia/Nicosia")
        _write_fixture_receipt(target_date, "0 1 * * *")
        assert cy_morning_delivery_path(target_date).name == "2026-07-06.json"
        data = cy_morning_load_delivery_receipt(target_date)
        assert data is not None
        assert data["target_date"] == "2026-07-06"

    _with_temp_delivery_dir(_case)


def cy_morning_partial_text_failure_has_no_completed_receipt() -> None:
    async def _run() -> tuple[list[int], str]:
        class FailingBot:
            def __init__(self) -> None:
                self.calls = 0

            async def send_message(self, **_kwargs):
                self.calls += 1
                if self.calls == 1:
                    return types.SimpleNamespace(message_id=7001)
                raise RuntimeError("fixture second chunk failed")

        partial: list[int] = []
        try:
            await _send_telegram_text_chunks(
                FailingBot(),
                chat_id=123,
                chunks=["chunk one", "chunk two"],
                add_test_label=False,
                partial_message_ids=partial,
            )
        except RuntimeError as exc:
            return partial, exc.__class__.__name__
        raise AssertionError("second chunk failure was expected")

    def _case(_tmp: Path) -> None:
        partial, error_type = asyncio.run(_run())
        assert partial == [7001]
        assert error_type == "RuntimeError"
        receipt = cy_morning_maybe_write_delivery_receipt(
            target_date="2026-07-06",
            chat_type="production",
            telegram_message_ids=partial,
            text_chunk_count=2,
            sent=True,
            event_schedule="0 1 * * *",
        )
        assert receipt is None
        assert not cy_morning_has_valid_production_receipt("2026-07-06")

    _with_temp_delivery_dir(_case)


def cy_morning_image_phase_names_match_delivery_state() -> None:
    assert cy_morning_image_phase_for_result("sent") == "image_sent"
    assert cy_morning_image_phase_for_result("generated") == "image_generated"
    assert cy_morning_image_phase_for_result("failed_non_fatal") == "image_failed_non_fatal"
    assert cy_morning_image_phase_for_result("failed_after_duplicates") == "image_failed_non_fatal"
    assert cy_morning_image_phase_for_result("skipped") == "image_skipped"
    assert cy_morning_image_phase_for_result("failed_non_fatal") != "image_sent"


def cy_text_and_image_receipt_validators_are_strict() -> None:
    def _case(tmp: Path) -> None:
        target_date = "2026-07-06"
        assert not is_valid_cy_text_receipt(target_date, "morning")
        assert not is_valid_cy_image_receipt(target_date, "morning")
        _write_canonical_text_receipt(target_date)
        _write_image_receipt(target_date)
        assert is_valid_cy_text_receipt(target_date, "morning")
        assert has_valid_cy_text_delivery(target_date, "morning")
        assert is_valid_cy_image_receipt(target_date, "morning")

        bad_text = _cy_text_receipt_path("2026-07-07", "morning")
        bad_text.parent.mkdir(parents=True, exist_ok=True)
        bad_text.write_text("{not json", encoding="utf-8")
        assert not is_valid_cy_text_receipt("2026-07-07", "morning")

        bad_image = _cy_image_receipt_path("2026-07-07", "morning")
        bad_image.parent.mkdir(parents=True, exist_ok=True)
        bad_image.write_text(json.dumps({"target_date": "2026-07-07", "post_type": "morning"}), encoding="utf-8")
        assert not is_valid_cy_image_receipt("2026-07-07", "morning")

    _with_temp_delivery_dir(_case)


def cy_morning_delayed_0315_decision_sends_image_only_when_text_exists() -> None:
    def _case(_tmp: Path) -> None:
        target_date = "2026-07-06"

        def decision() -> str:
            text_delivered = has_valid_cy_text_delivery(target_date, "morning")
            image_delivered = is_valid_cy_image_receipt(target_date, "morning")
            if text_delivered and image_delivered:
                return "skip_all"
            if text_delivered and not image_delivered:
                return "image_only"
            return "full_publish"

        assert decision() == "full_publish"
        _write_canonical_text_receipt(target_date)
        assert decision() == "image_only"
        _write_image_receipt(target_date)
        assert decision() == "skip_all"

    _with_temp_delivery_dir(_case)


def cy_evening_late_recovery_decision_sends_image_only_after_delayed_text() -> None:
    def _case(_tmp: Path) -> None:
        target_date = "2026-07-07"
        assert not has_valid_cy_text_delivery(target_date, "evening", allow_legacy_morning=False)
        _write_canonical_text_receipt(target_date, "evening")
        assert has_valid_cy_text_delivery(target_date, "evening", allow_legacy_morning=False)
        assert not is_valid_cy_image_receipt(target_date, "evening")
        _write_image_receipt(target_date, "evening")
        assert is_valid_cy_image_receipt(target_date, "evening")

    _with_temp_delivery_dir(_case)


def cy_manual_prod_text_receipt_suppresses_scheduled_recovery() -> None:
    def _case(_tmp: Path) -> None:
        target_date = "2026-07-06"
        _write_canonical_text_receipt(target_date, "morning", ids=[9001])
        assert has_valid_cy_text_delivery(target_date, "morning")
        assert not cy_morning_has_valid_production_receipt(target_date)

    _with_temp_delivery_dir(_case)


def cy_legacy_morning_receipt_remains_compatibility_fallback() -> None:
    def _case(_tmp: Path) -> None:
        target_date = "2026-07-06"
        assert not is_valid_cy_text_receipt(target_date, "morning")
        _write_fixture_receipt(target_date, "0 1 * * *")
        assert has_valid_cy_text_delivery(target_date, "morning")

    _with_temp_delivery_dir(_case)


def cy_missing_image_message_id_does_not_validate_receipt() -> None:
    def _case(_tmp: Path) -> None:
        target_date = "2026-07-06"
        _write_image_receipt(target_date, message_id=0)
        assert not is_valid_cy_image_receipt(target_date, "morning")

    _with_temp_delivery_dir(_case)


def cy_image_diagnostics_redacts_secrets() -> None:
    def _case(tmp: Path) -> None:
        old_token = os.environ.get("POLLINATIONS_TOKEN")
        old_diag = os.environ.get("CY_IMAGE_DIAGNOSTICS_DIR")
        secret = "fixture-secret-token"
        try:
            os.environ["POLLINATIONS_TOKEN"] = secret
            path = _cy_write_image_diagnostics(
                mode="morning",
                target_date="2026-07-06",
                result="failed",
                error=RuntimeError(f"backend failed with {secret} and https://x.test/?token={secret}"),
            )
            data = json.loads(path.read_text(encoding="utf-8"))
            message = data["error"]["message"]
            assert secret not in message
            assert "[redacted]" in message or "[redacted-url]" in message
        finally:
            if old_token is None:
                os.environ.pop("POLLINATIONS_TOKEN", None)
            else:
                os.environ["POLLINATIONS_TOKEN"] = old_token
            if old_diag is None:
                os.environ.pop("CY_IMAGE_DIAGNOSTICS_DIR", None)
            else:
                os.environ["CY_IMAGE_DIAGNOSTICS_DIR"] = old_diag

    _with_temp_delivery_dir(_case)


def cy_image_recovery_force_regenerates_instead_of_reusing_cache() -> None:
    async def _run(tmp: Path) -> tuple[int, list[str]]:
        world_old = sys.modules.get("world_en")
        imagegen_old = sys.modules.get("world_en.imagegen")
        old_img_dir = os.environ.get("CY_SAFE_IMAGE_DIR")
        old_min = os.environ.get("CY_IMG_MIN_BYTES")
        world_stub = types.ModuleType("world_en")
        imagegen_stub = types.ModuleType("world_en.imagegen")
        calls: list[str] = []

        def _generate(_prompt: str, requested_path: str) -> str:
            calls.append(requested_path)
            path = Path(requested_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"fresh image bytes " + str(len(calls)).encode("ascii"))
            return str(path)

        imagegen_stub.generate_astro_image = _generate
        world_stub.imagegen = imagegen_stub
        sys.modules["world_en"] = world_stub
        sys.modules["world_en.imagegen"] = imagegen_stub
        os.environ["CY_SAFE_IMAGE_DIR"] = str(tmp / "images")
        os.environ["CY_IMG_MIN_BYTES"] = "1"
        try:
            for _ in range(2):
                result = await _build_safe_test_image(
                    REAL_LEGACY_MORNING_WITHOUT_SEA_ASTRO,
                    "morning",
                    generate_image=True,
                    send_image_to_test=False,
                    send_image_to_chat=False,
                    image_chat_id=None,
                    image_only_recovery=True,
                )
                assert result["result"] == "generated"
        finally:
            if world_old is None:
                sys.modules.pop("world_en", None)
            else:
                sys.modules["world_en"] = world_old
            if imagegen_old is None:
                sys.modules.pop("world_en.imagegen", None)
            else:
                sys.modules["world_en.imagegen"] = imagegen_old
            if old_img_dir is None:
                os.environ.pop("CY_SAFE_IMAGE_DIR", None)
            else:
                os.environ["CY_SAFE_IMAGE_DIR"] = old_img_dir
            if old_min is None:
                os.environ.pop("CY_IMG_MIN_BYTES", None)
            else:
                os.environ["CY_IMG_MIN_BYTES"] = old_min
        return len(calls), calls

    def _case(tmp: Path) -> None:
        count, paths = asyncio.run(_run(tmp))
        assert count == 2
        assert len(set(paths)) == 2

    _with_temp_delivery_dir(_case)


def cy_test_image_checks_prod_history_and_writes_only_test_history() -> None:
    async def _run(tmp: Path) -> tuple[dict, bytes, list[dict[str, object]]]:
        world_old = sys.modules.get("world_en")
        imagegen_old = sys.modules.get("world_en.imagegen")
        old_prod = cyprus_visual_dedup.CYPRUS_VISUAL_HISTORY_PROD_PATH
        old_test = cyprus_visual_dedup.CYPRUS_VISUAL_HISTORY_TEST_PATH
        old_bot = safe_module.Bot
        old_token = safe_module.TOKEN
        old_env = {name: os.environ.get(name) for name in (
            "CHANNEL_ID_TEST",
            "CY_SAFE_IMAGE_DIR",
            "CY_IMG_MIN_BYTES",
            "CY_DISABLE_BAY_VISUALS",
        )}
        prod = tmp / "cyprus_visual_history_prod.json"
        test = tmp / "cyprus_visual_history_test.json"
        duplicate_seed = tmp / "production.ppm"
        _write_ppm(duplicate_seed, color=(70, 90, 120), comment="production")
        cyprus_visual_dedup.record_cyprus_visual_publication(
            date_value="2026-06-20",
            post_type="evening",
            image_path=duplicate_seed,
            selected_scene="protected_bay",
            prompt_version="cyprus_visual_v5",
            cache_key="fixture-prod",
            style_name="fixture-prod",
            composition="wide panorama composition",
            visual_archetype="bay_panorama",
            history_path=prod,
        )
        prod_before = prod.read_bytes()
        calls: list[str] = []
        photo_calls: list[dict[str, object]] = []
        world_stub = types.ModuleType("world_en")
        imagegen_stub = types.ModuleType("world_en.imagegen")

        def _generate(_prompt: str, requested_path: str, **_kwargs):
            calls.append(requested_path)
            path = Path(requested_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            if len(calls) == 1:
                shutil.copy2(duplicate_seed, path)
            else:
                _write_gradient_ppm(path, comment="distinct-test")
            return types.SimpleNamespace(
                path=str(path),
                backend="custom",
                byte_count=path.stat().st_size,
                backend_attempts=[{"backend": "custom"}],
            )

        class FakeBot:
            def __init__(self, token: str) -> None:
                self.token = token

            async def send_photo(self, **kwargs):
                photo_calls.append(kwargs)
                return types.SimpleNamespace(message_id=8080)

        imagegen_stub.generate_astro_image_result_with_exclusions = _generate
        imagegen_stub.generate_astro_image = lambda prompt, path: str(_generate(prompt, path).path)
        world_stub.imagegen = imagegen_stub
        sys.modules["world_en"] = world_stub
        sys.modules["world_en.imagegen"] = imagegen_stub
        cyprus_visual_dedup.CYPRUS_VISUAL_HISTORY_PROD_PATH = prod
        cyprus_visual_dedup.CYPRUS_VISUAL_HISTORY_TEST_PATH = test
        safe_module.Bot = FakeBot
        safe_module.TOKEN = "fixture-token"
        os.environ["CHANNEL_ID_TEST"] = "9090"
        os.environ["CY_SAFE_IMAGE_DIR"] = str(tmp / "images")
        os.environ["CY_IMG_MIN_BYTES"] = "12000"
        os.environ["CY_DISABLE_BAY_VISUALS"] = "1"
        try:
            result = await _build_safe_test_image(
                REAL_LEGACY_MORNING_WITHOUT_SEA_ASTRO,
                "morning",
                generate_image=True,
                send_image_to_test=True,
                send_image_to_chat=False,
                image_chat_id=None,
            )
        finally:
            cyprus_visual_dedup.CYPRUS_VISUAL_HISTORY_PROD_PATH = old_prod
            cyprus_visual_dedup.CYPRUS_VISUAL_HISTORY_TEST_PATH = old_test
            safe_module.Bot = old_bot
            safe_module.TOKEN = old_token
            if world_old is None:
                sys.modules.pop("world_en", None)
            else:
                sys.modules["world_en"] = world_old
            if imagegen_old is None:
                sys.modules.pop("world_en.imagegen", None)
            else:
                sys.modules["world_en.imagegen"] = imagegen_old
            for name, value in old_env.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value
        assert prod.read_bytes() == prod_before
        assert len(cyprus_visual_dedup.load_cyprus_visual_history(test)) == 1
        return result, prod_before, photo_calls

    def _case(tmp: Path) -> None:
        result, _prod_before, photo_calls = asyncio.run(_run(tmp))
        assert result["result"] == "sent"
        attempts = result.get("attempts") or []
        assert attempts[0]["dedup_reason"] == "exact_duplicate"
        assert attempts[-1]["dedup_reason"] == "accepted"
        assert len(photo_calls) == 1

    _with_temp_delivery_dir(_case)


async def _run_image_liveness_fixture(tmp: Path, text: str, history: list[dict]) -> tuple[int, dict]:
    world_old = sys.modules.get("world_en")
    imagegen_old = sys.modules.get("world_en.imagegen")
    old_img_dir = os.environ.get("CY_SAFE_IMAGE_DIR")
    old_min = os.environ.get("CY_IMG_MIN_BYTES")
    old_history = cyprus_visual_dedup.CYPRUS_VISUAL_HISTORY_TEST_PATH
    world_stub = types.ModuleType("world_en")
    imagegen_stub = types.ModuleType("world_en.imagegen")
    calls: list[str] = []
    history_path = tmp / "history.json"
    history_path.write_text(json.dumps(history, ensure_ascii=False), encoding="utf-8")

    def _generate(_prompt: str, requested_path: str) -> str:
        calls.append(requested_path)
        path = Path(requested_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"liveness image bytes " + str(len(calls)).encode("ascii"))
        return str(path)

    imagegen_stub.generate_astro_image = _generate
    world_stub.imagegen = imagegen_stub
    sys.modules["world_en"] = world_stub
    sys.modules["world_en.imagegen"] = imagegen_stub
    cyprus_visual_dedup.CYPRUS_VISUAL_HISTORY_TEST_PATH = history_path
    os.environ["CY_SAFE_IMAGE_DIR"] = str(tmp / "images")
    os.environ["CY_IMG_MIN_BYTES"] = "1"
    try:
        result = await _build_safe_test_image(
            text,
            "morning",
            generate_image=True,
            send_image_to_test=False,
            send_image_to_chat=False,
            image_chat_id=None,
            image_only_recovery=False,
        )
    finally:
        cyprus_visual_dedup.CYPRUS_VISUAL_HISTORY_TEST_PATH = old_history
        if world_old is None:
            sys.modules.pop("world_en", None)
        else:
            sys.modules["world_en"] = world_old
        if imagegen_old is None:
            sys.modules.pop("world_en.imagegen", None)
        else:
            sys.modules["world_en.imagegen"] = imagegen_old
        if old_img_dir is None:
            os.environ.pop("CY_SAFE_IMAGE_DIR", None)
        else:
            os.environ["CY_SAFE_IMAGE_DIR"] = old_img_dir
        if old_min is None:
            os.environ.pop("CY_IMG_MIN_BYTES", None)
        else:
            os.environ["CY_IMG_MIN_BYTES"] = old_min
    return len(calls), result


def _history_entry_for_liveness(
    index: int,
    *,
    scene: str = "",
    composition: str = "",
    post_type: str = "morning",
) -> dict:
    return {
        "date": f"2026-07-{index + 1:02d}",
        "post_type": post_type,
        "sha256": f"{index:x}" * 64,
        "selected_scene": scene,
        "composition": composition,
        "prompt_version": "cyprus_visual_v5",
        "cache_key": f"fixture-{index}",
        "style_name": "fixture",
    }


def _write_ppm(path: Path, *, color: tuple[int, int, int], comment: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    width = height = 300
    header = f"P6\n# {comment}\n{width} {height}\n255\n".encode("ascii")
    path.write_bytes(header + bytes(color) * width * height)


def _write_gradient_ppm(path: Path, *, comment: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    width = height = 300
    header = f"P6\n# {comment}\n{width} {height}\n255\n".encode("ascii")
    pixels = bytearray()
    for y in range(height):
        for x in range(width):
            pixels.extend((255 - (x * 255) // width, (y * 255) // height, ((x + y) * 255) // (width + height)))
    path.write_bytes(header + bytes(pixels))


def cy_image_candidate_errors_continue_to_later_candidate() -> None:
    async def _run(tmp: Path) -> tuple[dict, list[str]]:
        world_old = sys.modules.get("world_en")
        imagegen_old = sys.modules.get("world_en.imagegen")
        old_img_dir = os.environ.get("CY_SAFE_IMAGE_DIR")
        old_min = os.environ.get("CY_IMG_MIN_BYTES")
        world_stub = types.ModuleType("world_en")
        imagegen_stub = types.ModuleType("world_en.imagegen")
        calls: list[str] = []

        def _generate(_prompt: str, requested_path: str, **_kwargs):
            calls.append(requested_path)
            path = Path(requested_path)
            if len(calls) == 1:
                raise RuntimeError("fixture candidate backend error")
            if len(calls) == 2:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"x" * 92)
                return types.SimpleNamespace(
                    path=str(path),
                    backend="pollinations",
                    byte_count=92,
                    backend_attempts=[{"backend": "pollinations"}],
                )
            _write_ppm(path, color=(40, 120, 180), comment=f"candidate-{len(calls)}")
            return types.SimpleNamespace(
                path=str(path),
                backend="stable_horde",
                byte_count=path.stat().st_size,
                backend_attempts=[{"backend": "stable_horde"}],
            )

        imagegen_stub.generate_astro_image_result_with_exclusions = _generate
        imagegen_stub.generate_astro_image = lambda prompt, path: str(_generate(prompt, path).path)
        world_stub.imagegen = imagegen_stub
        sys.modules["world_en"] = world_stub
        sys.modules["world_en.imagegen"] = imagegen_stub
        os.environ["CY_SAFE_IMAGE_DIR"] = str(tmp / "images")
        os.environ["CY_IMG_MIN_BYTES"] = "12000"
        try:
            result = await _build_safe_test_image(
                REAL_LEGACY_MORNING_WITHOUT_SEA_ASTRO,
                "morning",
                generate_image=True,
                send_image_to_test=False,
                send_image_to_chat=False,
                image_chat_id=None,
            )
        finally:
            if world_old is None:
                sys.modules.pop("world_en", None)
            else:
                sys.modules["world_en"] = world_old
            if imagegen_old is None:
                sys.modules.pop("world_en.imagegen", None)
            else:
                sys.modules["world_en.imagegen"] = imagegen_old
            if old_img_dir is None:
                os.environ.pop("CY_SAFE_IMAGE_DIR", None)
            else:
                os.environ["CY_SAFE_IMAGE_DIR"] = old_img_dir
            if old_min is None:
                os.environ.pop("CY_IMG_MIN_BYTES", None)
            else:
                os.environ["CY_IMG_MIN_BYTES"] = old_min
        return result, calls

    def _case(tmp: Path) -> None:
        result, calls = asyncio.run(_run(tmp))
        assert result["result"] == "generated"
        assert len(calls) == 3
        attempts = result.get("attempts") or []
        assert attempts[0]["error_type"] == "RuntimeError"
        assert attempts[1]["image_bytes"] == 92
        assert attempts[-1]["backend"] == "stable_horde"

    _with_temp_delivery_dir(_case)


def cy_image_repeated_pollinations_switches_backend_and_sends_receipt() -> None:
    async def _run(tmp: Path) -> tuple[dict, list[set[str]], list[dict[str, object]]]:
        world_old = sys.modules.get("world_en")
        imagegen_old = sys.modules.get("world_en.imagegen")
        old_img_dir = os.environ.get("CY_SAFE_IMAGE_DIR")
        old_min = os.environ.get("CY_IMG_MIN_BYTES")
        old_channel = os.environ.get("CHANNEL_ID")
        old_token_attr = safe_module.TOKEN
        old_bot = safe_module.Bot
        old_history = cyprus_visual_dedup.CYPRUS_VISUAL_HISTORY_PROD_PATH
        history_path = tmp / "history.json"
        duplicate_seed = tmp / "duplicate_seed.ppm"
        _write_ppm(duplicate_seed, color=(80, 80, 80), comment="history")
        history = [
            {
                **_history_entry_for_liveness(0),
                "date": "2026-06-27",
                "perceptual_hash": cyprus_visual_dedup.dhash_file(duplicate_seed),
                "phash": cyprus_visual_dedup.phash_file(duplicate_seed),
            }
        ]
        history_path.write_text(json.dumps(history, ensure_ascii=False), encoding="utf-8")

        world_stub = types.ModuleType("world_en")
        imagegen_stub = types.ModuleType("world_en.imagegen")
        excluded_seen: list[set[str]] = []
        photo_calls: list[dict[str, object]] = []

        def _generate(_prompt: str, requested_path: str, *, excluded_backends=None, **_kwargs):
            excluded = set(excluded_backends or set())
            excluded_seen.append(excluded)
            path = Path(requested_path)
            if "pollinations" not in excluded:
                _write_ppm(path, color=(80, 80, 80), comment=f"pollinations-{len(excluded_seen)}")
                return types.SimpleNamespace(
                    path=str(path),
                    backend="pollinations",
                    byte_count=path.stat().st_size,
                    backend_attempts=[{"backend": "pollinations"}],
                )
            _write_gradient_ppm(path, comment="stable-horde-distinct")
            return types.SimpleNamespace(
                path=str(path),
                backend="stable_horde",
                byte_count=path.stat().st_size,
                backend_attempts=[{"backend": "stable_horde"}],
            )

        class FakeBot:
            def __init__(self, token: str) -> None:
                self.token = token

            async def send_photo(self, **kwargs):
                photo_calls.append(kwargs)
                return types.SimpleNamespace(message_id=7001)

        imagegen_stub.generate_astro_image_result_with_exclusions = _generate
        imagegen_stub.generate_astro_image = lambda prompt, path: str(_generate(prompt, path).path)
        world_stub.imagegen = imagegen_stub
        sys.modules["world_en"] = world_stub
        sys.modules["world_en.imagegen"] = imagegen_stub
        cyprus_visual_dedup.CYPRUS_VISUAL_HISTORY_PROD_PATH = history_path
        safe_module.TOKEN = "fixture-token"
        safe_module.Bot = FakeBot
        os.environ["CHANNEL_ID"] = "777"
        os.environ["CY_SAFE_IMAGE_DIR"] = str(tmp / "images")
        os.environ["CY_IMG_MIN_BYTES"] = "12000"
        try:
            result = await _build_safe_test_image(
                REAL_LEGACY_MORNING_WITHOUT_SEA_ASTRO,
                "morning",
                generate_image=True,
                send_image_to_test=False,
                send_image_to_chat=True,
                image_chat_id=777,
            )
        finally:
            cyprus_visual_dedup.CYPRUS_VISUAL_HISTORY_PROD_PATH = old_history
            safe_module.TOKEN = old_token_attr
            safe_module.Bot = old_bot
            if world_old is None:
                sys.modules.pop("world_en", None)
            else:
                sys.modules["world_en"] = world_old
            if imagegen_old is None:
                sys.modules.pop("world_en.imagegen", None)
            else:
                sys.modules["world_en.imagegen"] = imagegen_old
            if old_img_dir is None:
                os.environ.pop("CY_SAFE_IMAGE_DIR", None)
            else:
                os.environ["CY_SAFE_IMAGE_DIR"] = old_img_dir
            if old_min is None:
                os.environ.pop("CY_IMG_MIN_BYTES", None)
            else:
                os.environ["CY_IMG_MIN_BYTES"] = old_min
            if old_channel is None:
                os.environ.pop("CHANNEL_ID", None)
            else:
                os.environ["CHANNEL_ID"] = old_channel
        return result, excluded_seen, photo_calls

    def _case(tmp: Path) -> None:
        result, excluded_seen, photo_calls = asyncio.run(_run(tmp))
        assert result["result"] == "sent"
        assert result["backend"] == "stable_horde"
        assert any("pollinations" in value for value in excluded_seen)
        assert len(photo_calls) == 1
        receipt = _cy_image_receipt_path("2026-06-27", "morning")
        assert is_valid_cy_image_receipt("2026-06-27", "morning")
        receipt_data = json.loads(receipt.read_text("utf-8"))
        assert receipt_data["telegram_message_id"] == 7001
        assert receipt_data["backend"] == "stable_horde"
        attempts = result.get("attempts") or []
        assert any(item.get("dedup_reason") == "provider_repeated_output" for item in attempts)
        assert attempts[-1]["backend"] == "stable_horde"

    _with_temp_delivery_dir(_case)


def cy_image_raising_backend_is_bounded_to_ten_calls() -> None:
    async def _run(tmp: Path) -> tuple[dict, list[int]]:
        world_old = sys.modules.get("world_en")
        imagegen_old = sys.modules.get("world_en.imagegen")
        old_img_dir = os.environ.get("CY_SAFE_IMAGE_DIR")
        world_stub = types.ModuleType("world_en")
        imagegen_stub = types.ModuleType("world_en.imagegen")
        remaining_values: list[int] = []

        def _raise(_prompt: str, _requested_path: str, *, max_backend_calls: int, **_kwargs):
            remaining_values.append(max_backend_calls)
            raise RuntimeError("fixture provider outage")

        imagegen_stub.generate_astro_image_outcome_with_exclusions = _raise
        world_stub.imagegen = imagegen_stub
        sys.modules["world_en"] = world_stub
        sys.modules["world_en.imagegen"] = imagegen_stub
        os.environ["CY_SAFE_IMAGE_DIR"] = str(tmp / "images")
        try:
            result = await _build_safe_test_image(
                REAL_LEGACY_MORNING_WITHOUT_SEA_ASTRO,
                "morning",
                generate_image=True,
                send_image_to_test=False,
                send_image_to_chat=False,
                image_chat_id=None,
            )
        finally:
            if world_old is None:
                sys.modules.pop("world_en", None)
            else:
                sys.modules["world_en"] = world_old
            if imagegen_old is None:
                sys.modules.pop("world_en.imagegen", None)
            else:
                sys.modules["world_en.imagegen"] = imagegen_old
            if old_img_dir is None:
                os.environ.pop("CY_SAFE_IMAGE_DIR", None)
            else:
                os.environ["CY_SAFE_IMAGE_DIR"] = old_img_dir
        return result, remaining_values

    def _case(tmp: Path) -> None:
        result, remaining_values = asyncio.run(_run(tmp))
        assert result["result"] == "failed_non_fatal"
        assert len(remaining_values) == 10
        assert remaining_values == list(range(10, 0, -1))
        assert result["backend_call_count"] == 10
        assert result["backend_call_limit"] == 10
        assert result["provider_failure_count"] == 10
        diag = tmp / "cy_image_diagnostics" / "2026-06-27-morning" / "image_result.json"
        payload = json.loads(diag.read_text("utf-8"))
        assert payload["backend_call_count"] == 10
        assert payload["final_reason"] == "failed_non_fatal"

    _with_temp_delivery_dir(_case)


def cy_image_duplicates_then_outage_is_failed_after_duplicates() -> None:
    async def _run(tmp: Path) -> dict:
        world_old = sys.modules.get("world_en")
        imagegen_old = sys.modules.get("world_en.imagegen")
        old_img_dir = os.environ.get("CY_SAFE_IMAGE_DIR")
        old_min = os.environ.get("CY_IMG_MIN_BYTES")
        old_history = cyprus_visual_dedup.CYPRUS_VISUAL_HISTORY_TEST_PATH
        history_path = tmp / "history.json"
        seed = tmp / "seed.ppm"
        _write_ppm(seed, color=(90, 90, 90), comment="history")
        history_path.write_text(
            json.dumps(
                [
                    {
                        **_history_entry_for_liveness(0),
                        "date": "2026-06-26",
                        "perceptual_hash": cyprus_visual_dedup.dhash_file(seed),
                        "phash": cyprus_visual_dedup.phash_file(seed),
                    }
                ],
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        world_stub = types.ModuleType("world_en")
        imagegen_stub = types.ModuleType("world_en.imagegen")
        calls = 0

        def _outcome(_prompt: str, requested_path: str, *, max_backend_calls: int, **_kwargs):
            nonlocal calls
            calls += 1
            if calls <= 2:
                path = Path(requested_path)
                _write_ppm(path, color=(90, 90, 90), comment=f"duplicate-{calls}")
                result = types.SimpleNamespace(
                    path=str(path),
                    backend="pollinations",
                    byte_count=path.stat().st_size,
                    backend_attempts=[{"backend": "pollinations", "result": "success"}],
                )
                return types.SimpleNamespace(
                    result=result,
                    backend_attempts=result.backend_attempts,
                    error_type="",
                    error_message="",
                    exhausted=False,
                    actual_backend_call_count=1,
                )
            attempts = [
                {"backend": "stable_horde", "result": "failed"}
                for _ in range(max_backend_calls)
            ]
            return types.SimpleNamespace(
                result=None,
                backend_attempts=attempts,
                error_type="ProviderOutage",
                error_message="fixture outage after duplicates",
                exhausted=True,
                actual_backend_call_count=max_backend_calls,
            )

        imagegen_stub.generate_astro_image_outcome_with_exclusions = _outcome
        world_stub.imagegen = imagegen_stub
        sys.modules["world_en"] = world_stub
        sys.modules["world_en.imagegen"] = imagegen_stub
        cyprus_visual_dedup.CYPRUS_VISUAL_HISTORY_TEST_PATH = history_path
        os.environ["CY_SAFE_IMAGE_DIR"] = str(tmp / "images")
        os.environ["CY_IMG_MIN_BYTES"] = "12000"
        try:
            return await _build_safe_test_image(
                REAL_LEGACY_MORNING_WITHOUT_SEA_ASTRO,
                "morning",
                generate_image=True,
                send_image_to_test=False,
                send_image_to_chat=False,
                image_chat_id=None,
            )
        finally:
            cyprus_visual_dedup.CYPRUS_VISUAL_HISTORY_TEST_PATH = old_history
            if world_old is None:
                sys.modules.pop("world_en", None)
            else:
                sys.modules["world_en"] = world_old
            if imagegen_old is None:
                sys.modules.pop("world_en.imagegen", None)
            else:
                sys.modules["world_en.imagegen"] = imagegen_old
            if old_img_dir is None:
                os.environ.pop("CY_SAFE_IMAGE_DIR", None)
            else:
                os.environ["CY_SAFE_IMAGE_DIR"] = old_img_dir
            if old_min is None:
                os.environ.pop("CY_IMG_MIN_BYTES", None)
            else:
                os.environ["CY_IMG_MIN_BYTES"] = old_min

    def _case(tmp: Path) -> None:
        result = asyncio.run(_run(tmp))
        assert result["result"] == "failed_after_duplicates"
        assert result["result"] != "skipped_duplicate"
        assert result["valid_candidate_count"] == 2
        assert result["duplicate_candidate_count"] == 2
        assert result["provider_failure_count"] == 8
        assert result["backend_call_count"] == 10
        assert not _cy_image_receipt_path("2026-06-27", "morning").exists()

    _with_temp_delivery_dir(_case)


def cy_image_no_available_backends_stops_without_variation_spin() -> None:
    async def _run(tmp: Path) -> tuple[dict, int]:
        world_old = sys.modules.get("world_en")
        imagegen_old = sys.modules.get("world_en.imagegen")
        old_img_dir = os.environ.get("CY_SAFE_IMAGE_DIR")
        world_stub = types.ModuleType("world_en")
        imagegen_stub = types.ModuleType("world_en.imagegen")
        calls = 0

        def _outcome(*_args, **_kwargs):
            nonlocal calls
            calls += 1
            return types.SimpleNamespace(
                result=None,
                backend_attempts=[],
                error_type="NoBackendsAvailable",
                error_message="all backends excluded",
                exhausted=True,
                actual_backend_call_count=0,
            )

        imagegen_stub.generate_astro_image_outcome_with_exclusions = _outcome
        world_stub.imagegen = imagegen_stub
        sys.modules["world_en"] = world_stub
        sys.modules["world_en.imagegen"] = imagegen_stub
        os.environ["CY_SAFE_IMAGE_DIR"] = str(tmp / "images")
        try:
            result = await _build_safe_test_image(
                REAL_LEGACY_MORNING_WITHOUT_SEA_ASTRO,
                "morning",
                generate_image=True,
                send_image_to_test=False,
                send_image_to_chat=False,
                image_chat_id=None,
            )
        finally:
            if world_old is None:
                sys.modules.pop("world_en", None)
            else:
                sys.modules["world_en"] = world_old
            if imagegen_old is None:
                sys.modules.pop("world_en.imagegen", None)
            else:
                sys.modules["world_en.imagegen"] = imagegen_old
            if old_img_dir is None:
                os.environ.pop("CY_SAFE_IMAGE_DIR", None)
            else:
                os.environ["CY_SAFE_IMAGE_DIR"] = old_img_dir
        return result, calls

    def _case(tmp: Path) -> None:
        result, calls = asyncio.run(_run(tmp))
        assert result["result"] == "failed_non_fatal"
        assert calls == 1
        assert result["backend_call_count"] == 0
        assert result["provider_failure_count"] == 0

    _with_temp_delivery_dir(_case)


def cy_image_pollinations_receipt_uses_actual_backend() -> None:
    async def _run(tmp: Path) -> tuple[dict, list[dict[str, object]]]:
        world_old = sys.modules.get("world_en")
        imagegen_old = sys.modules.get("world_en.imagegen")
        old_img_dir = os.environ.get("CY_SAFE_IMAGE_DIR")
        old_min = os.environ.get("CY_IMG_MIN_BYTES")
        old_channel = os.environ.get("CHANNEL_ID")
        old_token_attr = safe_module.TOKEN
        old_bot = safe_module.Bot
        old_history = cyprus_visual_dedup.CYPRUS_VISUAL_HISTORY_PROD_PATH
        history_path = tmp / "history.json"
        history_path.write_text("[]", encoding="utf-8")
        photo_calls: list[dict[str, object]] = []
        world_stub = types.ModuleType("world_en")
        imagegen_stub = types.ModuleType("world_en.imagegen")

        def _outcome(_prompt: str, requested_path: str, **_kwargs):
            path = Path(requested_path)
            _write_gradient_ppm(path, comment="pollinations-distinct")
            attempts = [{"backend": "pollinations", "result": "success"}]
            result = types.SimpleNamespace(
                path=str(path),
                backend="pollinations",
                byte_count=path.stat().st_size,
                backend_attempts=attempts,
            )
            return types.SimpleNamespace(
                result=result,
                backend_attempts=attempts,
                error_type="",
                error_message="",
                exhausted=False,
                actual_backend_call_count=1,
            )

        class FakeBot:
            def __init__(self, token: str) -> None:
                self.token = token

            async def send_photo(self, **kwargs):
                photo_calls.append(kwargs)
                return types.SimpleNamespace(message_id=7002)

        imagegen_stub.generate_astro_image_outcome_with_exclusions = _outcome
        world_stub.imagegen = imagegen_stub
        sys.modules["world_en"] = world_stub
        sys.modules["world_en.imagegen"] = imagegen_stub
        cyprus_visual_dedup.CYPRUS_VISUAL_HISTORY_PROD_PATH = history_path
        safe_module.TOKEN = "fixture-token"
        safe_module.Bot = FakeBot
        os.environ["CHANNEL_ID"] = "777"
        os.environ["CY_SAFE_IMAGE_DIR"] = str(tmp / "images")
        os.environ["CY_IMG_MIN_BYTES"] = "12000"
        try:
            result = await _build_safe_test_image(
                REAL_LEGACY_MORNING_WITHOUT_SEA_ASTRO,
                "morning",
                generate_image=True,
                send_image_to_test=False,
                send_image_to_chat=True,
                image_chat_id=777,
            )
        finally:
            cyprus_visual_dedup.CYPRUS_VISUAL_HISTORY_PROD_PATH = old_history
            safe_module.TOKEN = old_token_attr
            safe_module.Bot = old_bot
            if world_old is None:
                sys.modules.pop("world_en", None)
            else:
                sys.modules["world_en"] = world_old
            if imagegen_old is None:
                sys.modules.pop("world_en.imagegen", None)
            else:
                sys.modules["world_en.imagegen"] = imagegen_old
            for key, value in (
                ("CY_SAFE_IMAGE_DIR", old_img_dir),
                ("CY_IMG_MIN_BYTES", old_min),
                ("CHANNEL_ID", old_channel),
            ):
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value
        return result, photo_calls

    def _case(tmp: Path) -> None:
        result, photo_calls = asyncio.run(_run(tmp))
        assert result["result"] == "sent"
        assert result["backend"] == "pollinations"
        assert len(photo_calls) == 1
        receipt = json.loads(_cy_image_receipt_path("2026-06-27", "morning").read_text("utf-8"))
        assert receipt["backend"] == "pollinations"

    _with_temp_delivery_dir(_case)


def cy_image_liveness_skips_recent_compositions_before_backend_without_consuming_attempts() -> None:
    text = REAL_LEGACY_MORNING_WITHOUT_SEA_ASTRO
    first_five_metadata = [
        build_cyprus_scene_prompt_with_metadata(text, post_type="morning", variation_attempt=attempt)[2]
        for attempt in range(5)
    ]

    history = [
        _history_entry_for_liveness(
            index,
            scene=metadata["selected_scene"],
            composition=metadata["composition"],
        )
        for index, metadata in enumerate(first_five_metadata)
    ]

    def _case(tmp: Path) -> None:
        count, result = asyncio.run(_run_image_liveness_fixture(tmp, text, history))
        assert count == 1
        assert result["result"] == "generated"
        attempts = result.get("attempts") or []
        assert attempts
        assert attempts[0]["dedup_reason"] in {"accepted", "recent_scene_family_lru_allowed", "recent_composition_lru_allowed"}
        assert attempts[0]["composition"] not in {meta["composition"] for meta in first_five_metadata}

    _with_temp_delivery_dir(_case)


def cy_image_liveness_visibility_haze_lru_scene_reaches_backend() -> None:
    text = """
    <b>Кипр: погода на сегодня (27.06.2026)</b>
    Лимассол: +31°, ветер 3 м/с.
    Утром местами локальная дымка/туман, AQI 35, PM₂.₅ 8, PM₁₀ 14.
    🌊 Море: волна спокойная.
    #Кипр #погода
    """
    history = [
        _history_entry_for_liveness(0, scene="coastal_promenade"),
        _history_entry_for_liveness(1, scene="small_harbour"),
    ]

    def _case(tmp: Path) -> None:
        count, result = asyncio.run(_run_image_liveness_fixture(tmp, text, history))
        assert count == 1
        attempts = result.get("attempts") or []
        assert attempts and attempts[0]["dedup_reason"] == "recent_scene_family_lru_allowed"
        assert attempts[0]["selected_scene"] in {"coastal_promenade", "small_harbour"}
        assert attempts[0]["cache_status"] != "not_generated"
        assert result["metadata"]["scene_selection_mode"] == "least_recently_used"

    _with_temp_delivery_dir(_case)


def cy_image_liveness_inland_thunder_lru_scene_reaches_backend() -> None:
    text = """
    <b>Кипр: погода на сегодня (27.06.2026)</b>
    Ларнака: 30/25 °C • переменная облачность • 💨 5 м/с
    Лимассол: 30/23 °C • переменная облачность • 💨 5 м/с
    Тродос: 25/18 °C • возможна гроза в горах
    Лимассол: +32°, ветер 4 м/с.
    #Кипр #погода
    """
    history = [
        _history_entry_for_liveness(0, scene="mountain_coast_view"),
        _history_entry_for_liveness(1, scene="open_sea_cliffs"),
        _history_entry_for_liveness(2, scene="breakwater_coast"),
    ]

    def _case(tmp: Path) -> None:
        count, result = asyncio.run(_run_image_liveness_fixture(tmp, text, history))
        assert count == 1
        attempts = result.get("attempts") or []
        assert attempts and attempts[0]["dedup_reason"] == "recent_scene_family_lru_allowed"
        assert attempts[0]["selected_scene"] in {"mountain_coast_view", "open_sea_cliffs", "breakwater_coast"}
        assert result["metadata"]["scene_selection_mode"] == "least_recently_used"

    _with_temp_delivery_dir(_case)


def cy_image_liveness_prefers_eligible_scene_when_available() -> None:
    text = """
    <b>Кипр: погода на сегодня (27.06.2026)</b>
    Лимассол: +31°, ветер 3 м/с.
    Утром местами локальная дымка/туман, AQI 35, PM₂.₅ 8, PM₁₀ 14.
    🌊 Море: волна спокойная.
    #Кипр #погода
    """
    history = [_history_entry_for_liveness(0, scene="coastal_promenade")]

    def _case(tmp: Path) -> None:
        count, result = asyncio.run(_run_image_liveness_fixture(tmp, text, history))
        assert count == 1
        attempts = result.get("attempts") or []
        assert attempts and attempts[0]["selected_scene"] == "small_harbour"
        assert attempts[0]["dedup_reason"] == "accepted"
        assert result["metadata"]["scene_selection_mode"] == "eligible"

    _with_temp_delivery_dir(_case)


def cy_image_liveness_all_recent_compositions_lru_reaches_backend() -> None:
    text = REAL_LEGACY_MORNING_WITHOUT_SEA_ASTRO
    probe = build_cyprus_scene_prompt_with_metadata(text, post_type="morning", variation_attempt=0)[2]
    selected_scene = str(probe["selected_scene"])
    old_scene_compositions = cy_scene_prompt._CY_SCENE_COMPOSITIONS
    test_compositions = old_scene_compositions[selected_scene]
    history = [
        _history_entry_for_liveness(index, composition=composition)
        for index, composition in enumerate(test_compositions)
    ]

    def _case(tmp: Path) -> None:
        try:
            cy_scene_prompt._CY_SCENE_COMPOSITIONS = {
                **old_scene_compositions,
                selected_scene: test_compositions,
            }
            count, result = asyncio.run(_run_image_liveness_fixture(tmp, text, history))
            assert count == 1
            attempts = result.get("attempts") or []
            assert attempts and attempts[0]["dedup_reason"] == "recent_composition_lru_allowed"
            assert attempts[0]["composition"] == test_compositions[0]
            assert result["metadata"]["composition_selection_mode"] == "least_recently_used"
        finally:
            cy_scene_prompt._CY_SCENE_COMPOSITIONS = old_scene_compositions

    _with_temp_delivery_dir(_case)


def _visibility_payload(
    visibility_m: float,
    *,
    humidity: float = 96,
    temperature: float = 24,
    dew_point: float = 23.5,
    weather_code: int = 45,
    current_visibility: float | None = None,
) -> dict:
    times = [f"2026-07-16T{hour:02d}:00" for hour in range(4, 11)]
    visibilities = [8000.0] * len(times)
    visibilities[2] = visibility_m
    return {
        "current": {
            "time": "2026-07-16T03:20",
            "visibility": current_visibility if current_visibility is not None else max(visibility_m, 7000),
            "relative_humidity_2m": humidity,
            "temperature_2m": temperature,
            "dew_point_2m": dew_point,
            "weather_code": weather_code,
        },
        "hourly": {
            "time_local": times,
            "visibility": visibilities,
            "relative_humidity_2m": [humidity] * len(times),
            "temperature_2m": [temperature] * len(times),
            "dew_point_2m": [dew_point] * len(times),
            "weather_code": [weather_code] * len(times),
        },
    }


def cy_visibility_thresholds_and_dust_classification_are_stable() -> None:
    dense = get_cyprus_visibility_context(_visibility_payload(300), target_date="2026-07-16")
    dry_low = get_cyprus_visibility_context(
        _visibility_payload(300, humidity=40, dew_point=16, weather_code=0),
        target_date="2026-07-16",
        air_data={"pm10": 90},
    )
    fog = get_cyprus_visibility_context(
        _visibility_payload(900, humidity=94, weather_code=0),
        target_date="2026-07-16",
    )
    dry_reduced = get_cyprus_visibility_context(
        _visibility_payload(900, humidity=40, dew_point=16, weather_code=0),
        target_date="2026-07-16",
        air_data={"aqi": 20, "pm10": 10},
    )
    mist = get_cyprus_visibility_context(
        _visibility_payload(2200, humidity=91, weather_code=0),
        target_date="2026-07-16",
    )
    clear = get_cyprus_visibility_context(_visibility_payload(8000, humidity=60, dew_point=16, weather_code=0), target_date="2026-07-16")
    mixed = get_cyprus_visibility_context(
        _visibility_payload(600, humidity=96, weather_code=0),
        target_date="2026-07-16",
        air_data={"aqi": 130},
    )
    dust = get_cyprus_visibility_context(
        _visibility_payload(4000, humidity=40, dew_point=16, weather_code=0),
        target_date="2026-07-16",
        air_data={"pm10": 90},
    )
    assert dense.condition == "dense_fog"
    assert dry_low.condition == "dust_haze"
    assert dry_low.condition != "dense_fog"
    assert fog.condition == "fog"
    assert dry_reduced.condition == "reduced_visibility"
    assert dry_reduced.condition != "fog"
    assert mist.condition == "mist"
    assert clear.condition == "clear"
    assert build_cyprus_visibility_line(clear) is None
    assert mixed.condition == "mixed_visibility"
    assert "смесь влажной дымки и загрязнения воздуха" in (build_cyprus_visibility_line(mixed) or "")
    assert dust.condition == "dust_haze"


def cy_visibility_number_normalization_and_uneven_hourly_arrays_are_safe() -> None:
    for invalid in (-1, "-0.1", "", "bad", float("nan"), float("inf"), float("-inf")):
        assert normalize_visibility_m(invalid) is None
    assert dew_point_spread_c(20.0, 20.2) == 0.0

    uneven = _visibility_payload(300, weather_code=0)
    uneven["hourly"]["visibility"] = [300]
    uneven["hourly"]["relative_humidity_2m"] = []
    uneven["hourly"]["dew_point_2m"] = [float("nan")]
    uneven["hourly"]["weather_code"] = []
    context = get_cyprus_visibility_context(uneven, target_date="2026-07-16")
    assert context.condition == "reduced_visibility"

    assert classify_visibility_values(
        visibility_m=300,
        humidity_pct=40,
        temperature_c=24,
        dew_point_c=16,
        weather_code=0,
    )[0] == "reduced_visibility"


def cy_visibility_wmo_fog_and_weather_request_fallback_are_safe() -> None:
    payload = _visibility_payload(8000, current_visibility=None)
    payload["current"].pop("visibility", None)
    payload["current"]["weather_code"] = 45
    payload["hourly"]["visibility"] = []
    context = get_cyprus_visibility_context(payload, target_date="2026-07-16")
    assert context.condition == "fog"
    assert context.condition != "dense_fog"
    line = build_cyprus_visibility_line(context)
    assert line and not re.search(r"\d+\s*м\b", line)
    unavailable = get_cyprus_visibility_context({}, target_date="2026-07-16")
    assert unavailable.condition == "clear"
    assert build_cyprus_visibility_line(unavailable) is None
    rich_url = weather_module._build_url(34.707, 33.022, "Asia/Nicosia", weather_module.ATTEMPTS[0])
    minimal_url = weather_module._build_url(34.707, 33.022, "Asia/Nicosia", weather_module.ATTEMPTS[2])
    compatibility_url = weather_module._build_url(34.707, 33.022, "Asia/Nicosia", weather_module.ATTEMPTS[3])
    assert "visibility" in rich_url and "dew_point_2m" in rich_url
    assert "visibility" in minimal_url and "dew_point_2m" in minimal_url
    assert "visibility" in compatibility_url and "dew_point_2m" in compatibility_url

    old_dir = os.environ.get("CY_VISIBILITY_DIAGNOSTICS_DIR")
    with tempfile.TemporaryDirectory() as tmp:
        try:
            os.environ["CY_VISIBILITY_DIAGNOSTICS_DIR"] = tmp
            save_cyprus_visibility_diagnostics(context, post_type="morning", fog_text_added=True)
            diagnostic = json.loads((Path(tmp) / "2026-07-16-morning.json").read_text("utf-8"))
        finally:
            if old_dir is None:
                os.environ.pop("CY_VISIBILITY_DIAGNOSTICS_DIR", None)
            else:
                os.environ["CY_VISIBILITY_DIAGNOSTICS_DIR"] = old_dir
    required = {
        "condition",
        "confidence",
        "current_visibility_m",
        "morning_min_visibility_m",
        "humidity_pct",
        "temperature_c",
        "dew_point_c",
        "dew_point_spread_c",
        "weather_code",
        "weather_code_source",
        "aqi",
        "pm25",
        "pm10",
        "evidence_source",
        "observation_time",
        "target_date",
        "location_label",
        "classification_reason",
        "score_penalty",
        "fog_text_added",
        "fog_visual_rule",
        "dust_vs_fog_classification",
    }
    assert required <= set(diagnostic)


def cy_visibility_evidence_names_city_and_selected_time() -> None:
    forecast_context = get_cyprus_visibility_context(
        _visibility_payload(900),
        target_date="2026-07-16",
        location_label="Ларнака",
    )
    forecast_line = build_cyprus_visibility_line(forecast_context)
    assert forecast_context.observation_time == "2026-07-16T06:00"
    assert forecast_line
    assert "утром в Ларнаке (прогноз на 06:00)" in forecast_line

    current_context = get_cyprus_visibility_context(
        _visibility_payload(900, current_visibility=400),
        target_date="2026-07-16",
        location_label="Лимассол",
    )
    current_line = build_cyprus_visibility_line(current_context)
    assert current_context.observation_time == "2026-07-16T03:20"
    assert current_line
    assert "сильный утренний туман в Лимассоле (данные на 03:20)" in current_line


def cy_daily_wmo_fog_stays_local_without_structured_visibility_alert() -> None:
    assert post_common_module.code_desc(1) == "🌤 преимущественно ясно"
    assert post_common_module.code_desc(2) == "⛅ переменная облачность"
    assert post_common_module.code_desc(3) == "☁️ пасмурно"

    local_daily_fog = """<b>Кипр: погода на завтра (16.07.2026)</b>
✨ VayboMeter завтра: 8.0/10 — хорошо.
🏖 <b>Морские города</b>
Ларнака: 31/24 °C • 🌫 туман • 💨 4 м/с
Лимассол: 31/24 °C • ☀️ ясно • 💨 4 м/с
———
🏞 <b>Континентальные города</b>
Никосия: 34/23 °C • ☀️ ясно
———
#Кипр #погода
"""
    formatted = build_evening_format_v2("Кипр", local_daily_fog)
    assert "Ларнака: 31/24 °C • 🌫 туман" in formatted
    assert "🧭 Главное завтра: утром местами дымка/туман" not in formatted
    assert "⚠️ Нюанс: воздух по текущим данным чистый, но локальная дымка" not in formatted

    old_flag = os.environ.get("FORMAT_V2_MAIN_NUANCE")
    try:
        os.environ["FORMAT_V2_MAIN_NUANCE"] = "1"
        polished = _insert_main_nuance(formatted)
    finally:
        if old_flag is None:
            os.environ.pop("FORMAT_V2_MAIN_NUANCE", None)
        else:
            os.environ["FORMAT_V2_MAIN_NUANCE"] = old_flag
    assert "дымка/туман" not in polished

    reduced_line = "🌫 Видимость: завтра утром в Лимассоле (прогноз на 07:00) местами снижена, местами около 4000 м; на дорогах и у моря нужна дополнительная дистанция."
    reduced_with_local_fog = local_daily_fog.replace(
        "#Кипр #погода",
        reduced_line + "\n#Кипр #погода",
    )
    assert visibility_condition_from_text(reduced_with_local_fog) == "reduced_visibility"

    context = get_cyprus_visibility_context(
        _visibility_payload(900),
        post_type="evening",
        target_date="2026-07-16",
        location_label="Ларнака",
    )
    structured_line = build_cyprus_visibility_line(context, post_type="evening")
    assert structured_line
    with_evidence = build_evening_format_v2(
        "Кипр",
        local_daily_fog.replace("#Кипр #погода", structured_line + "\n#Кипр #погода"),
    )
    assert "🧭 Главное завтра: утром местами дымка/туман" in with_evidence
    assert structured_line in with_evidence


def cy_morning_fog_survives_format_and_changes_score_nuance_plan() -> None:
    fog_line = build_cyprus_visibility_line(
        get_cyprus_visibility_context(_visibility_payload(320), target_date="2026-07-16")
    )
    assert fog_line
    assert "сильный утренний туман в Лимассоле" in fog_line
    legacy = MORNING_WITH_SEA.replace("☀️ <b>УФ-индекс", fog_line + "\n☀️ <b>УФ-индекс")
    formatted = build_morning_format_v2("Кипр", legacy)
    assert formatted.index("💨 Ветер") < formatted.index("🌫 Видимость:") < formatted.index("☀️ УФ")
    clear_score = _cyprus_score_line(build_morning_format_v2("Кипр", MORNING_WITH_SEA))
    fog_score = _cyprus_score_line(formatted)
    clear_value = float(clear_score.split(":", 1)[1].split("/", 1)[0].strip())
    fog_value = float(fog_score.split(":", 1)[1].split("/", 1)[0].strip())
    assert round(clear_value - fog_value, 1) == 0.5
    assert _cyprus_main_nuance(formatted) == "⚠️ Главный нюанс: до рассеивания тумана осторожнее на дорогах и развязках."
    assert _cyprus_smart_plan_line(formatted) == "✅ План: утром снизить скорость и увеличить дистанцию; после прояснения — вода, SPF и тень."

    mixed_context = get_cyprus_visibility_context(
        _visibility_payload(600),
        target_date="2026-07-16",
        air_data={"aqi": 130, "pm10": 65},
    )
    mixed_line = build_cyprus_visibility_line(mixed_context)
    high_air_legacy = MORNING_WITH_SEA.replace("AQI 58", "AQI 130")
    high_air = build_morning_format_v2("Кипр", high_air_legacy)
    mixed_air = build_morning_format_v2(
        "Кипр",
        high_air_legacy.replace("☀️ <b>УФ-индекс", (mixed_line or "") + "\n☀️ <b>УФ-индекс"),
    )
    assert _cyprus_score_line(mixed_air).split("/10", 1)[0] == _cyprus_score_line(high_air).split("/10", 1)[0]
    assert visibility_air_penalty("fog", 0.0) == 0.5
    assert visibility_air_penalty("mist", 0.0) == 0.2
    assert visibility_air_penalty("mixed_visibility", 0.8) == 0.8
    assert visibility_air_penalty("dust_haze", 0.8) == 0.8


def cy_evening_does_not_use_current_aqi_for_tomorrow_visibility() -> None:
    tomorrow_weather = _visibility_payload(
        4000,
        humidity=40,
        temperature=24,
        dew_point=16,
        weather_code=0,
    )
    without_forecast_air = get_cyprus_visibility_context(
        tomorrow_weather,
        post_type="evening",
        target_date="2026-07-16",
        air_data=None,
    )
    with_today_air_incorrectly_reused = get_cyprus_visibility_context(
        tomorrow_weather,
        post_type="evening",
        target_date="2026-07-16",
        air_data={"aqi": 150},
    )
    assert without_forecast_air.condition == "reduced_visibility"
    assert with_today_air_incorrectly_reused.condition == "dust_haze"


def cy_evening_preserves_only_tomorrow_morning_visibility() -> None:
    context = get_cyprus_visibility_context(
        _visibility_payload(900),
        post_type="evening",
        target_date="2026-07-16",
    )
    line = build_cyprus_visibility_line(context, post_type="evening")
    assert line and "завтра утром" in line
    legacy = """<b>Кипр: погода на завтра (16.07.2026)</b>
✨ VayboMeter завтра: 8.0/10 — хорошо.
🏖 <b>Морские города</b>
Лимассол: 31/24 °C • ясно • 💨 4 м/с
———
🏞 <b>Континентальные города</b>
Никосия: 35/23 °C • ясно
———
{line}
#Кипр #погода
""".format(line=line)
    formatted = build_format_v2("Кипр", "evening", legacy)
    assert line in formatted
    assert "туман весь день" not in formatted.lower()
    clear_score = _cyprus_evening_score_line(legacy.replace(line, ""))
    fog_score = _cyprus_evening_score_line(legacy)
    clear_value = float(clear_score.split(":", 1)[1].split("/", 1)[0].strip())
    fog_value = float(fog_score.split(":", 1)[1].split("/", 1)[0].strip())
    assert round(clear_value - fog_value, 1) == 0.5


def cy_morning_safe_production_polish_keeps_fog_actions() -> None:
    fog_line = build_cyprus_visibility_line(
        get_cyprus_visibility_context(_visibility_payload(320), target_date="2026-07-16")
    )
    legacy = MORNING_WITH_SEA.replace("☀️ <b>УФ-индекс", (fog_line or "") + "\n☀️ <b>УФ-индекс")
    old_values = {key: os.environ.get(key) for key in ("MORNING_VAYBOMETER_SCORE", "FORMAT_V2_MAIN_NUANCE", "MORNING_SMART_PLAN")}
    try:
        os.environ.update({key: "1" for key in old_values})
        sanitized_legacy = sanitize_post_text(legacy).text
        final = build_morning_format_v2("Кипр", sanitized_legacy)
        final = _inject_morning_score(final, "morning")
        final = _insert_main_nuance(final)
        final = _inject_morning_smart_plan(final, "morning")
        final = sanitize_post_text(final).text
    finally:
        for key, value in old_values.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
    assert fog_line in final
    assert "✨ VayboMeter:" in final and "утренний туман" in final
    assert "⚠️ Главный нюанс: до рассеивания тумана осторожнее на дорогах и развязках." in final
    assert "✅ План: утром снизить скорость и увеличить дистанцию; после прояснения — вода, SPF и тень." in final


H2_HEAT_UV_MORNING = """<b>Кипр: погода на сегодня (10.08.2026)</b>
👋 Доброе утро! Теплее всего — Никосия (37°), прохладнее — Тродос (26°).
☀️ <b>УФ-индекс 9 (Very High)</b>: тень 11–16.
🏭 AQI 45 (низкий) • PM₂.₅ 9 / PM₁₀ 18
💨 Ветер: 6.0 м/с • порывы до 16 м/с • 🔹 1009 гПа →
Ларнака: 34/25 °C • ☀️ ясно • 🌊 28
🌇 Закат сегодня: 20:05
✅ Сегодня: вода, SPF, тень.
#Кипр #погода #здоровье
"""

# Concrete actions that belong to the plan line only.
H2_PLAN_ACTION_MARKERS = ("SPF", "11–16", "до 11:00", "18:30", "вода с собой", "в тени", "в помещени")


def _h2_render_morning(source: str) -> str:
    """Run the production morning orchestration order over a legacy fixture."""
    import safe_test_post as safe_module

    old_values = {
        key: os.environ.get(key)
        for key in (
            "MORNING_FEELS_LIKE",
            "MORNING_VAYBOMETER_SCORE",
            "FORMAT_V2_MAIN_NUANCE",
            "MORNING_SMART_PLAN",
            "FORMAT_V2_SCORE_CONCLUSION",
        )
    }
    try:
        os.environ.update({key: "1" for key in old_values})
        v2 = build_morning_format_v2("Кипр", sanitize_post_text(source).text)
        v2 = safe_module._inject_morning_feels(v2, "morning")
        v2 = safe_module._inject_morning_score(v2, "morning")
        v2 = _insert_main_nuance(v2)
        v2 = _apply_astro_cleanup(v2)
        v2 = _apply_cyprus_sensor_cleanup(v2)
        v2 = safe_module._apply_score_conclusion(v2)
        v2 = safe_module._inject_morning_smart_plan(v2, "morning")
        v2 = safe_module._apply_editorial_voice(v2, "morning")
        return sanitize_post_text(v2).text
    finally:
        for key, value in old_values.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _h2_line(text: str, prefix: str) -> str:
    for line in text.splitlines():
        if line.strip().startswith(prefix):
            return line.strip()
    return ""


def cy_h2_morning_heat_uv_roles_are_separated() -> None:
    """Each guidance line plays its own role for one dominant hazard."""
    text = _h2_render_morning(H2_HEAT_UV_MORNING)

    score = _h2_line(text, "✨ VayboMeter")
    feels = _h2_line(text, "🌡 Ощущается:")
    voice = _h2_line(text, "💬 По ощущениям дня:")
    plan = _h2_line(text, "✅ План:")

    # The score keeps its factual reasons.
    assert score and "жара" in score.lower()
    # Feels describes sensation/contrast, never the protective actions.
    assert feels
    for marker in H2_PLAN_ACTION_MARKERS:
        assert marker.lower() not in feels.lower(), f"feels repeats plan action: {marker}"
    # The plan remains the single place for concrete actions.
    assert plan and "SPF" in plan and "11–16" in plan
    # The voice does not restate those actions.
    assert voice
    for marker in H2_PLAN_ACTION_MARKERS:
        assert marker.lower() not in voice.lower(), f"voice repeats plan action: {marker}"


def cy_h3_sea_facts_and_protective_plan_are_separated() -> None:
    """Factual marine data has no timing; the existing high-UV/windy plan keeps it."""
    source = H2_HEAT_UV_MORNING.replace("Никосия (37°)", "Никосия (29°)")
    text = build_morning_format_v2("Кипр", sanitize_post_text(source).text)
    sea_line = _h3_sea_line(text)
    assert sea_line == "🌊 Море: вода 28°C; волна спокойная."
    _assert_h3_factual_sea_line(sea_line)
    plan = _cyprus_smart_plan_line(text)
    assert plan == "✅ План: активность до 11:00 или после 18:30; 11–16 — тень; SPF 50, вода; у моря — защищённые места."


def cy_h2_feels_line_never_carries_plan_actions_at_any_uv() -> None:
    """Every UV branch of the feels line must describe sensation, not actions."""
    import safe_test_post as safe_module

    for uv_value in (5, 6, 7, 8, 9, 11):
        source = H2_HEAT_UV_MORNING.replace(
            "☀️ <b>УФ-индекс 9 (Very High)</b>: тень 11–16.",
            f"☀️ <b>УФ-индекс {uv_value} (High)</b>: тень 11–16.",
        )
        v2 = build_morning_format_v2("Кипр", sanitize_post_text(source).text)
        feels = safe_module._cyprus_feels_line(v2)
        if not feels:
            continue
        low = feels.lower()
        for marker in H2_PLAN_ACTION_MARKERS:
            assert marker.lower() not in low, f"UV {uv_value}: feels repeats plan action {marker!r}: {feels}"


def cy_h2_nuance_does_not_restate_score_reasons() -> None:
    """A nuance that merely rephrases the score is suppressed; new signal is kept."""
    text = _h2_render_morning(H2_HEAT_UV_MORNING)
    score = _h2_line(text, "✨ VayboMeter").lower()
    nuance = _h2_line(text, "⚠️ Главный нюанс:")

    # The score already names the heat, so the nuance must not repeat it.
    assert "жара" in score
    if nuance:
        assert "жара во внутренних районах" not in nuance
        assert not (
            "жара" in nuance.lower() and "порыв" not in nuance.lower()
        ), f"nuance only rephrases the score: {nuance}"


def cy_h2_fog_safety_guidance_survives_dedup() -> None:
    """Independent fog signal and its safety action are never deduplicated away."""
    fog_source = H2_HEAT_UV_MORNING.replace(
        "☀️ <b>УФ-индекс 9 (Very High)</b>: тень 11–16.",
        "🌫 Видимость: утром местами около 300 м, вероятен туман.\n☀️ <b>УФ-индекс 9 (Very High)</b>: тень 11–16.",
    )
    text = _h2_render_morning(fog_source)
    nuance = _h2_line(text, "⚠️ Главный нюанс:")
    plan = _h2_line(text, "✅ План:")
    assert "туман" in nuance.lower(), nuance
    assert "дистанц" in plan.lower() or "скорость" in plan.lower(), plan


def cy_h2_poor_air_advisory_is_not_suppressed() -> None:
    """Air-quality advisory must survive semantic dedup."""
    poor_air = H2_HEAT_UV_MORNING.replace(
        "🏭 AQI 45 (низкий) • PM₂.₅ 9 / PM₁₀ 18",
        "🏭 AQI 135 (высокий) • PM₂.₅ 38 / PM₁₀ 82",
    )
    text = _h2_render_morning(poor_air)
    assert "AQI 135" in text
    assert "PM₂.₅ 38" in text
    assert "😷" in text or "неидеален" in text.lower(), "poor-air advisory disappeared"


def cy_h2_factual_values_are_unchanged_by_role_separation() -> None:
    """Role separation must not touch any factual value."""
    text = _h2_render_morning(H2_HEAT_UV_MORNING)
    for fact in ("37°", "26°", "AQI 45", "PM₂.₅ 9", "PM₁₀ 18", "УФ 9", "28°C"):
        assert fact in text, fact
    # Morning stays "today" and hashtags stay last.
    assert "Кипр сегодня" in text
    lines = [line for line in text.splitlines() if line.strip()]
    assert lines[-1].startswith("#")
    HTMLParser().feed(text)


def cy_h2_editorial_truth_safety_covers_rain_wind_uv_and_nuance() -> None:
    """CI-visible truth guard for LOCAL_WEATHER, WINDY_COAST, HOT_UV and wind nuance."""
    from editorial_voice import CYPRUS_EVENING_VARIANTS, CYPRUS_MORNING_VARIANTS, _scenario

    temperature_claim = re.compile(
        r"\b(?:жар\w*|зно\w*|пекл\w*|тепл\w*|прохлад\w*|холод\w*)",
        re.IGNORECASE,
    )
    wind_claim = re.compile(r"\b(?:ветр\w*|порыв\w*)", re.IGNORECASE)

    rain_only = {
        "rain": True,
        "max_temp": 25,
        "uv": 3,
        "uv_high": False,
        "heat": False,
        "wind": False,
        "gust": 3,
        "aqi": 31,
    }
    assert _scenario(rain_only) == "LOCAL_WEATHER"
    for bank in (CYPRUS_MORNING_VARIANTS, CYPRUS_EVENING_VARIANTS):
        for phrase in bank["LOCAL_WEATHER"]:
            assert temperature_claim.search(phrase) is None, phrase
            assert wind_claim.search(phrase) is None, phrase

    wind_only = {
        "rain": False,
        "max_temp": 27,
        "uv": 4,
        "uv_high": False,
        "heat": False,
        "wind": 7,
        "gust": 11,
        "aqi": 31,
    }
    assert _scenario(wind_only) == "WINDY_COAST"
    for bank in (CYPRUS_MORNING_VARIANTS, CYPRUS_EVENING_VARIANTS):
        for phrase in bank["WINDY_COAST"]:
            assert temperature_claim.search(phrase) is None, phrase

    uv_only = {
        "rain": False,
        "max_temp": 27,
        "uv": 7,
        "uv_high": True,
        "heat": False,
        "wind": False,
        "gust": 3,
        "aqi": 31,
    }
    assert _scenario(uv_only) == "HOT_UV"
    for bank in (CYPRUS_MORNING_VARIANTS, CYPRUS_EVENING_VARIANTS):
        for phrase in bank["HOT_UV"]:
            assert re.search(r"жар\w*|зно\w*|пекл\w*", phrase, re.IGNORECASE) is None, phrase

    wind_nuance_fixture = """<b>🌅 Кипр сегодня (04.05.2026)</b>
✨ VayboMeter: 7.6/10 — хорошо; очень высокий УФ.
🌡 Теплее всего — Никосия (28°), прохладнее — Тродос (21°).
💨 Ветер: 7.0 м/с • порывы до 16 м/с • 🔹 1010 гПа →
☀️ УФ 7 — высокий.
🏭 Воздух: AQI 31 (низкий) • PM₂.₅ 9 / PM₁₀ 16
#Кипр #погода #здоровье
"""
    nuance = _cyprus_main_nuance(wind_nuance_fixture)
    assert nuance == "⚠️ Главный нюанс: порывы у моря."


def cy_morning_final_publication_path_applies_editorial_voice_once() -> None:
    """The final FORMAT_V2 path keeps H.1/H.2 roles and H.3 factual sea provenance."""
    import safe_test_post as safe_module

    old_values = {
        key: os.environ.get(key)
        for key in (
            "MORNING_FEELS_LIKE",
            "MORNING_VAYBOMETER_SCORE",
            "FORMAT_V2_MAIN_NUANCE",
            "MORNING_SMART_PLAN",
            "FORMAT_V2_SCORE_CONCLUSION",
        )
    }
    try:
        os.environ.update({key: "1" for key in old_values})
        sanitized_legacy = sanitize_post_text(MORNING_WITH_SEA).text
        v2 = build_morning_format_v2("Кипр", sanitized_legacy)
        v2 = safe_module._inject_morning_feels(v2, "morning")
        v2 = safe_module._inject_morning_best_window(v2, "morning")
        v2 = safe_module._inject_morning_score(v2, "morning")
        v2 = safe_module._inject_evening_score(v2, "morning")
        v2 = safe_module._apply_format_v2_test_polish(v2)
        v2 = safe_module._apply_confidence_polish(v2)
        v2 = _insert_main_nuance(v2)
        v2 = _apply_astro_cleanup(v2)
        v2 = _apply_cyprus_morning_raw_context(v2, MORNING_WITH_SEA, sanitized_legacy, "morning")
        v2 = _apply_cyprus_sensor_cleanup(v2)
        v2 = safe_module._apply_score_conclusion(v2)
        v2 = safe_module._inject_morning_smart_plan(v2, "morning")
        v2 = safe_module._apply_editorial_voice(v2, "morning")
        v2 = safe_module._apply_compact(v2)
        final_text = sanitize_post_text(v2).text
        final_text = finalize_hashtags_at_end(
            final_text,
            canonical_hashtags="#Кипр #погода #здоровье #Никосия #Тродос",
        )
    finally:
        for key, value in old_values.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    sea_line = _h3_sea_line(final_text)
    assert sea_line == "🌊 Море: вода 28°C; волна спокойная."
    _assert_h3_factual_sea_line(sea_line)
    assert "<b>🌅 Кипр сегодня (27.06.2026)</b>" in final_text
    assert final_text.count("🌊 Море:") == 1
    assert final_text.count("💬 По ощущениям дня:") == 1
    assert "💬 Настрой на завтра:" not in final_text
    assert "AQI 58" in final_text
    feels = _h2_line(final_text, "🌡 Ощущается:")
    voice = _h2_line(final_text, "💬 По ощущениям дня:")
    plan = _h2_line(final_text, "✅ План:")
    assert feels and voice and plan
    for marker in H2_PLAN_ACTION_MARKERS:
        assert marker.lower() not in feels.lower(), marker
        assert marker.lower() not in voice.lower(), marker
    assert "SPF" in plan and "11–16" in plan
    lines = [line for line in final_text.splitlines() if line.strip()]
    assert lines[-1] == "#Кипр #погода #здоровье #Никосия #Тродос"
    HTMLParser().feed(final_text)


def cy_final_orchestration_applies_editorial_voice_after_factual_passes() -> None:
    """Guard the orchestration order itself: voice is applied, not stripped."""
    import inspect

    import safe_test_post as safe_module

    source = inspect.getsource(safe_module.main)
    assert "_apply_editorial_voice(v2_raw, mode)" in source, (
        "final FORMAT_V2 orchestration no longer applies the editorial voice"
    )
    assert '"\\n".join(_without_editorial_voice(v2_raw))' not in source, (
        "final FORMAT_V2 orchestration still strips the editorial voice"
    )

    voice_index = source.index("_apply_editorial_voice(v2_raw, mode)")
    for factual in (
        "_apply_astro_cleanup(v2_raw)",
        "_apply_cyprus_morning_raw_context(",
        "_apply_cyprus_sensor_cleanup(v2_raw)",
        "_apply_score_conclusion(v2_raw)",
        "_inject_morning_smart_plan(v2_raw, mode)",
    ):
        assert source.index(factual) < voice_index, factual
    assert voice_index < source.index("_apply_compact(v2_raw)")
    assert voice_index < source.index("sanitize_post_text(v2_raw)")


def cy_astro_llm_cannot_override_canonical_lunar_facts() -> None:
    """Contradictory or stale LLM prose must never replace canonical lunar facts."""
    import post_common

    canonical = dict(
        phase_name="Полнолуние",
        percent=100,
        sign_raw="Козерог",
        sign_sym="♑",
        voc_text="08:20–10:10",
    )
    contradictory = (
        "🌑 Новолуние — время новых намерений.",
        "✨ 42% освещённости — Луна тусклая.",
        "🌙 Луна в ♒ — идеи и общение.",
        "⚫️ VoC: 14:00–15:30 — без стартов.",
    )
    for line in contradictory:
        assert post_common.astro_llm_line_contradicts_canonical(line, **canonical), line

    for line in (
        "✅ Хороший день для спокойных дел.",
        "💚 В плюсе: восстановление и завершение.",
        "🌕 Полнолуние в ♑ — 100% освещённости.",
        "⚫️ VoC: 08:20–10:10.",
    ):
        assert not post_common.astro_llm_line_contradicts_canonical(line, **canonical), line


def cy_astro_llm_cache_is_keyed_by_canonical_fingerprint() -> None:
    """A cache written for different lunar facts must not be reused for the same date."""
    import post_common

    date_str = "10.08.2026"
    base = ("Полнолуние", 100, "Козерог", "08:20–10:10")
    changed = ("Убывающая Луна", 92, "Козерог", "08:20–10:10")

    fp_base = post_common.astro_canonical_fingerprint(date_str, *base)
    fp_same = post_common.astro_canonical_fingerprint(date_str, *base)
    fp_changed = post_common.astro_canonical_fingerprint(date_str, *changed)
    assert fp_base == fp_same
    assert fp_base != fp_changed
    assert post_common._astro_cache_file(date_str, fp_base) != post_common._astro_cache_file(
        date_str, fp_changed
    )

    old_cache_dir = post_common.CACHE_DIR
    old_llm = os.environ.get("DISABLE_LLM_DAILY")
    with tempfile.TemporaryDirectory() as tmp:
        try:
            post_common.CACHE_DIR = Path(tmp)
            stale_path = post_common._astro_cache_file(date_str, fp_base)
            stale_path.write_text("🌕 Stale interpretation for the old facts.", encoding="utf-8")
            assert post_common._astro_llm_bullets(date_str, *base) == [
                "🌕 Stale interpretation for the old facts."
            ]
            os.environ["DISABLE_LLM_DAILY"] = "1"
            post_common.USE_DAILY_LLM = False
            assert post_common._astro_llm_bullets(date_str, *changed) == []
        finally:
            post_common.CACHE_DIR = old_cache_dir
            post_common.USE_DAILY_LLM = True
            if old_llm is None:
                os.environ.pop("DISABLE_LLM_DAILY", None)
            else:
                os.environ["DISABLE_LLM_DAILY"] = old_llm


def _pendulum_date(year: int, month: int, day: int):
    """Minimal date object with the attributes build_astro_section uses."""
    import datetime as _dt

    class _D(_dt.date):
        def add(self, days: int = 0):
            shifted = _dt.date(self.year, self.month, self.day) + _dt.timedelta(days=days)
            return _D(shifted.year, shifted.month, shifted.day)

        def format(self, fmt: str) -> str:
            if fmt == "YYYY-MM-DD":
                return f"{self.year}-{self.month:02d}-{self.day:02d}"
            return f"{self.day:02d}.{self.month:02d}.{self.year}"

    return _D(year, month, day)


def cy_astro_section_drops_contradictory_llm_lines() -> None:
    """End-to-end: contradictory LLM prose must not reach the rendered astro block."""
    import post_common

    calendar = {
        "2026-08-10": {
            "phase_name": "Полнолуние",
            "percent": 100,
            "sign": "Козерог",
            "void_of_course": {"start": "10.08 08:20", "end": "10.08 10:10"},
        }
    }
    contradictory = [
        "🌑 Новолуние — время новых намерений.",
        "✨ 42% освещённости — Луна почти тёмная.",
        "🌙 Луна в ♒ — идеи и лёгкое общение.",
        "✅ Спокойный день для рутины.",
        "🌟 Хорошее время завершать начатое.",
        "🧭 План дня держи простым.",
    ]

    old_calendar = post_common.load_calendar
    old_bullets = post_common._astro_llm_bullets
    had_timezone = hasattr(post_common.pendulum, "timezone")
    old_timezone = getattr(post_common.pendulum, "timezone", None)
    try:
        post_common.load_calendar = lambda *args, **kwargs: calendar
        post_common._astro_llm_bullets = lambda *args, **kwargs: list(contradictory)
        if not had_timezone:
            post_common.pendulum.timezone = lambda name: name
        section = post_common.build_astro_section(
            date_local=_pendulum_date(2026, 8, 10),
            tz_local="Asia/Nicosia",
        )
    finally:
        post_common.load_calendar = old_calendar
        post_common._astro_llm_bullets = old_bullets
        if not had_timezone:
            delattr(post_common.pendulum, "timezone")
        else:
            post_common.pendulum.timezone = old_timezone

    assert "Полнолуние" in section
    assert "100%" in section
    assert "♑" in section
    assert "Новолуние" not in section
    assert "42%" not in section
    assert "♒" not in section
    assert "Спокойный день для рутины" in section


def cy_astro_canonical_facts_are_never_crowded_out_by_llm() -> None:
    """A verbose LLM must not push canonical phase/illumination out of the block."""
    import post_common

    calendar = {
        "2026-08-10": {
            "phase_name": "Полнолуние",
            "percent": 100,
            "sign": "Козерог",
            "void_of_course": {"start": "10.08 08:20", "end": "10.08 10:10"},
        }
    }
    verbose = [
        "✅ Спокойный день для рутины.",
        "🌟 Хорошее время завершать начатое.",
        "🧭 План дня держи простым.",
        "🫧 Больше пауз и воды.",
        "📋 Дела лучше делать по одному.",
    ]

    old_calendar = post_common.load_calendar
    old_bullets = post_common._astro_llm_bullets
    had_timezone = hasattr(post_common.pendulum, "timezone")
    old_timezone = getattr(post_common.pendulum, "timezone", None)
    try:
        post_common.load_calendar = lambda *args, **kwargs: calendar
        post_common._astro_llm_bullets = lambda *args, **kwargs: list(verbose)
        if not had_timezone:
            post_common.pendulum.timezone = lambda name: name
        section = post_common.build_astro_section(
            date_local=_pendulum_date(2026, 8, 10),
            tz_local="Asia/Nicosia",
        )
    finally:
        post_common.load_calendar = old_calendar
        post_common._astro_llm_bullets = old_bullets
        if not had_timezone:
            delattr(post_common.pendulum, "timezone")
        else:
            post_common.pendulum.timezone = old_timezone

    assert "Полнолуние" in section
    assert "100%" in section
    assert "♑" in section


def cy_astro_block_is_correct_without_llm() -> None:
    """With the LLM disabled the deterministic astro block still carries the facts."""
    import post_common

    old_llm_flag = post_common.USE_DAILY_LLM
    old_bullets = post_common._astro_llm_bullets
    try:
        post_common.USE_DAILY_LLM = False
        post_common._astro_llm_bullets = lambda *args, **kwargs: []
        text = build_morning_format_v2("Кипр", MORNING_FULL_MOON)
    finally:
        post_common.USE_DAILY_LLM = old_llm_flag
        post_common._astro_llm_bullets = old_bullets

    assert "🌕 Полнолуние в ♑ — 100% освещённости." in text
    assert "⚫️ VoC: 08:20–10:10." in text
    HTMLParser().feed(text)


def cy_astro_zero_percent_is_canonical() -> None:
    canonical = dict(
        phase_name="Новолуние",
        percent=0,
        sign_raw="Рак",
        sign_sym="♋",
        voc_text="",
    )
    assert post_common_module.astro_llm_line_contradicts_canonical(
        "✨ 15% освещённости — Луна почти тёмная.", **canonical
    )
    assert not post_common_module.astro_llm_line_contradicts_canonical(
        "🌑 Новолуние в ♋ — 0% освещённости.", **canonical
    )


def cy_astro_quarter_phases_are_distinct() -> None:
    common = dict(percent=50, sign_raw="Рак", sign_sym="♋", voc_text="")
    assert post_common_module.astro_llm_line_contradicts_canonical(
        "🌗 Последняя четверть — время завершать дела.",
        phase_name="Первая четверть",
        **common,
    )
    assert post_common_module.astro_llm_line_contradicts_canonical(
        "🌓 Первая четверть — пора набирать темп.",
        phase_name="Последняя четверть",
        **common,
    )
    assert not post_common_module.astro_llm_line_contradicts_canonical(
        "🌓 Первая четверть — сохраняй спокойный темп.",
        phase_name="Первая четверть",
        **common,
    )


def cy_astro_voc_interval_requires_exact_match() -> None:
    canonical = dict(
        phase_name="Полнолуние",
        percent=100,
        sign_raw="Козерог",
        sign_sym="♑",
        voc_text="08:20–10:10",
    )
    assert post_common_module.astro_llm_line_contradicts_canonical(
        "⚫️ VoC: 08:20–11:30 — без стартов.", **canonical
    )
    assert not post_common_module.astro_llm_line_contradicts_canonical(
        "⚫️ VoC: 08:20–10:10 — без стартов.", **canonical
    )

    # Behavioral path: a partially matching interval must be filtered before merge.
    calendar = {
        "2026-08-10": {
            "phase_name": "Полнолуние",
            "percent": 100,
            "sign": "Козерог",
            "void_of_course": {"start": "10.08 08:20", "end": "10.08 10:10"},
        }
    }
    llm_lines = [
        "⚫️ VoC: 08:20–11:30 — без стартов.",
        "✅ Спокойный день для рутины.",
        "🌟 Хорошее время завершать начатое.",
        "🧭 План дня держи простым.",
    ]
    old_calendar = post_common_module.load_calendar
    old_bullets = post_common_module._astro_llm_bullets
    old_voc_interval = post_common_module.voc_interval_for_date
    had_timezone = hasattr(post_common_module.pendulum, "timezone")
    old_timezone = getattr(post_common_module.pendulum, "timezone", None)
    voc_start = types.SimpleNamespace(format=lambda _pattern: "08:20")
    voc_end = types.SimpleNamespace(format=lambda _pattern: "10:10")
    try:
        post_common_module.load_calendar = lambda *args, **kwargs: calendar
        post_common_module._astro_llm_bullets = lambda *args, **kwargs: list(llm_lines)
        post_common_module.voc_interval_for_date = lambda *args, **kwargs: (voc_start, voc_end)
        if not had_timezone:
            post_common_module.pendulum.timezone = lambda name: name
        section = post_common_module.build_astro_section(
            date_local=_pendulum_date(2026, 8, 10),
            tz_local="Asia/Nicosia",
        )
    finally:
        post_common_module.load_calendar = old_calendar
        post_common_module._astro_llm_bullets = old_bullets
        post_common_module.voc_interval_for_date = old_voc_interval
        if not had_timezone:
            delattr(post_common_module.pendulum, "timezone")
        else:
            post_common_module.pendulum.timezone = old_timezone
    assert "11:30" not in section
    assert "08:20–10:10" in section
    assert "Спокойный день для рутины" in section


def cy_astro_absent_voc_cannot_be_invented_by_llm() -> None:
    canonical = dict(
        phase_name="Полнолуние",
        percent=100,
        sign_raw="Козерог",
        sign_sym="♑",
        voc_text="",
    )
    for fake in (
        "⚫️ VoC: 12:00–13:00 — без стартов.",
        "⚫️ Луна без курса 12:00–13:00 — отложи старты.",
        "⚫️ Void of Course 12:00–13:00.",
    ):
        assert post_common_module.astro_llm_line_contradicts_canonical(fake, **canonical), fake
    assert not post_common_module.astro_llm_line_contradicts_canonical(
        "✅ Спокойный день для рутины.", **canonical
    )


def main() -> None:
    checks = (
        cy_weather_attempts_request_only_sea_level_pressure,
        cy_post_common_prefers_sea_level_pressure_with_surface_fallback,
        cy_format_v2_prefers_sea_level_pressure_with_surface_fallback,
        cy_utils_pressure_trend_supports_sea_level_and_surface_pressure,
        cy_morning_adds_concise_sea_block_when_available,
        cy_morning_source_rows_use_city_formatter_sst_and_preserve_evening,
        cy_morning_uses_today_for_raw_format_score_feels_and_plan,
        cy_evening_keeps_tomorrow_city_forecast,
        cy_city_forecast_omits_row_when_target_daily_date_is_missing,
        cy_city_forecast_does_not_shift_incomplete_or_malformed_arrays,
        cy_city_forecast_never_uses_current_for_missing_target_hourly_date,
        cy_format_v2_source_uses_only_exact_target_date_hourly_values,
        cy_format_v2_source_missing_target_date_never_uses_tomorrow_or_current,
        cy_format_v2_source_current_only_omits_wind_and_pressure,
        cy_format_v2_source_incomplete_arrays_emit_only_aligned_fields,
        cy_format_v2_source_malformed_timestamps_do_not_shift_arrays,
        cy_morning_format_v2_current_sentinels_do_not_change_downstream,
        cy_evening_format_v2_preserves_tomorrow_city_values,
        cy_morning_averages_coastal_sea_rows,
        cy_morning_adds_sea_fallback_when_unavailable,
        cy_morning_rejects_non_marine_numbers_for_sea,
        cy_morning_accepts_winter_explicit_sea_temperature,
        cy_morning_winter_sunset_time_is_not_sea_temperature,
        cy_morning_preserves_full_moon_line_without_illumination_duplicate,
        cy_morning_poor_air_adds_health_recommendation,
        cy_morning_recent_safecast_elevated_is_omitted,
        cy_morning_hashtags_are_final_without_editorial_tail,
        cy_morning_real_safe_path_restores_sea_and_astro_from_raw,
        cy_morning_image_failure_still_sends_text_chunks,
        cy_morning_recovery_publishes_then_delayed_primary_skips_by_receipt,
        cy_morning_primary_publishes_then_recovery_skips_by_receipt,
        cy_morning_failed_primary_without_text_receipt_allows_recovery,
        cy_morning_dry_run_does_not_create_delivery_receipt,
        cy_morning_test_channel_send_does_not_create_production_receipt,
        cy_morning_image_failure_plus_successful_text_creates_receipt,
        cy_morning_successful_github_job_without_receipt_does_not_suppress_recovery,
        cy_morning_receipt_uses_asia_nicosia_target_date,
        cy_morning_partial_text_failure_has_no_completed_receipt,
        cy_morning_image_phase_names_match_delivery_state,
        cy_text_and_image_receipt_validators_are_strict,
        cy_morning_delayed_0315_decision_sends_image_only_when_text_exists,
        cy_evening_late_recovery_decision_sends_image_only_after_delayed_text,
        cy_manual_prod_text_receipt_suppresses_scheduled_recovery,
        cy_legacy_morning_receipt_remains_compatibility_fallback,
        cy_missing_image_message_id_does_not_validate_receipt,
        cy_image_diagnostics_redacts_secrets,
        cy_image_recovery_force_regenerates_instead_of_reusing_cache,
        cy_test_image_checks_prod_history_and_writes_only_test_history,
        cy_image_candidate_errors_continue_to_later_candidate,
        cy_image_repeated_pollinations_switches_backend_and_sends_receipt,
        cy_image_raising_backend_is_bounded_to_ten_calls,
        cy_image_duplicates_then_outage_is_failed_after_duplicates,
        cy_image_no_available_backends_stops_without_variation_spin,
        cy_image_pollinations_receipt_uses_actual_backend,
        cy_image_liveness_skips_recent_compositions_before_backend_without_consuming_attempts,
        cy_image_liveness_visibility_haze_lru_scene_reaches_backend,
        cy_image_liveness_inland_thunder_lru_scene_reaches_backend,
        cy_image_liveness_prefers_eligible_scene_when_available,
        cy_image_liveness_all_recent_compositions_lru_reaches_backend,
        cy_visibility_thresholds_and_dust_classification_are_stable,
        cy_visibility_number_normalization_and_uneven_hourly_arrays_are_safe,
        cy_visibility_wmo_fog_and_weather_request_fallback_are_safe,
        cy_visibility_evidence_names_city_and_selected_time,
        cy_daily_wmo_fog_stays_local_without_structured_visibility_alert,
        cy_morning_fog_survives_format_and_changes_score_nuance_plan,
        cy_evening_does_not_use_current_aqi_for_tomorrow_visibility,
        cy_evening_preserves_only_tomorrow_morning_visibility,
        cy_morning_safe_production_polish_keeps_fog_actions,
        cy_h2_morning_heat_uv_roles_are_separated,
        cy_h3_sea_facts_and_protective_plan_are_separated,
        cy_h2_feels_line_never_carries_plan_actions_at_any_uv,
        cy_h2_nuance_does_not_restate_score_reasons,
        cy_h2_fog_safety_guidance_survives_dedup,
        cy_h2_poor_air_advisory_is_not_suppressed,
        cy_h2_factual_values_are_unchanged_by_role_separation,
        cy_h2_editorial_truth_safety_covers_rain_wind_uv_and_nuance,
        cy_morning_final_publication_path_applies_editorial_voice_once,
        cy_final_orchestration_applies_editorial_voice_after_factual_passes,
        cy_astro_llm_cannot_override_canonical_lunar_facts,
        cy_astro_llm_cache_is_keyed_by_canonical_fingerprint,
        cy_astro_section_drops_contradictory_llm_lines,
        cy_astro_canonical_facts_are_never_crowded_out_by_llm,
        cy_astro_block_is_correct_without_llm,
        cy_astro_zero_percent_is_canonical,
        cy_astro_quarter_phases_are_distinct,
        cy_astro_voc_interval_requires_exact_match,
        cy_astro_absent_voc_cannot_be_invented_by_llm,
    )
    for check in checks:
        check()
        print(f"PASS: {check.__name__}")
    print(f"OK: {len(checks)} Cyprus morning FORMAT_V2 checks passed")


if __name__ == "__main__":
    main()
