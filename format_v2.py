#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""FORMAT_V2 text transformer for Cyprus VayboMeter posts."""
from __future__ import annotations

import datetime as dt
import re

CY_LAT, CY_LON = 34.707, 33.022


def _is_sep(line: str) -> bool:
    s = line.strip()
    return bool(s) and set(s) <= {"—", "-", "─"}


def _plain(line: str) -> str:
    return re.sub(r"</?b>", "", str(line or "")).strip()


def _date_from_title(text: str) -> str:
    m = re.search(r"\((\d{2}\.\d{2}\.\d{4})\)", text)
    return m.group(1) if m else ""


def _section_after(lines: list[str], marker: str) -> list[str]:
    out: list[str] = []
    capture = False
    for line in lines:
        if marker in line:
            capture = True
            continue
        if capture:
            if _is_sep(line):
                break
            if line.strip():
                out.append(line.strip())
    return out


def _astro_lines(lines: list[str]) -> list[str]:
    keep = []
    for line in lines:
        s = line.strip()
        if not s:
            continue
        if s.startswith(("🌅 Рассвет", "🌇 Закат", "➿", "✅", "💚", "⚫️", "🌙", "🌘")):
            keep.append(s)
    return keep[:4]


def _storm_line(lines: list[str]) -> str:
    for line in lines:
        if "Шторм" in line or "шторм" in line:
            return line.strip()
    return ""


def _compact_warning(line: str) -> str:
    s = str(line or "").strip()
    s = re.sub(r"^⚠️\s*", "", s)
    s = s.replace("<b>Штормовое предупреждение</b>:", "Штормовое предупреждение:")
    return s.strip()


def _city_names(lines: list[str]) -> list[str]:
    names: list[str] = []
    for line in lines:
        p = _plain(line)
        m = re.match(r"^[^А-ЯA-Z]*([А-ЯA-Z][^:]+):", p)
        if m:
            names.append(m.group(1).strip())
    return names


def _hashtags(lines: list[str], fallback: str) -> str:
    for line in reversed(lines):
        s = line.strip()
        if s.startswith("#"):
            return s
    return fallback


def _first_content_line(lines: list[str]) -> str:
    for line in lines[1:]:
        s = line.strip()
        if s and not _is_sep(s) and not s.startswith("#"):
            return s
    return ""


def _morning_pick(lines: list[str], prefixes: tuple[str, ...]) -> list[str]:
    return [x.strip() for x in lines if x.strip().startswith(prefixes)]


def _temperature_note(greeting: str) -> str:
    """Extract only the useful weather part from the long greeting/fact line."""
    s = _plain(greeting)
    m = re.search(r"(Теплее всего\s*[—-].+)$", s)
    return "🌡 " + m.group(1).strip() if m else ""


def _clean_today_tip(line: str) -> str:
    s = str(line or "").strip()
    s = re.sub(r"^✅\s*Сегодня:\s*", "", s)
    s = s.rstrip(".")
    return s


def _clean_kp_line(line: str) -> str:
    s = str(line or "").strip()
    s = re.sub(r"(\b(?:Kp|Кр)\s*\d+(?:[\.,]\d+)?)\s*\([^)]*\)", r"\1", s, flags=re.I)
    s = re.sub(r"\s{2,}", " ", s).strip()
    return s


def _clean_uv_line(line: str) -> str:
    s = _plain(line).strip()
    m = re.search(r"УФ-индекс\s*(\d+(?:[\.,]\d+)?)\s*\(([^)]+)\)\s*:\s*(.+)$", s, flags=re.I)
    if m:
        value = m.group(1).replace(",", ".")
        label_raw = m.group(2).strip().lower()
        advice = m.group(3).strip()
        label_map = {
            "low": "низкий",
            "moderate": "умеренный",
            "medium": "умеренный",
            "high": "высокий",
            "very high": "очень высокий",
            "extreme": "экстремальный",
        }
        label = label_map.get(label_raw, label_raw)
        return f"☀️ УФ {value:g} — {label}: {advice}" if value.replace('.', '', 1).isdigit() else f"☀️ УФ {value} — {label}: {advice}"
    s = re.sub(r"^☀️\s*<b>УФ-индекс\s*", "☀️ УФ ", s, flags=re.I)
    s = re.sub(r"</?b>", "", s)
    s = re.sub(r"\((Very High|High|Moderate|Low|Extreme)\)", lambda mm: "— " + {"Very High": "очень высокий", "High": "высокий", "Moderate": "умеренный", "Low": "низкий", "Extreme": "экстремальный"}.get(mm.group(1), mm.group(1)), s)
    s = re.sub(r"\s{2,}", " ", s).strip()
    return s


def _to_float(value) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except Exception:
        return None


def _pick(mapping: dict, *keys):
    for key in keys:
        if key in mapping and mapping.get(key) is not None:
            return mapping.get(key)
    return None


def _kmh_to_ms(value) -> float | None:
    x = _to_float(value)
    if x is None:
        return None
    try:
        from utils import kmh_to_ms as _repo_kmh_to_ms  # type: ignore
        return float(_repo_kmh_to_ms(x))
    except Exception:
        return float(x) / 3.6


def _compass(deg) -> str | None:
    x = _to_float(deg)
    if x is None:
        return None
    try:
        from utils import compass as _repo_compass  # type: ignore
        return str(_repo_compass(int(round(x))))
    except Exception:
        dirs = ["С", "СВ", "В", "ЮВ", "Ю", "ЮЗ", "З", "СЗ"]
        return dirs[int((x + 22.5) // 45) % 8]


def _parse_target_date(date_s: str) -> dt.date:
    try:
        return dt.datetime.strptime(date_s, "%d.%m.%Y").date()
    except Exception:
        try:
            from zoneinfo import ZoneInfo
            return dt.datetime.now(ZoneInfo("Asia/Nicosia")).date()
        except Exception:
            return dt.date.today()


def _parse_hourly_time(value) -> dt.datetime | None:
    try:
        s = str(value).replace("Z", "+00:00")
        return dt.datetime.fromisoformat(s)
    except Exception:
        return None


def _nearest_index(times: list, target_date: dt.date, prefer_hour: int) -> int | None:
    best_i = None
    best_diff = None
    target_min = prefer_hour * 60
    for i, raw_t in enumerate(times or []):
        parsed = _parse_hourly_time(raw_t)
        if parsed is None or parsed.date() != target_date:
            continue
        minute = parsed.hour * 60 + parsed.minute
        diff = abs(minute - target_min)
        if best_diff is None or diff < best_diff:
            best_i, best_diff = i, diff
    return best_i


def _value_at(arr, idx: int | None) -> float | None:
    if idx is None or not isinstance(arr, list) or idx >= len(arr):
        return None
    return _to_float(arr[idx])


def _source_wind_pressure_line(date_s: str) -> str:
    """Fetch current source weather and build a compact wind/gust/pressure line when data exists."""
    try:
        from weather import get_weather  # type: ignore
        wm = get_weather(CY_LAT, CY_LON) or {}
    except Exception:
        return ""

    target_date = _parse_target_date(date_s)
    hourly = wm.get("hourly") or {}
    current = wm.get("current") or wm.get("current_weather") or {}
    times = hourly.get("time") or hourly.get("time_local") or hourly.get("timestamp") or []

    idx_day = _nearest_index(times, target_date, 12)
    idx_morn = _nearest_index(times, target_date, 6)

    spd_arr = _pick(hourly, "windspeed_10m", "windspeed", "wind_speed_10m", "wind_speed") or []
    gust_arr = _pick(hourly, "windgusts_10m", "wind_gusts_10m", "wind_gusts", "windgusts") or []
    dir_arr = _pick(hourly, "winddirection_10m", "winddirection", "wind_dir_10m", "wind_dir", "wind_direction_10m") or []
    prs_arr = _pick(hourly, "surface_pressure", "pressure_msl", "pressure") or []

    wind_ms = _kmh_to_ms(_value_at(spd_arr, idx_day))
    wind_dir = _value_at(dir_arr, idx_day)
    pressure = _value_at(prs_arr, idx_day)
    pressure_morn = _value_at(prs_arr, idx_morn)

    gust_ms = None
    day_gusts: list[float] = []
    for i, raw_t in enumerate(times or []):
        parsed = _parse_hourly_time(raw_t)
        if parsed is None or parsed.date() != target_date:
            continue
        g = _value_at(gust_arr, i)
        if g is not None:
            day_gusts.append(g)
    if day_gusts:
        gust_ms = _kmh_to_ms(max(day_gusts))

    if wind_ms is None:
        wind_ms = _kmh_to_ms(_pick(current, "windspeed", "wind_speed", "wind_speed_10m"))
    if wind_dir is None:
        wind_dir = _to_float(_pick(current, "winddirection", "wind_dir", "wind_direction_10m"))
    if gust_ms is None:
        gust_ms = _kmh_to_ms(_pick(current, "wind_gusts_10m", "wind_gusts", "windgusts"))
    if pressure is None:
        pressure = _to_float(_pick(current, "surface_pressure", "pressure_msl", "pressure"))

    parts: list[str] = []
    if isinstance(wind_ms, (int, float)):
        wind_part = f"💨 Ветер: {float(wind_ms):.1f} м/с"
        c = _compass(wind_dir)
        if c:
            wind_part += f" ({c})"
        if isinstance(gust_ms, (int, float)):
            wind_part += f" • порывы до {float(gust_ms):.0f} м/с"
        parts.append(wind_part)
    elif isinstance(gust_ms, (int, float)):
        parts.append(f"💨 Порывы до {float(gust_ms):.0f} м/с")

    if isinstance(pressure, (int, float)):
        trend = "→"
        if isinstance(pressure_morn, (int, float)):
            diff = float(pressure) - float(pressure_morn)
            trend = "↑" if diff >= 0.3 else "↓" if diff <= -0.3 else "→"
        parts.append(f"🔹 {int(round(float(pressure)))} гПа {trend}")

    return " • ".join(parts)


def _legacy_wind_pressure_line(lines: list[str]) -> str:
    for line in lines:
        s = line.strip()
        if s.startswith("💨") or s.startswith("🔹"):
            return re.sub(r"\bпорывы до (\d+)(?!\s*м/с)", r"порывы до \1 м/с", s)
    return ""


def build_morning_format_v2(region_name: str, safe_legacy_text: str) -> str:
    """Compact morning post: only actionable weather, air, UV, valid Kp, wind/pressure and short plan."""
    lines = [x.rstrip() for x in str(safe_legacy_text or "").splitlines() if x.strip()]
    date_s = _date_from_title(safe_legacy_text)
    title_date = f" ({date_s})" if date_s else ""

    greeting = _first_content_line(lines)
    temp_note = _temperature_note(greeting)
    warning = _storm_line(lines)
    weather_line = _legacy_wind_pressure_line(lines) or _source_wind_pressure_line(date_s)
    uv = _morning_pick(lines, ("☀️", "🌞", "🔥"))
    sun = _morning_pick(lines, ("🌇",))
    air = _morning_pick(lines, ("🏭", "🌫", "🌬", "🌿", "🫁", "💨", "🟢", "🟡", "🔴", "ℹ️"))
    space = [x for x in _morning_pick(lines, ("🧲",)) if "н/д" not in x]
    today_tips = _morning_pick(lines, ("✅ Сегодня",))
    tags = _hashtags(lines, "#Кипр #погода #здоровье #Никосия #Тродос")

    out: list[str] = [f"<b>🌅 Кипр сегодня{title_date}</b>"]

    if temp_note:
        out.append(temp_note)
    if weather_line:
        out.append(weather_line)
    if warning:
        out.append("⚠️ " + _compact_warning(warning))
    if uv:
        out.append(_clean_uv_line(uv[0]))
    if air:
        out.append(air[0])
    if space:
        out.append(_clean_kp_line(space[0]))
    if sun:
        out.append(sun[0])

    plan = _clean_today_tip(today_tips[0]) if today_tips else "вода, SPF, тень 11–16, прогулка до полудня"
    if warning:
        out.append("✅ План: " + plan + "; у моря ориентируйся на фактический ветер.")
    else:
        out.append("✅ План: " + plan + ".")

    out.append(tags)
    return "\n".join(out).strip()


def build_evening_format_v2(region_name: str, safe_legacy_text: str) -> str:
    lines = [x.rstrip() for x in str(safe_legacy_text or "").splitlines()]
    date_s = _date_from_title(safe_legacy_text)
    storm = _storm_line(lines)
    sea = _section_after(lines, "Морские города")
    inland = _section_after(lines, "Континентальные города")
    astro = _astro_lines(lines)

    sea_names = _city_names(sea)

    has_storm = bool(storm)
    has_troodos = any("Тродос" in x for x in inland)
    has_nicosia = any("Никос" in x for x in inland)

    title_date = f" ({date_s})" if date_s else ""
    out: list[str] = [f"<b>🌅 Кипр завтра: прогноз с поправкой на остров{title_date}</b>", ""]

    out.append("🧭 <b>Главный сценарий</b>")
    if has_storm:
        out.append("Главный фактор дня — ветер и порывы. На побережье условия могут быстро меняться по часам, особенно у открытых пляжей, на пирсах и на трассах вдоль моря.")
    else:
        out.append("Остров живёт разными микросценариями: у моря мягче и ветренее, в Никосии заметно теплее, а Тродос держит отдельный горный режим.")
    if has_nicosia and has_troodos:
        out.append("Никосия — самый тёплый внутренний сценарий; Тродос — прохладнее и чувствительнее к ветру/облачности.")
    out.append("")

    out.append("🎯 <b>Уверенность прогноза</b>")
    out.append("✅ Температура: высокая — общий диапазон по городам устойчивый.")
    out.append("🟡 Ветер у моря: средняя — порывы и направление лучше проверить утром.")
    out.append("🟡 Туман/дымка: локально, особенно утром и у моря.")
    out.append("✅ Давление/общий фон: можно использовать для планирования дня.")
    out.append("")

    if storm:
        out.append("⚠️ <b>Предупреждение</b>")
        out.append(_compact_warning(storm))
        out.append("")

    if sea:
        out.append("🌊 <b>Побережье</b>")
        out.extend(sea)
        out.append("")

    if inland:
        out.append("🏙 <b>Центр и горы</b>")
        out.extend(inland)
        out.append("")

    out.append("🌊 <b>Островная поправка</b>")
    if sea_names:
        out.append("У моря смотри не только температуру, но и порывы: при одинаковых градусах ощущение в Ларнаке, Лимассоле, Айя-Напе и Пафосе может отличаться заметно.")
    else:
        out.append("На Кипре короткие расстояния не гарантируют одинаковую погоду: побережье, центр острова и горы часто расходятся по ветру и ощущаемой температуре.")
    out.append("")

    if astro:
        out.append("☀️ <b>Солнце и ритм дня</b>")
        out.extend(astro)
        out.append("")

    out.append("📌 <b>Вывод</b>")
    if has_storm:
        out.append("Планируй день гибко: прогулки у моря лучше переносить на более спокойные часы, а перед выездом перепроверить ветер и порывы.")
    else:
        out.append("Хороший день для обычных дел, но с поправкой на микроклимат: море, Никосия и Тродос завтра ощущаются как разные погодные зоны.")
    out.append("")
    out.append("#Кипр #погода #здоровье #Никосия #Тродос")
    return "\n".join(out).strip()


def build_format_v2(region_name: str, mode: str, safe_legacy_text: str) -> str:
    mode_s = (mode or "").strip().lower()
    if mode_s.startswith("morn"):
        return build_morning_format_v2(region_name, safe_legacy_text)
    return build_evening_format_v2(region_name, safe_legacy_text)
