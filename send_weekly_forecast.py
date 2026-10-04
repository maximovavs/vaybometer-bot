#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Weekly Cyprus VayboMeter forecast post."""
from __future__ import annotations

import argparse
import asyncio
import html
import json
import os
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from editorial_voice import build_weekly_meaning
from settings_cy import INLAND_CITIES, MARINE_CITIES
from weekly_cover import render_weekly_cover
from weekly_snapshot import (
    build_snapshot_candidate as _make_weekly_snapshot,
    derive_delta_lines,
    finalize_snapshot,
    load_snapshot_file,
    write_snapshot_atomic,
)

REGION_NAME = "Кипр"
SNAPSHOT_REGION = "cyprus"
TZ_STR = os.getenv("TZ", "Asia/Nicosia")
ISLAND_POINTS = [*MARINE_CITIES.items(), *INLAND_CITIES.items()]
SEA_POINTS = list(MARINE_CITIES.items())
HASHTAGS = "#Кипр #вайбнедели #погода #море #астропогода"

MONTHS_RU = {
    1: "января",
    2: "февраля",
    3: "марта",
    4: "апреля",
    5: "мая",
    6: "июня",
    7: "июля",
    8: "августа",
    9: "сентября",
    10: "октября",
    11: "ноября",
    12: "декабря",
}

RAIN_CODES = {51, 53, 55, 56, 57, 61, 63, 65, 66, 67, 80, 81, 82, 95, 96, 99}
FORBIDDEN_PHRASES = (
    "аварии",
    "чрезвычайные ситуации",
    "операции лучше отложить",
    "воздушном пространстве",
    "неприятности в воздушном пространстве",
)


def _today() -> date:
    return datetime.now().date()


def _fmt_week_range(start: date) -> str:
    end = start + timedelta(days=6)
    if start.month == end.month:
        return f"{start.day:02d}–{end.day:02d} {MONTHS_RU[end.month]}"
    return f"{start.day:02d} {MONTHS_RU[start.month]}–{end.day:02d} {MONTHS_RU[end.month]}"


def _week_dates(start: date) -> list[date]:
    return [start + timedelta(days=i) for i in range(7)]


def _num(value: Any) -> float | None:
    try:
        if value in (None, "", "н/д"):
            return None
        return float(str(value).replace(",", "."))
    except Exception:
        return None


def _fmt_num(value: float, digits: int = 0) -> str:
    return f"{value:.0f}" if digits == 0 else f"{value:.{digits}f}"


def _safe_text(value: Any) -> str:
    text = html.escape(str(value or "").strip(), quote=False)
    low = text.lower()
    if any(phrase in low for phrase in FORBIDDEN_PHRASES):
        return ""
    return text


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _fetch_weather(start: date | None = None) -> dict[str, Any]:
    try:
        from weather import get_weekly_weather  # type: ignore
    except Exception:
        return {}
    start = start or _today()
    end = start + timedelta(days=6)
    out: dict[str, Any] = {}
    for city, coords in ISLAND_POINTS:
        try:
            payload = get_weekly_weather(
                *coords,
                start_date=start.isoformat(),
                end_date=end.isoformat(),
                tz_name=TZ_STR,
            ) or {}
        except Exception:
            payload = {}
        if isinstance(payload, dict):
            out[city] = payload
    return out


def _fetch_air() -> dict[str, Any]:
    try:
        from air import get_air_for_cities  # type: ignore

        return get_air_for_cities(ISLAND_POINTS) or {}
    except Exception:
        return {}


def _fetch_sea_temps() -> list[float]:
    temps: list[float] = []
    try:
        from air import get_sst  # type: ignore
    except Exception:
        return temps
    for _name, coords in SEA_POINTS:
        try:
            value = get_sst(*coords)
        except Exception:
            value = None
        if isinstance(value, (int, float)):
            temps.append(float(value))
    return temps


def _fetch_kp() -> tuple[float | None, str, int | None, str]:
    try:
        from air import get_kp  # type: ignore

        return get_kp()
    except Exception:
        return None, "н/д", None, "n/d"


def _daily_rows(weather_payload: dict[str, Any], start: date) -> list[dict[str, Any]]:
    daily = weather_payload.get("daily") if isinstance(weather_payload, dict) else {}
    if not isinstance(daily, dict):
        return []
    times = daily.get("time") or daily.get("date") or []
    dates = _week_dates(start)
    if not isinstance(times, list) or not times:
        return []
    normalized_times = [str(item)[:10] for item in times]

    def arr(name: str) -> list[Any]:
        value = daily.get(name) or []
        return value if isinstance(value, list) else []

    keys = {
        "tmax": ("temperature_2m_max",),
        "tmin": ("temperature_2m_min",),
        "wind": ("wind_speed_10m_max", "windspeed_10m_max"),
        "gust": ("wind_gusts_10m_max", "windgusts_10m_max"),
        "wave": ("wave_height_max", "wave_height", "wave_max"),
        "rain_prob": ("precipitation_probability_max",),
        "precip_sum": ("precipitation_sum",),
        "code": ("weathercode", "weather_code"),
        "uv": ("uv_index_max",),
    }
    arrays: dict[str, list[Any]] = {}
    for out_key, aliases in keys.items():
        arrays[out_key] = []
        for alias in aliases:
            arrays[out_key] = arr(alias)
            if arrays[out_key]:
                break

    rows: list[dict[str, Any]] = []
    for idx in range(7):
        row_date = dates[idx]
        wanted = row_date.isoformat()
        try:
            src_idx = normalized_times.index(wanted)
        except ValueError:
            continue
        row = {"date": row_date}
        for key, values in arrays.items():
            row[key] = values[src_idx] if src_idx < len(values) else None
        rows.append(row)
    return rows


def _weather_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    tmax = [_num(row.get("tmax")) for row in rows]
    tmin = [_num(row.get("tmin")) for row in rows]
    wind = [_num(row.get("wind")) for row in rows]
    gust = [_num(row.get("gust")) for row in rows]
    wave = [_num(row.get("wave")) for row in rows]
    uv = [_num(row.get("uv")) for row in rows]
    rain_prob = [_num(row.get("rain_prob")) for row in rows]
    precip_sum = [_num(row.get("precip_sum")) for row in rows]
    codes = [_num(row.get("code")) for row in rows]
    clean = lambda items: [x for x in items if isinstance(x, (int, float))]
    return {
        "tmax_min": min(clean(tmax), default=None),
        "tmax_max": max(clean(tmax), default=None),
        "tmin_min": min(clean(tmin), default=None),
        "wind_max": max(clean(wind), default=None),
        "gust_max": max(clean(gust), default=None),
        "wave_max": max(clean(wave), default=None),
        "uv_max": max(clean(uv), default=None),
        "precipitation_sum_total": sum(clean(precip_sum)) if clean(precip_sum) else None,
        "rain": any((x or 0) >= 40 for x in rain_prob if x is not None)
        or any(int(x) in RAIN_CODES for x in codes if x is not None),
    }


def _payload_weekly_coverage(payload: dict[str, Any], start: date) -> tuple[int, bool]:
    expected = [day.isoformat() for day in _week_dates(start)]
    daily = payload.get("daily") if isinstance(payload, dict) else {}
    if not isinstance(daily, dict):
        return 0, False
    raw_dates = daily.get("time") or daily.get("date") or []
    returned = [str(item)[:10] for item in raw_dates if item is not None] if isinstance(raw_dates, list) else []
    coverage_days = len(set(expected).intersection(returned))
    exact_dates = len(returned) == 7 and len(set(returned)) == 7 and returned == expected
    meta = payload.get("_weekly_meta") if isinstance(payload.get("_weekly_meta"), dict) else None
    if meta is not None:
        exact_dates = (
            exact_dates
            and meta.get("coverage_complete") is True
            and meta.get("weather_code_system") == "wmo"
            and meta.get("normalized_units", {}).get("wind_speed") == "m/s"
            and meta.get("normalized_units", {}).get("precipitation") == "mm"
        )
    return coverage_days, exact_dates


def _weather_payloads(weather_payload: dict[str, Any]) -> list[dict[str, Any]]:
    if not isinstance(weather_payload, dict):
        return []
    if isinstance(weather_payload.get("daily"), dict):
        return [weather_payload]
    return [value for value in weather_payload.values() if isinstance(value, dict)]


def _weather_metrics_for_payload(weather_payload: dict[str, Any], start: date) -> dict[str, Any]:
    payloads = _weather_payloads(weather_payload)
    rows: list[dict[str, Any]] = []
    coverages: list[tuple[int, bool]] = []
    for payload in payloads:
        rows.extend(_daily_rows(payload, start))
        coverages.append(_payload_weekly_coverage(payload, start))
    metrics = _weather_metrics(rows)
    expected_payloads = 1 if isinstance(weather_payload.get("daily"), dict) else len(ISLAND_POINTS)
    metrics["coverage_days"] = min((days for days, _complete in coverages), default=0)
    metrics["coverage_complete"] = (
        len(payloads) == expected_payloads
        and bool(coverages)
        and all(complete for _days, complete in coverages)
    )
    return metrics


def _snapshot_weather_days(weather_payload: dict[str, Any], start: date) -> list[dict[str, Any]]:
    payloads = _weather_payloads(weather_payload)
    if len(payloads) != len(ISLAND_POINTS):
        return []
    city_rows = [_daily_rows(payload, start) for payload in payloads]
    result: list[dict[str, Any]] = []
    for target in _week_dates(start):
        matches = [next((row for row in rows if row.get("date") == target), None) for rows in city_rows]
        if any(row is None for row in matches):
            return []
        rows = [row for row in matches if isinstance(row, dict)]
        def complete_values(key: str) -> list[float] | None:
            values=[_num(row.get(key)) for row in rows]
            return [float(value) for value in values] if all(value is not None for value in values) else None
        highs=complete_values("tmax"); lows=complete_values("tmin"); winds=complete_values("wind"); gusts=complete_values("gust")
        rain_known=all(_num(row.get("rain_prob")) is not None or _num(row.get("code")) is not None for row in rows)
        rainy=(any((_num(row.get("rain_prob")) or 0)>=40 or int(_num(row.get("code")) or -1) in RAIN_CODES for row in rows) if rain_known else None)
        result.append({"date":target.isoformat(),"tmax":max(highs) if highs else None,"tmin":min(lows) if lows else None,
            "wind":max(winds) if winds else None,"gust":max(gusts) if gusts else None,"rainy":rainy,"precip_sum":None})
    return result

def _build_snapshot_candidate(start: date, weather_payload: dict[str, Any], sea_temps: list[float]) -> dict[str, Any] | None:
    metrics=_weather_metrics_for_payload(weather_payload,start)
    return _make_weekly_snapshot(region=SNAPSHOT_REGION,week_start=start,weather_days=_snapshot_weather_days(weather_payload,start),
        weather_coverage_days=int(metrics.get("coverage_days") or 0),weather_coverage_complete=metrics.get("coverage_complete") is True,
        sea_temps=sea_temps,expected_sea_samples=len(SEA_POINTS))


def _aggregate_air_data(air_data: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(air_data, dict):
        return {}
    if any(key in air_data for key in ("aqi", "pm25", "pm10")):
        return air_data
    aggregated: dict[str, Any] = {}
    for key in ("aqi", "pm25", "pm10"):
        values = [
            _num(city_data.get(key))
            for city_data in air_data.values()
            if isinstance(city_data, dict)
        ]
        available = [value for value in values if value is not None]
        if available:
            aggregated[key] = max(available)
    return aggregated


def _main_background(metrics: dict[str, Any]) -> str:
    if metrics.get("coverage_complete") is False:
        return "Полный прогноз на все 7 дней пока не подтверждён; погодные ориентиры недели уточнятся ближе к датам."
    hot = isinstance(metrics.get("tmax_max"), (int, float)) and metrics["tmax_max"] >= 33
    uv_high = isinstance(metrics.get("uv_max"), (int, float)) and metrics["uv_max"] >= 6
    windy = isinstance(metrics.get("gust_max"), (int, float)) and metrics["gust_max"] >= 10
    rainy = bool(metrics.get("rain"))
    if hot and uv_high:
        return "жаркая неделя с высоким УФ; у моря легче, но дневное солнце требует пауз."
    if windy:
        return "тёплая неделя, но у моря порывы могут менять ощущение дня."
    if rainy:
        return "неделя с локальными осадками; планы лучше держать гибкими."
    return "ровная тёплая неделя для обычных дел, моря и спокойного ритма."


def _weather_line(metrics: dict[str, Any]) -> str:
    if metrics.get("coverage_complete") is False:
        return "Полный прогноз на все 7 дней пока не собран; температуру, осадки и ветер лучше уточнять ближе к датам."
    if isinstance(metrics.get("tmax_min"), (int, float)) and isinstance(metrics.get("tmax_max"), (int, float)):
        line = f"Температура держится в диапазоне {_fmt_num(metrics['tmax_min'])}–{_fmt_num(metrics['tmax_max'])}°C"
    else:
        line = "Погодные данные обновятся ближе к неделе"
    if metrics.get("rain"):
        line += "; локальные дождевые окна лучше проверять утром."
    elif isinstance(metrics.get("gust_max"), (int, float)) and metrics["gust_max"] >= 10:
        line += "; у моря заметны порывы, выбирай защищённые места."
    elif isinstance(metrics.get("uv_max"), (int, float)) and metrics["uv_max"] >= 6:
        line += "; дневной УФ высокий, лучше смещать активность на утро."
    else:
        line += "; резких погодных акцентов не видно."
    return line


def _sea_line(sea_temps: list[float] | None) -> str:
    values = [float(x) for x in sea_temps or [] if isinstance(x, (int, float))]
    if values:
        low, high = min(values), max(values)
        if round(low) == round(high):
            return f"Средняя вода около {_fmt_num(sum(values) / len(values))}°C; лучше утром или ближе к закату."
        return f"Средняя вода {_fmt_num(low)}–{_fmt_num(high)}°C; лучше утром или ближе к закату."
    return "Данные по воде обновятся ближе к неделе; у берега комфортнее утром и вечером."


def _water_sport_lines(metrics: dict[str, Any]) -> list[str]:
    gust = metrics.get("gust_max")
    has_gust = isinstance(gust, (int, float))

    sup = "SUP: короткие утренние окна в защищённых бухтах."
    if has_gust and gust > 15:
        kite = "Кайт/винг: только опытным; порывы могут быть резкими."
    else:
        kite = "Кайт/винг: рабочие окна только для уверенных; порывы проверять по споту."
    return [
        sup,
        kite,
        "Серф: зависит от фактической волны; скорее не главный сценарий недели.",
    ]


def _air_line(air_data: dict[str, Any]) -> tuple[str, bool]:
    aqi = _num(air_data.get("aqi"))
    pm25 = _num(air_data.get("pm25"))
    pm10 = _num(air_data.get("pm10"))
    parts: list[str] = []
    if aqi is not None:
        label = "низкий" if aqi <= 50 else "умеренный" if aqi <= 100 else "высокий" if aqi <= 150 else "очень высокий"
        parts.append(f"AQI {_fmt_num(aqi)} ({label})")
    pm: list[str] = []
    if pm25 is not None:
        pm.append(f"PM₂.₅ {_fmt_num(pm25)}")
    if pm10 is not None:
        pm.append(f"PM₁₀ {_fmt_num(pm10)}")
    if pm:
        parts.append(" / ".join(pm))
    poor = (aqi is not None and aqi >= 100) or (pm25 is not None and pm25 >= 20) or (pm10 is not None and pm10 >= 50)
    if not parts:
        return "Текущий снимок воздуха: данных пока нет.", False
    line = "Текущий снимок воздуха: " + " • ".join(parts) + "."
    if poor:
        line += " Воздух неидеален: активность на улице короче, окна лучше закрывать в часы пыли/дымки."
    return line, poor


def _space_line(kp_tuple: tuple[Any, ...] | None) -> tuple[str, bool]:
    kp = _num(kp_tuple[0]) if kp_tuple else None
    if kp is None:
        return "Текущий снимок Kp: данных пока нет; это не прогноз на всю неделю.", False
    if kp >= 5:
        return f"Сейчас Kp {kp:.1f}: фон повышен; это текущий снимок, а не прогноз на всю неделю.", True
    if kp >= 4:
        return f"Сейчас Kp {kp:.1f}: фон умеренно повышен; это текущий снимок, а не прогноз на всю неделю.", True
    return f"Сейчас Kp {kp:.1f}: фон спокойный; это текущий снимок, а не прогноз на всю неделю.", False


def _parse_voc_part(value: Any) -> tuple[str | None, str | None]:
    text = _safe_text(value)
    iso = re.search(r"\b\d{4}-(\d{1,2})-(\d{1,2})[T\s]+(\d{1,2}:\d{2})", text)
    if iso:
        month, day, time = iso.groups()
        return f"{int(day):02d}.{int(month):02d}", time
    match = re.search(r"\b(?:(\d{1,2})[./](\d{1,2})\s+)?(\d{1,2}:\d{2})", text)
    if not match:
        return None, None
    day, month, time = match.groups()
    if day and month:
        return f"{int(day):02d}.{int(month):02d}", time
    return None, time


def _format_voc_interval(day: date, start_raw: Any, end_raw: Any) -> str:
    default_date = f"{day.day:02d}.{day.month:02d}"
    start_date, start_time = _parse_voc_part(start_raw)
    end_date, end_time = _parse_voc_part(end_raw)
    if not start_time or not end_time:
        return ""
    start_date = start_date or default_date
    end_date = end_date or start_date
    start_label = f"{start_date} {start_time}"
    end_label = end_time if end_date == start_date else f"{end_date} {end_time}"
    return f"{start_label}–{end_label}"


def _calendar_days(lunar_data: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(lunar_data, dict):
        return {}
    days = lunar_data.get("days")
    return days if isinstance(days, dict) else lunar_data


def _load_lunar_calendar(path: Path = Path("lunar_calendar.json")) -> dict[str, Any]:
    data = _load_json(path)
    return data if isinstance(data, dict) else {}


def _load_astro_events(start: date, paths: list[Path] | None = None) -> list[dict[str, Any]]:
    if paths is None:
        paths = [
            Path("data") / "astro_events_monthly.json",
            Path("data") / f"astro_events_{start.year}_{start.month:02d}.json",
        ]
    events: list[dict[str, Any]] = []
    week = {d.isoformat() for d in _week_dates(start)}
    for path in paths:
        data = _load_json(path)
        if not isinstance(data, list):
            continue
        for item in data:
            if isinstance(item, dict) and str(item.get("date", ""))[:10] in week:
                title = _safe_text(item.get("title"))
                tone = _safe_text(item.get("tone"))
                advice = _safe_text(item.get("advice"))
                if title:
                    events.append({"date": str(item.get("date"))[:10], "title": title, "tone": tone, "advice": advice})
    return events


def _lunar_lines(start: date, lunar_data: dict[str, Any] | None, astro_events: list[dict[str, Any]]) -> list[str]:
    days = _calendar_days(lunar_data)
    week = _week_dates(start)
    records = [(d, days.get(d.isoformat(), {})) for d in week if isinstance(days.get(d.isoformat(), {}), dict)]
    out: list[str] = []
    for d, rec in records:
        phase = str(rec.get("phase_name") or rec.get("phase") or "")
        low = phase.lower()
        if "полн" in low:
            out.append(f"🌕 {d.day:02d}.{d.month:02d}: Полнолуние — лучше закрывать хвосты и подводить итоги.")
            break
        if "новол" in low:
            out.append(f"🌑 {d.day:02d}.{d.month:02d}: Новолуние — мягко планировать новое без рывков.")
            break
    percents = [_num(rec.get("percent") or rec.get("illumination")) for _d, rec in records]
    percents = [p for p in percents if p is not None]
    if percents:
        out.append(f"✨ Освещённость Луны: примерно {_fmt_num(percents[0])}→{_fmt_num(percents[-1])}% — темп недели лучше держать ровным.")
    voc = []
    for d, rec in records:
        raw = rec.get("void_of_course") or rec.get("voc")
        if isinstance(raw, dict) and raw.get("start") and raw.get("end"):
            interval = _format_voc_interval(d, raw["start"], raw["end"])
            if interval:
                voc.append(interval)
    if voc:
        out.append("⚫️ VoC: " + "; ".join(voc[:2]) + " — не перегружать расписание.")
    for event in astro_events[:2]:
        tail = "; ".join(x for x in (event.get("tone"), event.get("advice")) if x)
        out.append(f"🪐 {event['date'][8:10]}.{event['date'][5:7]}: {event['title']}" + (f" — {tail}." if tail else "."))
    if not out:
        out.append("Лунный фон недели спокойный: планируй важное без спешки и оставляй буфер.")
    return out[:4]


def _plan_lines(metrics: dict[str, Any], poor_air: bool, elevated_kp: bool, lunar_lines: list[str]) -> list[str]:
    lines = ["• важное и активное планировать на утро;"]
    if isinstance(metrics.get("uv_max"), (int, float)) and metrics["uv_max"] >= 6:
        lines.append("• держать воду, SPF и тень в дневные часы;")
    else:
        lines.append("• оставлять буфер на дорогу и бытовые дела;")
    if poor_air:
        lines.append("• при пыли/дымке сокращать активность на улице;")
    if elevated_kp or any("VoC" in line for line in lunar_lines):
        lines.append("• не перегружать дни с нестабильным фоном, важное подтверждать дважды;")
    lines.append("• море планировать утром или ближе к закату.")
    return lines[:5]


def build_weekly_forecast(
    start: date | None = None,
    *,
    weather_payload: dict[str, Any] | None = None,
    air_data: dict[str, Any] | None = None,
    sea_temps: list[float] | None = None,
    kp_tuple: tuple[Any, ...] | None = None,
    lunar_data: dict[str, Any] | None = None,
    astro_events_paths: list[Path] | None = None,
    delta_lines: list[str] | None = None,
) -> str:
    start = start or _today()
    weather_payload = weather_payload if weather_payload is not None else _fetch_weather(start)
    air_data = air_data if air_data is not None else _fetch_air()
    sea_temps = sea_temps if sea_temps is not None else _fetch_sea_temps()
    kp_tuple = kp_tuple if kp_tuple is not None else _fetch_kp()
    lunar_data = lunar_data if lunar_data is not None else _load_lunar_calendar()
    astro_events = _load_astro_events(start, astro_events_paths)

    metrics = _weather_metrics_for_payload(weather_payload or {}, start)
    air, poor_air = _air_line(_aggregate_air_data(air_data or {}))
    space, elevated_kp = _space_line(kp_tuple)
    lunar = _lunar_lines(start, lunar_data, astro_events)
    decision_metrics = metrics if metrics.get("coverage_complete") is not False else {}
    plan = _plan_lines(decision_metrics, poor_air, elevated_kp, lunar)
    water_sport = _water_sport_lines(decision_metrics)
    weekly_meaning = build_weekly_meaning(REGION_NAME, start, decision_metrics)
    delta_section = ["", "↔️ К прошлому недельному прогнозу", *delta_lines] if delta_lines else []

    lines = [
        f"🗓 Вайб недели: {_fmt_week_range(start)}",
        "",
        "✨ Главный фон недели",
        _main_background(metrics),
        "",
        "🌿 Смысл недели",
        weekly_meaning,
        "",
        "🌦 Погода",
        _weather_line(metrics),
        "",
        "🌊 Море",
        _sea_line(sea_temps),
        "",
        "🏄 Вода и спорт",
        *water_sport,
        "",
        "🏭 Воздух сейчас",
        air,
        "",
        "🧲 Космопогода сейчас",
        space,
        "",
        "🌙 Луна и астроритм (интерпретация)",
        *lunar,
        *delta_section,
        "",
        "✅ Как прожить неделю",
        *plan,
        "",
        HASHTAGS,
    ]
    text = "\n".join(lines).strip()
    for phrase in FORBIDDEN_PHRASES:
        text = re.sub(re.escape(phrase), "", text, flags=re.I)
    return re.sub(r"\n{3,}", "\n\n", text)


async def _send(
    text: str, chat_id: str, start: date, *, snapshot_candidate: dict[str, Any] | None = None,
    production_snapshot_out: Path | None = None, production_chat_id: str = "",
) -> int | None:
    from telegram import Bot, constants  # type: ignore
    token=os.getenv("TELEGRAM_TOKEN","").strip()
    if not token: raise SystemExit("TELEGRAM_TOKEN is not set")
    destination=int(chat_id) if str(chat_id).lstrip("-").isdigit() else chat_id
    bot=Bot(token=token)
    try:
        cover=render_weekly_cover(text,start=start,output_path=Path("weekly_cover.png"))
        with Path(cover["path"]).open("rb") as photo:
            await bot.send_photo(chat_id=destination,photo=photo,caption=f"Вайб недели: {_fmt_week_range(start)}")
    except Exception as exc:
        print(f"Weekly cover unavailable; text will still be sent: {type(exc).__name__}: {exc}",file=sys.stderr)
    message=await bot.send_message(chat_id=destination,text=text,parse_mode=constants.ParseMode.HTML,disable_web_page_preview=True)
    message_id=getattr(message,"message_id",None)
    if (snapshot_candidate is not None and production_snapshot_out is not None and str(production_chat_id).strip()
        and str(chat_id)==str(production_chat_id) and isinstance(message_id,int) and not isinstance(message_id,bool) and message_id>0):
        write_snapshot_atomic(production_snapshot_out,finalize_snapshot(snapshot_candidate,message_id))
        print(f"WEEKLY_SNAPSHOT: {production_snapshot_out}")
    return message_id if isinstance(message_id,int) and not isinstance(message_id,bool) else None

async def main() -> None:
    parser=argparse.ArgumentParser(description="Build/send weekly Cyprus VayboMeter forecast")
    parser.add_argument("--date",default="",help="Week start date, YYYY-MM-DD. Defaults to today.")
    parser.add_argument("--send",action="store_true")
    parser.add_argument("--chat-id",default=os.getenv("CHANNEL_ID",""))
    parser.add_argument("--cover-out",default="",help="Optional path for a deterministic weekly cover preview.")
    parser.add_argument("--production-snapshot-out",default="",help="Write authoritative WeeklySnapshot only after successful production text send.")
    args=parser.parse_args()
    start=datetime.strptime(args.date,"%Y-%m-%d").date() if args.date else _today()
    weather_payload=_fetch_weather(start); air_data=_fetch_air(); sea_temps=_fetch_sea_temps(); kp_tuple=_fetch_kp(); lunar_data=_load_lunar_calendar()
    candidate=_build_snapshot_candidate(start,weather_payload,sea_temps)
    previous=load_snapshot_file(Path(os.getenv("WEEKLY_PREVIOUS_SNAPSHOT","weekly_snapshot_state/previous.json")),
        region=SNAPSHOT_REGION,expected_week_start=(start-timedelta(days=7)).isoformat())
    delta_lines=derive_delta_lines(candidate,previous,region=SNAPSHOT_REGION) if candidate else []
    text=build_weekly_forecast(start,weather_payload=weather_payload,air_data=air_data,sea_temps=sea_temps,kp_tuple=kp_tuple,
        lunar_data=lunar_data,delta_lines=delta_lines)
    print(text)
    if args.cover_out:
        metadata=render_weekly_cover(text,start=start,output_path=args.cover_out)
        print("WEEKLY_COVER: "+json.dumps(metadata,ensure_ascii=False,sort_keys=True))
    if args.send:
        if not args.chat_id: raise SystemExit("--chat-id or CHANNEL_ID is required for sending")
        await _send(text,args.chat_id,start,snapshot_candidate=candidate,
            production_snapshot_out=Path(args.production_snapshot_out) if args.production_snapshot_out else None,
            production_chat_id=os.getenv("CHANNEL_ID",""))

if __name__=="__main__":
    asyncio.run(main())
