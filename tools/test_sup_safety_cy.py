#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Offline regressions for Cyprus SUP wind/gust/offshore safety."""
from __future__ import annotations

import datetime as dt
import os
import sys
import types
from pathlib import Path

os.environ.setdefault("VAYBOMETER_CACHE_DIR", "/tmp/vaybometer-sup-safety-test-cache")


try:
    import pendulum  # type: ignore
except Exception:  # pragma: no cover - lightweight local offline fallback
    class _OfflineDuration(dt.timedelta):
        def total_minutes(self) -> float:
            return self.total_seconds() / 60.0

    class _OfflineDateTime(dt.datetime):
        def in_tz(self, _tz):
            return self

        def in_timezone(self, _tz):
            return self

        def format(self, pattern: str) -> str:
            translated = pattern.replace("YYYY", "%Y").replace("MM", "%m").replace("DD", "%d").replace("HH", "%H").replace("mm", "%M")
            return self.strftime(translated)

        def add(self, *, days: int = 0):
            value = self + dt.timedelta(days=days)
            return _OfflineDateTime.fromtimestamp(value.timestamp())

        def __sub__(self, other):
            value = super().__sub__(other)
            if isinstance(value, dt.timedelta):
                return _OfflineDuration(seconds=value.total_seconds())
            return value

        @property
        def int_timestamp(self) -> int:
            return int(self.timestamp())

    class _OfflineTimezone:
        def __init__(self, name: str):
            self.name = name

    def _offline_parse(value, tz=None):
        parsed = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is not None:
            parsed = parsed.replace(tzinfo=None)
        return _OfflineDateTime(*parsed.timetuple()[:6], parsed.microsecond)

    pendulum = types.ModuleType("pendulum")
    pendulum.DateTime = _OfflineDateTime
    pendulum.Date = dt.date
    pendulum.Timezone = _OfflineTimezone
    pendulum.timezone = lambda name: _OfflineTimezone(str(name))
    pendulum.parse = _offline_parse
    pendulum.from_format = lambda value, fmt, tz=None: _OfflineDateTime.strptime(
        str(value),
        fmt.replace("DD", "%d").replace("MM", "%m").replace("YYYY", "%Y").replace("HH", "%H").replace("mm", "%M"),
    )
    pendulum.date = dt.date
    pendulum.datetime = lambda year, month, day, hour=0, minute=0, second=0, tz=None: _OfflineDateTime(
        year, month, day, hour, minute, second
    )
    pendulum.now = lambda tz=None: _OfflineDateTime.now()
    pendulum.today = lambda tz=None: _OfflineDateTime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    pendulum.instance = lambda value: value
    pendulum.tz = types.SimpleNamespace(timezone=types.SimpleNamespace(Timezone=_OfflineTimezone))
    sys.modules["pendulum"] = pendulum

try:
    import requests  # noqa: F401
except Exception:  # pragma: no cover - no network is used by this suite
    requests_stub = types.ModuleType("requests")
    requests_stub.get = lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("offline"))
    requests_stub.post = requests_stub.get
    sys.modules["requests"] = requests_stub

try:
    import telegram  # noqa: F401
except Exception:  # pragma: no cover - no Telegram call is used by this suite
    telegram_stub = types.ModuleType("telegram")
    telegram_stub.Bot = object
    telegram_stub.constants = types.SimpleNamespace(ParseMode=types.SimpleNamespace(HTML="HTML"))
    sys.modules["telegram"] = telegram_stub

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import post_common as post_common_module  # noqa: E402
from format_v2 import build_evening_format_v2  # noqa: E402
from post_common import (  # noqa: E402
    _shore_class,
    _sup_guidance_line,
    _sup_samples_are_aligned,
    _sup_weather_sample,
    _water_highlights,
    pick_tomorrow_header_metrics,
    sup_safety_level,
)
from post_safety import sanitize_post_text  # noqa: E402
from safe_test_post import _downgrade_sup_lines, _sup_guard_line, _translate_shore_notes  # noqa: E402


TZ = pendulum.timezone("Asia/Nicosia")
TARGET_DATE = pendulum.date(2026, 8, 10)


def _replace_attrs(obj, replacements: dict, callback):
    old = {name: getattr(obj, name) for name in replacements}
    try:
        for name, value in replacements.items():
            setattr(obj, name, value)
        return callback()
    finally:
        for name, value in old.items():
            setattr(obj, name, value)


def _with_forecast_clock(callback):
    fixed_today = pendulum.datetime(2026, 8, 9, 9, 0, tz=TZ)
    return _replace_attrs(
        post_common_module.pendulum,
        {"today": lambda _tz=None: fixed_today},
        callback,
    )


def _water_line(payload: dict, *, wave_h: float = 0.3) -> str:
    wave_at = pendulum.datetime(2026, 8, 10, 12, 0, tz=TZ)

    def build() -> str:
        return _replace_attrs(
            post_common_module,
            {
                "get_weather": lambda *_args, **_kwargs: payload,
                "_fetch_wave_for_tomorrow": lambda *_args, **_kwargs: (wave_h, None, wave_at),
                "get_sst_cached": lambda *_args, **_kwargs: 27.0,
            },
            lambda: _water_highlights("Limassol", 34.707, 33.022, TZ) or "",
        )

    return _with_forecast_clock(build)


def _tomorrow_weather(
    *,
    times: list[str] | None = None,
    wind_kmh: list[float | None] | None = None,
    gust_kmh: list[float | None] | None = None,
    wind_dir: list[float | None] | None = None,
    pressure: list[float | None] | None = None,
    current: dict | None = None,
) -> dict:
    return {
        "hourly": {
            "time": times if times is not None else ["2026-08-10T12:00"],
            "windspeed_10m": wind_kmh if wind_kmh is not None else [28.8],
            "windgusts_10m": gust_kmh if gust_kmh is not None else [32.4],
            "winddirection_10m": wind_dir if wind_dir is not None else [180.0],
            "surface_pressure": pressure if pressure is not None else [1012.0],
        },
        "current": current or {},
    }


def _final_evening_text(payload: dict) -> str:
    highlight = _water_line(payload)
    raw_lines = [
        "<b>Кипр: погода на завтра (10.08.2026)</b>",
        "🏖 <b>Морские города</b>",
        "😎 <b>Лимассол</b>: 30/24 °C • 💨 3.0 м/с (Ю) • порывы 7",
    ]
    if highlight:
        raw_lines.append("   " + highlight)
    raw_lines.extend(("———", "#Кипр #погода #здоровье"))
    legacy_safe = sanitize_post_text("\n".join(raw_lines)).text
    return sanitize_post_text(build_evening_format_v2("Кипр", legacy_safe)).text


def _weather(
    *,
    sample_time: str = "2026-08-10T12:00",
    wind_ms: float | None = 3.0,
    gust_ms: float | None = 7.0,
    wind_dir: float | None = 180.0,
) -> dict:
    return {
        "hourly": {
            "time": [sample_time],
            "windspeed_10m": [None if wind_ms is None else wind_ms * 3.6],
            "windgusts_10m": [None if gust_ms is None else gust_ms * 3.6],
            "winddirection_10m": [wind_dir],
        }
    }


def _decision(
    *,
    wind_ms: float | None = 3.0,
    gust_ms: float | None = 7.0,
    wave_h: float | None = 0.3,
    wind_dir: float | None = 180.0,
    weather_time: str = "2026-08-10T12:00",
    wave_time: str = "2026-08-10T12:00",
) -> str | None:
    sample = _sup_weather_sample(
        _weather(
            sample_time=weather_time,
            wind_ms=wind_ms,
            gust_ms=gust_ms,
            wind_dir=wind_dir,
        ),
        TZ,
        TARGET_DATE,
    )
    shore, _ = _shore_class("Limassol", sample.get("wind_dir"))
    wave_at = pendulum.parse(wave_time, tz=TZ)
    return sup_safety_level(
        wind_ms=sample.get("wind_ms"),
        gust_ms=sample.get("gust_ms"),
        wave_h=wave_h,
        shore=shore,
        samples_aligned=_sup_samples_are_aligned(
            sample.get("sample_at"),
            wave_at,
            TARGET_DATE,
            TZ,
        ),
    )


def calm_onshore_is_excellent() -> None:
    assert _decision(wind_dir=180.0) == "excellent"


def calm_cross_shore_is_excellent() -> None:
    assert _decision(wind_dir=90.0) == "excellent"


def gusts_12_to_14_are_caution_even_with_light_wind() -> None:
    assert _decision(wind_ms=2.5, gust_ms=12.0) == "caution"
    assert _decision(wind_ms=2.5, gust_ms=14.9) == "caution"


def gusts_15_or_more_delay_sup() -> None:
    assert _decision(wind_ms=2.5, gust_ms=15.0) == "delay"
    assert _decision(wind_ms=2.5, gust_ms=18.0) == "delay"


def offshore_light_wind_is_never_excellent() -> None:
    assert _decision(wind_ms=2.5, gust_ms=6.0, wind_dir=0.0) == "caution"


def offshore_from_5_ms_is_stricter() -> None:
    assert _decision(wind_ms=5.0, gust_ms=7.0, wind_dir=0.0) == "delay"


def offshore_with_strong_gusts_is_stricter() -> None:
    assert _decision(wind_ms=2.5, gust_ms=13.0, wind_dir=0.0) == "delay"
    assert _decision(wind_ms=2.5, gust_ms=15.0, wind_dir=0.0) == "delay"


def missing_sup_evidence_is_fail_closed() -> None:
    assert _decision(gust_ms=None) is None
    assert _decision(wind_dir=None) is None
    assert _decision(wave_h=None) is None


def wrong_day_or_mixed_window_is_fail_closed() -> None:
    assert _decision(weather_time="2026-08-09T12:00") is None
    assert _decision(wave_time="2026-08-10T15:00") is None


def malformed_time_does_not_shift_metric_arrays() -> None:
    weather = {
        "hourly": {
            "time": ["not-a-time", "2026-08-10T12:00"],
            "windspeed_10m": [36.0, 10.8],
            "windgusts_10m": [64.8, 25.2],
            "winddirection_10m": [0.0, 180.0],
        }
    }
    sample = _sup_weather_sample(weather, TZ, TARGET_DATE)
    assert round(sample["wind_ms"], 1) == 3.0
    assert round(sample["gust_ms"], 1) == 7.0
    assert sample["wind_dir"] == 180.0


def russian_direction_wording_is_preserved() -> None:
    raw = _sup_guidance_line(
        "excellent",
        city="Limassol",
        wind_ms=3.0,
        gust_ms=7.0,
        wave_h=0.3,
        card="S",
        shore="onshore",
        shore_src="Limassol",
        sst=27.0,
    )
    assert raw is not None
    safe = sanitize_post_text(_translate_shore_notes(raw)).text
    assert "Отлично: SUP" in safe
    assert "(южный ветер, к берегу)" in safe
    assert "/onshore" not in safe


def format_v2_guard_uses_sup_period_not_previous_city_gust() -> None:
    text = "\n".join(
        (
            "Лимассол: 30/24 °C • 💨 3 м/с • порывы до 16 м/с",
            "🧜‍♂️ Отлично: SUP • ветер 3 м/с • порывы до 7 м/с • волна 0.3 м (южный ветер, к берегу)",
        )
    )
    guarded = _downgrade_sup_lines(text)
    assert "Отлично: SUP" in guarded
    assert "только опытным" not in guarded
    assert "лучше отложить" not in guarded


def format_v2_guard_cannot_upgrade_or_hide_unsafe_sup() -> None:
    caution = _sup_guard_line(
        "🧜‍♂️ Отлично: SUP • ветер 3 м/с • порывы до 13 м/с • волна 0.3 м (южный ветер, к берегу)"
    )
    assert "Отлично: SUP" not in caution
    assert "только опытным и короткая сессия" in caution
    assert "порывы до 13 м/с" in caution

    delay = _sup_guard_line(
        "🧜‍♂️ Отлично: SUP • ветер 3 м/с • порывы до 15 м/с • волна 0.3 м (южный ветер, к берегу)"
    )
    assert "Отлично: SUP" not in delay
    assert "SUP лучше отложить" in delay
    assert "порывы до 15 м/с" in delay

    offshore = _sup_guard_line(
        "🧜‍♂️ Отлично: SUP • ветер 5 м/с • порывы до 7 м/с • волна 0.3 м (северный ветер, от берега)"
    )
    assert "Отлично: SUP" not in offshore
    assert "SUP лучше отложить" in offshore
    assert "ветер от берега" in offshore


def format_v2_guard_rejects_partial_evidence_without_touching_other_sports() -> None:
    text = "\n".join(
        (
            "🧜‍♂️ Отлично: Кайт/Винг/Винд; Сёрф (западный ветер, вдоль берега)",
            "🧜‍♂️ Отлично: SUP (южный ветер, к берегу)",
        )
    )
    guarded = _downgrade_sup_lines(text)
    assert "Отлично: Кайт/Винг/Винд; Сёрф" in guarded
    assert "Отлично: SUP" not in guarded
    assert "данных для уверенной оценки недостаточно" in guarded


def tomorrow_hourly_builds_existing_kite_recommendation() -> None:
    payload = _tomorrow_weather(
        times=["2026-08-10T06:00", "2026-08-10T12:00"],
        wind_kmh=[18.0, 28.8],
        gust_kmh=[25.2, 32.4],
        wind_dir=[180.0, 180.0],
        pressure=[1010.0, 1012.0],
    )
    metrics = _with_forecast_clock(lambda: pick_tomorrow_header_metrics(payload, TZ))
    assert round(metrics[0], 1) == 8.0
    assert metrics[1:] == (180, 1012, "↑")
    line = _water_line(payload)
    assert "Отлично: Кайт/Винг/Винд" in line
    assert "S/onshore" in line


def current_only_never_builds_positive_water_recommendation() -> None:
    payload = {
        "current": {
            "windspeed": 28.8,
            "wind_gusts_10m": 32.4,
            "winddirection": 180.0,
            "pressure": 777.0,
        }
    }
    assert _with_forecast_clock(lambda: pick_tomorrow_header_metrics(payload, TZ)) == (
        None,
        None,
        None,
        "→",
    )
    assert "Отлично: Кайт/Винг/Винд" not in _water_line(payload)


def today_hourly_plus_current_never_builds_positive_water_recommendation() -> None:
    payload = _tomorrow_weather(
        times=["2026-08-09T12:00"],
        wind_kmh=[28.8],
        gust_kmh=[32.4],
        wind_dir=[180.0],
        pressure=[1012.0],
        current={
            "windspeed": 28.8,
            "wind_gusts_10m": 32.4,
            "winddirection": 180.0,
            "pressure": 777.0,
        },
    )
    assert _with_forecast_clock(lambda: pick_tomorrow_header_metrics(payload, TZ)) == (
        None,
        None,
        None,
        "→",
    )
    assert "Отлично: Кайт/Винг/Винд" not in _water_line(payload)


def missing_tomorrow_direction_or_gust_is_fail_closed_for_kite() -> None:
    missing_direction = _tomorrow_weather(wind_dir=[])
    missing_gust = _tomorrow_weather(gust_kmh=[])
    assert "Отлично: Кайт/Винг/Винд" not in _water_line(missing_direction)
    assert "Отлично: Кайт/Винг/Винд" not in _water_line(missing_gust)


def malformed_timestamp_keeps_weather_arrays_at_original_indices() -> None:
    payload = _tomorrow_weather(
        times=["not-a-time", "2026-08-10T12:00"],
        wind_kmh=[36.0, 28.8],
        gust_kmh=[72.0, 32.4],
        wind_dir=[0.0, 180.0],
        pressure=[777.0, 1012.0],
    )
    metrics = _with_forecast_clock(lambda: pick_tomorrow_header_metrics(payload, TZ))
    assert round(metrics[0], 1) == 8.0
    assert metrics[1] == 180
    assert metrics[2] == 1012
    line = _water_line(payload)
    assert "Отлично: Кайт/Винг/Винд" in line
    assert "S/onshore" in line
    assert "N/offshore" not in line


def current_sentinels_do_not_change_final_evening_format_v2() -> None:
    today_hourly = {
        "time": ["2026-08-09T12:00"],
        "windspeed_10m": [3.6],
        "windgusts_10m": [7.2],
        "winddirection_10m": [0.0],
        "surface_pressure": [1000.0],
    }
    without_current = {"hourly": today_hourly, "current": {}}
    with_current = {
        "hourly": today_hourly,
        "current": {
            "windspeed": 28.8,
            "wind_gusts_10m": 32.4,
            "winddirection": 180.0,
            "pressure": 777.0,
        },
    }
    baseline = _final_evening_text(without_current)
    sentinel = _final_evening_text(with_current)
    assert sentinel == baseline
    assert "Кайт/Винг/Винд" not in sentinel
    assert "777 гПа" not in sentinel


def safe_surf_and_sup_recommendations_preserve_existing_behavior() -> None:
    safe_weather = _tomorrow_weather(
        wind_kmh=[10.8],
        gust_kmh=[25.2],
        wind_dir=[180.0],
    )
    surf = _water_line(safe_weather, wave_h=1.2)
    assert "Отлично: Сёрф" in surf
    assert "Кайт/Винг/Винд" not in surf
    assert "Отлично: SUP" not in surf

    sup = _water_line(safe_weather, wave_h=0.3)
    assert "Отлично: SUP" in sup
    assert "Кайт/Винг/Винд" not in sup
    assert "Сёрф" not in sup


def missing_or_malformed_tomorrow_wind_is_fail_closed_for_surf() -> None:
    for noon_wind in (None, "not-a-number"):
        payload = _tomorrow_weather(
            times=[
                "2026-08-10T06:00",
                "2026-08-10T12:00",
                "2026-08-10T18:00",
            ],
            wind_kmh=[54.0, noon_wind, 54.0],
            gust_kmh=[57.6, 25.2, 57.6],
            wind_dir=[180.0, 180.0, 180.0],
            current={"windspeed": 3.6, "winddirection": 180.0},
        )
        sample = _with_forecast_clock(
            lambda: _sup_weather_sample(payload, TZ, TARGET_DATE)
        )
        assert sample["wind_ms"] is None
        assert "Отлично: Сёрф" not in _water_line(payload, wave_h=1.2)


CHECKS = [
    calm_onshore_is_excellent,
    calm_cross_shore_is_excellent,
    gusts_12_to_14_are_caution_even_with_light_wind,
    gusts_15_or_more_delay_sup,
    offshore_light_wind_is_never_excellent,
    offshore_from_5_ms_is_stricter,
    offshore_with_strong_gusts_is_stricter,
    missing_sup_evidence_is_fail_closed,
    wrong_day_or_mixed_window_is_fail_closed,
    malformed_time_does_not_shift_metric_arrays,
    russian_direction_wording_is_preserved,
    format_v2_guard_uses_sup_period_not_previous_city_gust,
    format_v2_guard_cannot_upgrade_or_hide_unsafe_sup,
    format_v2_guard_rejects_partial_evidence_without_touching_other_sports,
    tomorrow_hourly_builds_existing_kite_recommendation,
    current_only_never_builds_positive_water_recommendation,
    today_hourly_plus_current_never_builds_positive_water_recommendation,
    missing_tomorrow_direction_or_gust_is_fail_closed_for_kite,
    malformed_timestamp_keeps_weather_arrays_at_original_indices,
    current_sentinels_do_not_change_final_evening_format_v2,
    safe_surf_and_sup_recommendations_preserve_existing_behavior,
    missing_or_malformed_tomorrow_wind_is_fail_closed_for_surf,
]


def main() -> int:
    for check in CHECKS:
        check()
    print(f"OK: {len(CHECKS)} Cyprus SUP safety checks")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
