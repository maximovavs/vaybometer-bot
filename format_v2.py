#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""FORMAT_V2 text transformer for Cyprus VayboMeter posts."""
from __future__ import annotations

import re


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


def build_morning_format_v2(region_name: str, safe_legacy_text: str) -> str:
    """Compact morning post: only actionable weather, air, UV and short plan."""
    lines = [x.rstrip() for x in str(safe_legacy_text or "").splitlines() if x.strip()]
    date_s = _date_from_title(safe_legacy_text)
    title_date = f" ({date_s})" if date_s else ""

    greeting = _first_content_line(lines)
    temp_note = _temperature_note(greeting)
    warning = _storm_line(lines)
    uv = _morning_pick(lines, ("☀️", "🌞", "🔥"))
    sun = _morning_pick(lines, ("🌇",))
    air = _morning_pick(lines, ("🏭", "🌫", "🌬", "🌿", "🫁", "💨", "🟢", "🟡", "🔴", "ℹ️"))
    today_tips = _morning_pick(lines, ("✅ Сегодня",))
    tags = _hashtags(lines, "#Кипр #погода #здоровье #Никосия #Тродос")

    out: list[str] = [f"<b>🌅 Кипр сегодня{title_date}</b>"]

    if temp_note:
        out.append(temp_note)
    if warning:
        out.append("⚠️ " + _compact_warning(warning))
    if uv:
        out.append(uv[0])
    if air:
        out.append(air[0])
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
