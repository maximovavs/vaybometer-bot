#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""FORMAT_V2 text transformer for Cyprus VayboMeter posts."""
from __future__ import annotations

import datetime as dt
import json
import os
import re
from pathlib import Path

from visibility_context import has_structured_visibility_alert


CY_LAT, CY_LON = 34.707, 33.022


def _is_sep(line: str) -> bool:
    s = line.strip()
    return bool(s) and set(s) <= {"—", "-", "─"}


def _plain(line: str) -> str:
    return re.sub(r"</?b>", "", str(line or "")).strip()


def _date_from_title(text: str) -> str:
    m = re.search(r"\((\d{2}\.\d{2}\.\d{4})\)", text)
    return m.group(1) if m else ""


def _cy_sea_temp_bounds(date_s: str) -> tuple[float, float]:
    try:
        month = dt.datetime.strptime(date_s, "%d.%m.%Y").month
    except Exception:
        month = 7
    if 6 <= month <= 10:
        return 22.0, 34.0
    return 15.0, 29.0


def _valid_cy_sea_temp(value: float, date_s: str) -> bool:
    low, high = _cy_sea_temp_bounds(date_s)
    return low <= value <= high


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


_MOON_PHASE_PREFIXES = ("🌑", "🌒", "🌓", "🌔", "🌕", "🌖", "🌗", "🌘", "🌙")
_ZODIAC_SYMBOLS = "♈♉♊♋♌♍♎♏♐♑♒♓"


def _normalize_zodiac_symbol_suffix(line: str) -> str:
    return re.sub(rf"(в\s+[{_ZODIAC_SYMBOLS}])(?:[а-яё]+)\b", r"\1", str(line or ""), flags=re.I)


def _is_illumination_line(line: str) -> bool:
    return line.startswith("✨") and ("%" in line or "освещ" in line.lower())


def _is_moon_text_line(line: str) -> bool:
    s = str(line or "").strip()
    return bool(
        re.search(r"\b(?:полнолуние|луна|новолуние)\b", s, flags=re.I)
        or re.search(r"\b(?:full moon|moon|new moon)\b", s, flags=re.I)
    )


def _is_general_background_line(line: str) -> bool:
    return line.startswith(("✅", "⚠️", "➿")) and "общий фон" in line.lower()


def _is_astro_candidate(line: str) -> bool:
    return (
        line.startswith(("🌅 Рассвет", "🌇 Закат"))
        or line.startswith(_MOON_PHASE_PREFIXES)
        or _is_moon_text_line(line)
        or _is_illumination_line(line)
        or _is_general_background_line(line)
        or line.startswith(("💚 В плюсе", "⚫️"))
    )


def _first_matching(lines: list[str], predicate) -> str:
    return next((line for line in lines if predicate(line)), "")


def _append_unique(out: list[str], line: str) -> None:
    if line and line not in out:
        out.append(line)


def _astro_lines(lines: list[str]) -> list[str]:
    keep: list[str] = []
    for line in lines:
        s = line.strip()
        if not s:
            continue
        if s.startswith("<b>"):
            continue
        if _is_astro_candidate(s):
            keep.append(_normalize_zodiac_symbol_suffix(s))
    return keep


def _storm_line(lines: list[str]) -> str:
    for line in lines:
        if "Шторм" in line or "шторм" in line:
            return line.strip()
    return ""


_STORM_NEGATION_RE = re.compile(
    r"шторм\w*\s+не\s+ожида|без\s+шторма|штормов\w*\s+предупрежден\w*\s+нет|риск\s+шторма\s+низк",
    re.I,
)
_STORM_POSITIVE_RE = re.compile(r"\b(?:шторм\w*|шквал\w*)\b", re.I)


def _line_has_actual_storm_signal(line: str) -> bool:
    text = _plain(line)
    if _STORM_NEGATION_RE.search(text):
        return False
    return bool(_STORM_POSITIVE_RE.search(text))


def _has_actual_storm_signal(text: str, gust_max: float | None = None) -> bool:
    if isinstance(gust_max, (int, float)) and gust_max >= 15:
        return True
    return any(_line_has_actual_storm_signal(line) for line in str(text or "").splitlines())


def _evening_storm_line(lines: list[str]) -> str:
    for line in lines:
        if _line_has_actual_storm_signal(line):
            return line.strip()
    for line in lines:
        gust = _max_gust_ms(line)
        if isinstance(gust, (int, float)) and gust >= 15:
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


def _first_line_starts(lines: list[str], prefixes: tuple[str, ...]) -> str:
    for line in lines:
        s = line.strip()
        if s.startswith(prefixes):
            return s
    return ""


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


def _clean_evening_astro(lines: list[str]) -> list[str]:
    raw = _astro_lines(lines)
    out: list[str] = []
    _append_unique(out, _first_matching(raw, lambda s: s.startswith("🌅 Рассвет")))
    _append_unique(out, _first_matching(raw, lambda s: s.startswith("🌇 Закат")))
    _append_unique(out, _first_matching(raw, lambda s: s.startswith(_MOON_PHASE_PREFIXES)))
    _append_unique(out, _first_matching(raw, _is_illumination_line))
    _append_unique(out, _first_matching(raw, _is_general_background_line))
    _append_unique(out, _first_matching(raw, lambda s: s.startswith("💚 В плюсе")))
    _append_unique(out, _first_matching(raw, lambda s: s.startswith("⚫️")))
    return out[:7]


def _clean_morning_astro(lines: list[str]) -> list[str]:
    raw = _astro_lines(lines)
    moon = _first_matching(raw, lambda s: s.startswith(_MOON_PHASE_PREFIXES) or (_is_moon_text_line(s) and not _is_illumination_line(s)))
    illum = _first_matching(raw, _is_illumination_line)
    out: list[str] = []
    _append_unique(out, _compact_morning_moon_line(moon, illum))
    _append_unique(out, _first_matching(raw, _is_general_background_line))
    _append_unique(out, _first_matching(raw, lambda s: s.startswith("💚 В плюсе")))
    _append_unique(out, _first_matching(raw, lambda s: s.startswith("⚫️")))
    return out[:6]


def _compact_morning_moon_line(moon_line: str, illumination_line: str) -> str:
    moon = str(moon_line or "").strip()
    m = re.search(r"(\d{1,3})\s*%", str(illumination_line or ""))
    if not moon:
        if m:
            pct = int(m.group(1))
            if pct >= 95:
                return f"🌕 Луна: полнолуние, {pct}% освещённости."
        return ""
    if re.search(r"\b\d{1,3}%\s+освещ", moon, flags=re.I):
        return moon
    if not m:
        return moon if moon.startswith(_MOON_PHASE_PREFIXES) else "🌕 " + moon
    moon = moon.rstrip(" .")
    if "—" in moon:
        moon = moon.split("—", 1)[0].strip()
    if not moon.startswith(_MOON_PHASE_PREFIXES):
        moon = "🌕 " + moon
    return f"{moon} — {m.group(1)}% освещённости."


def _has_any(text: str, words: tuple[str, ...]) -> bool:
    low = _plain(text).lower()
    return any(word in low for word in words)


def _max_wind_ms(text: str) -> float | None:
    values: list[float] = []
    for m in re.finditer(r"(\d+(?:[\.,]\d+)?)\s*м/с", text, flags=re.I):
        try:
            values.append(float(m.group(1).replace(",", ".")))
        except Exception:
            continue
    return max(values) if values else None


def _max_gust_ms(text: str) -> float | None:
    values: list[float] = []
    for m in re.finditer(r"(?:порыв\w*|gust\w*)\D{0,18}(\d+(?:[\.,]\d+)?)\s*м/с", text, flags=re.I):
        try:
            values.append(float(m.group(1).replace(",", ".")))
        except Exception:
            continue
    return max(values) if values else None


def _max_temperature_c(text: str) -> float | None:
    values: list[float] = []
    for m in re.finditer(r"(-?\d+(?:[\.,]\d+)?)\s*/\s*-?\d+(?:[\.,]\d+)?\s*°C", text):
        try:
            values.append(float(m.group(1).replace(",", ".")))
        except Exception:
            continue
    return max(values) if values else None


def _air_quality_values(text: str) -> dict[str, float]:
    values: dict[str, float] = {}
    for key, pattern in {
        "aqi": r"\bAQI\s*(\d+(?:[\.,]\d+)?)",
        "pm25": r"(?:PM₂\.₅|PM2\.?5)\s*(\d+(?:[\.,]\d+)?)",
        "pm10": r"(?:PM₁₀|PM10)\s*(\d+(?:[\.,]\d+)?)",
    }.items():
        m = re.search(pattern, text, flags=re.I)
        if not m:
            continue
        try:
            values[key] = float(m.group(1).replace(",", "."))
        except Exception:
            pass
    return values


def _has_poor_air_signal(text: str) -> bool:
    values = _air_quality_values(text)
    official_level = re.search(r"официальн\w*\s+уров\w*\s*(\d)\s*/\s*4", _plain(text), flags=re.I)
    if (
        values.get("aqi", 0) >= 100
        or values.get("pm25", 0) >= 20
        or values.get("pm10", 0) >= 50
        or (official_level and int(official_level.group(1)) >= 3)
    ):
        return True
    low = re.sub(r"\bпыльца\w*", "", _plain(text).lower(), flags=re.I)
    return bool(
        re.search(
            r"воздух\s+неидеален|пыль\s+в\s+воздухе|пылев\w+\s+дымк\w*|задымлен\w*|\bдым\s*/\s*смог\b|(?<![а-яё])дым(?!к|[а-яё])|(?<![а-яё])смог(?![а-яё])|air-quality\s+alert",
            low,
            flags=re.I,
        )
    )


def _has_visibility_haze(text: str) -> bool:
    return has_structured_visibility_alert(text)


def _is_forecast_air_line(line: str) -> bool:
    low = _plain(line).strip().lower()
    if not low.startswith(("🏭", "🏙")):
        return False
    return bool(
        re.search(r"\bвоздух\s+завтра(?:\s+утром)?\b", low)
        or re.search(r"\bпрогноз\w*\s+(?:воздуха|aqi)\b", low)
        or re.search(r"\baqi\s+завтра(?:\s+утром)?\b", low)
    )


def _forecast_air_lines(lines: list[str]) -> list[str]:
    return [line for line in lines if _is_forecast_air_line(line)]


def _has_structured_dust_evidence(text: str, *, forecast_only: bool = False) -> bool:
    for raw_line in str(text or "").splitlines():
        line = _plain(raw_line).strip()
        low = line.lower()
        if line.startswith("🌫 Видимость:"):
            if re.search(r"пылев\w*\s+дымк|сух\w*\s+пыл", low, flags=re.I):
                return True
            continue
        if not line.startswith(("🏭", "🏙")):
            continue
        if forecast_only and not _is_forecast_air_line(line):
            continue
        if re.search(r"пылев\w*\s+дымк|пыль\s+в\s+воздухе|задымлен|\bсмог\b", low, flags=re.I):
            return True
    return False


def _format_reason_list(reasons: list[str]) -> str:
    if not reasons:
        return ""
    if len(reasons) == 1:
        return reasons[0]
    if len(reasons) == 2:
        return f"{reasons[0]} и {reasons[1]}"
    return ", ".join(reasons[:-1]) + " и " + reasons[-1]


def _normalize_reason_list(reasons: list[str]) -> list[str]:
    out: list[str] = []
    by_key: dict[str, int] = {}

    def key_for(reason: str) -> str:
        low = reason.lower()
        if "порыв" in low or ("ветер" in low and "мор" in low):
            return "wind_sea"
        if "жара" in low or "тепло" in low:
            return "heat"
        if "пыль" in low or "дым" in low or "aqi" in low or "воздух" in low:
            return "air"
        if "дожд" in low or "осад" in low or "гроз" in low:
            return "rain"
        return low

    def better(new: str, old: str) -> bool:
        new_low, old_low = new.lower(), old.lower()
        if "сильная жара" in new_low and "сильная жара" not in old_low:
            return True
        if "порыв" in new_low and "порыв" not in old_low:
            return True
        return False

    for raw in reasons:
        reason = re.sub(r"\s+", " ", str(raw or "")).strip(" .;—-")
        if not reason:
            continue
        key = key_for(reason)
        if key in by_key:
            idx = by_key[key]
            if better(reason, out[idx]):
                out[idx] = reason
            continue
        by_key[key] = len(out)
        out.append(reason)
    return out


def _normalize_evening_score_reasons(score_line: str) -> str:
    s = str(score_line or "").strip()
    if ";" not in s:
        return s
    prefix, tail = s.split(";", 1)
    reasons = [x.strip() for x in re.split(r"\s*,\s*|\s+и\s+", tail.strip(" .")) if x.strip()]
    normalized = _normalize_reason_list(reasons)
    if not normalized:
        return prefix.rstrip(" .") + "."
    return f"{prefix.rstrip()} ; {_format_reason_list(normalized)}.".replace(" ;", ";")


def _evening_flags(lines: list[str]) -> dict[str, bool]:
    text = "\n".join(lines)
    max_wind = _max_wind_ms(text)
    max_gust = _max_gust_ms(text)
    max_temp = _max_temperature_c(text)
    forecast_air_text = "\n".join(_forecast_air_lines(lines))
    forecast_poor_air = _has_poor_air_signal(forecast_air_text)
    forecast_dust = _has_structured_dust_evidence(text, forecast_only=True)
    visibility_haze = _has_visibility_haze(text)
    return {
        "storm": _has_actual_storm_signal(text, max_gust),
        "rain": _has_any(text, ("дожд", "ливн", "гроза", "осад")),
        "dust": forecast_dust,
        "poor_air": forecast_poor_air,
        "visibility_haze": visibility_haze and not forecast_dust,
        "heat": _has_any(text, ("жара", "жарко", "перегрев")) or (isinstance(max_temp, (int, float)) and max_temp >= 33),
        "wind": _has_any(text, ("порыв", "сильный ветер", "шторм")) or (isinstance(max_wind, (int, float)) and max_wind >= 7),
        "local": _has_any(text, ("локаль", "местами", "неравномер", "по часам", "микросценар")),
        "troodos": _has_any(text, ("тродос", "горы", "горн")),
        "uv": _has_any(text, ("уф", "uv", "spf")),
        "astro_unfavorable": _has_any(text, ("неблагоприят", "не перегруж", "напряж", "сложн", "осторожнее")),
    }


def _polish_evening_score(score_line: str, flags: dict[str, bool]) -> str:
    s = str(score_line or "").strip()
    if not s:
        return ""
    caution_count = sum(
        1
        for key in ("storm", "rain", "dust", "heat", "wind", "astro_unfavorable")
        if flags.get(key)
    )
    should_soften = (
        "хорошо" in s.lower()
        and (
            (flags.get("heat") and flags.get("wind"))
            or flags.get("storm")
            or flags.get("rain")
            or flags.get("astro_unfavorable")
            or caution_count >= 2
        )
    )
    if not should_soften:
        return _normalize_evening_score_reasons(s)

    m = re.match(r"^(✨\s*VayboMeter(?:\s+завтра)?:\s*\d+(?:[\.,]\d+)?/10)\s*[—-]\s*", s)
    prefix = m.group(1) if m else re.sub(r"\s*[—-]\s*.*$", "", s).strip()
    score_match = re.search(r"(\d+(?:[\.,]\d+)?)\s*/\s*10", prefix)
    if score_match:
        try:
            source_score = float(score_match.group(1).replace(",", "."))
        except Exception:
            source_score = None
        if isinstance(source_score, (int, float)):
            penalty = 0.0
            if flags.get("rain"):
                penalty += 0.6
            if flags.get("wind"):
                penalty += 0.4
            if flags.get("heat"):
                penalty += 0.3
            if flags.get("dust"):
                penalty += 0.4
            if flags.get("storm"):
                penalty += 0.8
            target_score = max(5.5, min(source_score, source_score - penalty))
            prefix = re.sub(r"\d+(?:[\.,]\d+)?\s*/\s*10", f"{target_score:.1f}/10", prefix, count=1)
    if flags.get("heat") and flags.get("wind"):
        reason = "жара и порывы у моря"
    elif flags.get("astro_unfavorable"):
        reason = "астрофон требует мягкого режима"
    elif flags.get("rain") or flags.get("storm"):
        reason = "локальные осадки и порывы требуют запаса по времени"
    elif flags.get("dust"):
        reason = "дымка/пыль требуют проверки воздуха"
    else:
        reason = "есть несколько факторов осторожности"
    return _normalize_evening_score_reasons(f"{prefix} — с оговорками; {reason}.")


def _evening_main_scenario(flags: dict[str, bool], score_line: str) -> str:
    low = (score_line or "").lower()
    if flags["storm"]:
        return "🧭 Главное завтра: сильные порывы у моря задают режим дня."
    if flags["rain"]:
        return "🧭 Главное завтра: день неоднородный по острову."
    if flags["dust"]:
        return "🧭 Главное завтра: пыль/дымка влияют на воздух и видимость; утром лучше сверить AQI/PM."
    if flags.get("poor_air"):
        return "🧭 Главное завтра: прогноз воздуха требует более щадящей активности на улице."
    if flags.get("visibility_haze"):
        return "🧭 Главное завтра: утром местами дымка/туман; на дороге и у побережья лучше проверить видимость."
    if flags["heat"] and flags["wind"]:
        return "🧭 Главное завтра: жара внутри острова и порывы у моря задают режим дня."
    if flags["heat"]:
        return "🧭 Главное завтра: главная нагрузка — жара, активность лучше сместить на утро и вечер."
    if flags["wind"]:
        return "🧭 Главное завтра: основной фактор — ветер у моря и открытых участков."
    if flags["troodos"]:
        return "🧭 Главное завтра: заметен контраст побережья, центра острова и Тродоса."
    if low:
        reason = re.sub(r"^.*?—\s*", "", score_line).strip(" .")
        return "🧭 Главное завтра: " + (reason[0].lower() + reason[1:] if reason else "день подходит для обычных дел") + "."
    return "🧭 Главное завтра: спокойный день для обычных дел и прогулок."


def _evening_nuance(flags: dict[str, bool], has_sea: bool, has_inland: bool) -> str:
    if flags["storm"]:
        return "⚠️ Нюанс: у открытого моря и на трассах вдоль берега порывы могут быть сильнее средних значений."
    if flags["rain"]:
        if flags.get("troodos") and has_sea:
            return "⚠️ Главный нюанс: осадки возможны локально, особенно в горах; у моря жарко и порывисто."
        return "⚠️ Главный нюанс: осадки возможны локально; по районам погода может отличаться сильнее среднего прогноза."
    if flags["dust"]:
        return "⚠️ Нюанс: при пыли/дыме чувствительным людям лучше сократить активность на улице."
    if flags.get("poor_air"):
        return "⚠️ Нюанс: чувствительным людям лучше сократить интенсивную активность на улице."
    if flags.get("visibility_haze"):
        return "⚠️ Нюанс: воздух по текущим данным чистый, но локальная дымка может ухудшать видимость."
    if flags["heat"] and has_inland:
        return "⚠️ Нюанс: в Никосии и внутри острова жарче, чем на побережье."
    if flags["wind"] and has_sea:
        return "⚠️ Нюанс: у моря ощущение меняют порывы, а не только температура."
    if flags["uv"]:
        return "⚠️ Нюанс: дневное солнце требует SPF, воды и тени."
    if flags["troodos"] and has_inland:
        return "⚠️ Нюанс: Тродос может ощущаться заметно прохладнее центра острова."
    return ""


def _evening_confidence_line(flags: dict[str, bool]) -> str:
    if flags["storm"] or flags["rain"] or flags["local"]:
        if flags["rain"] and flags["wind"]:
            return "🎯 Уверенность: температура надёжна; по горам и порывам возможны уточнения утром."
        if flags["rain"]:
            return "🎯 Уверенность: температура надёжна; по локальной погоде возможны уточнения утром."
        return "🎯 Уверенность: температура надёжна; порывы лучше перепроверить утром."
    return ""


def _evening_plan(flags: dict[str, bool]) -> str:
    if flags["storm"]:
        return "✅ План завтра: защищённый берег, короткие перемещения и без лишнего риска у открытого моря."
    if flags["rain"]:
        return "✅ План завтра: запасной indoor-вариант; радар — перед выездом."
    if flags["heat"] and flags["wind"]:
        return "✅ План завтра: основные дела утром/вечером, днём — вода и тень; у моря выбрать защищённое место."
    if flags["heat"]:
        return "✅ План завтра: активность до полудня или после заката, днём — вода, тень и SPF."
    if flags["wind"]:
        return "✅ План завтра: прогулки у моря — в защищённых местах, ветер перепроверить утром."
    if flags["dust"]:
        return "✅ План завтра: утром оценить видимость/воздух, прогулку сделать короче при дымке."
    if flags.get("poor_air"):
        return "✅ План завтра: утром сверить прогноз воздуха и при повышенных значениях выбрать спокойную нагрузку."
    if flags.get("visibility_haze"):
        return "✅ План завтра: утром проверить видимость, особенно для дороги и побережья."
    return "✅ План завтра: обычные дела и прогулки, с короткой проверкой ветра и солнца утром."


def _clean_air_line(line: str) -> str:
    s = _plain(line).strip()
    if "воздух по городам" in s.lower():
        return _clean_city_air_line(s)
    if re.search(r"официальн\w*\s+уров|AirQuality CY|📡 IQAir|🛰 OM|свежих наблюдений нет|время наблюдения", s, flags=re.I):
        return s
    aqi_match = re.search(r"\bAQI\s*(\d+|н/д)", s, flags=re.I)
    pm25_match = re.search(r"(?:PM₂\.₅|PM2\.?5)\s*(\d+)", s, flags=re.I)
    pm10_match = re.search(r"(?:PM₁₀|PM10)\s*(\d+)", s, flags=re.I)
    if not aqi_match:
        return s

    parts = [f"AQI {aqi_match.group(1)}"]
    label_match = re.search(r"\bAQI\s*(?:\d+|н/д)\s*\(([^)]+)\)", s, flags=re.I)
    if label_match:
        parts[0] += f" ({label_match.group(1).strip()})"

    pm_parts: list[str] = []
    if pm25_match:
        pm_parts.append(f"PM₂.₅ {pm25_match.group(1)}")
    if pm10_match:
        pm_parts.append(f"PM₁₀ {pm10_match.group(1)}")
    if pm_parts:
        parts.append(" / ".join(pm_parts))

    pollen = ""
    pollen_match = re.search(r"🌿\s*пыльца\s*:\s*([^•;\n]+)", s, flags=re.I)
    if pollen_match:
        raw_pollen = pollen_match.group(1).strip().lower()
        if raw_pollen.startswith(("низ", "low")):
            pollen = "низкая"
        elif raw_pollen.startswith(("умер", "сред", "moder")):
            pollen = "умеренная"
        elif raw_pollen.startswith(("выс", "high")):
            pollen = "высокая"
        else:
            pollen = raw_pollen

    city_bits = []
    for chunk in re.split(r"\s*[;•]\s*", s):
        if re.search(r"\b(Никос|Ларнак|Лимассол|Пафос|Айя|Тродос)\b", chunk, flags=re.I):
            city_bits.append(chunk.strip())
    low = s.lower()
    if "воздух сейчас" in low:
        prefix = "🏭 Воздух сейчас: "
    elif "воздух завтра утром" in low:
        prefix = "🏭 Воздух завтра утром: "
    elif "воздух завтра" in low:
        prefix = "🏭 Воздух завтра: "
    elif "прогноз воздуха" in low:
        prefix = "🏭 Прогноз воздуха: "
    else:
        prefix = "🏭 Воздух: "
    main = prefix + " • ".join(parts)
    if pollen:
        main += f" • 🌿 пыльца: {pollen}"
    if city_bits:
        return main + "\n" + "🏙 По городам: " + "; ".join(city_bits[:3])
    return main


def _clean_city_air_line(line: str) -> str:
    body = re.sub(r"^🏭\s*Воздух по городам\s*:\s*", "", _plain(line).strip(), flags=re.I)
    if re.search(r"\b(?:ур\.|уровень)\s*\d\s*/\s*4", body, flags=re.I):
        return "🏭 Воздух по городам: " + body
    city_re = r"Никосия|Лимассол|Ларнака|Пафос|Айя-Напа|Тродос"
    pollutant_re = r"PM₂\.₅|PM2\.?5|PM₁₀|PM10|NO₂|NO2|O₃|O3|SO₂|SO2|CO"
    value_re = r"\d+(?:[\.,]\d+)?"
    chunks: list[str] = []
    token_re = rf"({city_re})\s+([🟢🟡🟠🔴])(?P<tail>.*?)(?=(?:\s*·\s*|\s+{city_re}\s+[🟢🟡🟠🔴]|$))"
    for m in re.finditer(token_re, body, flags=re.I):
        city, marker = m.group(1), m.group(2)
        tail = (m.group("tail") or "").strip()
        pollutant = ""
        pollutant_match = re.search(rf"({pollutant_re})(?:\s*({value_re}))?", tail, flags=re.I)
        if pollutant_match:
            pollutant = pollutant_match.group(1)
            pollutant = pollutant.replace("PM10", "PM₁₀").replace("PM2.5", "PM₂.₅").replace("PM25", "PM₂.₅")
            pollutant = pollutant.replace("NO2", "NO₂").replace("O3", "O₃").replace("SO2", "SO₂")
            value = pollutant_match.group(2)
            if value:
                pollutant = f"{pollutant} {value.replace(',', '.')}"
        chunks.append(f"{city} {marker}" + (f" ({pollutant})" if pollutant else ""))
    if chunks:
        return "🏭 Воздух по городам: " + " · ".join(chunks[:6])
    return "🏭 Воздух по городам: " + body


def _air_health_recommendation(line: str) -> str:
    s = _plain(line).strip()
    values: dict[str, float] = {}
    for key, pattern in {
        "aqi": r"\bAQI\s*(\d+(?:[\.,]\d+)?)",
        "pm25": r"(?:PM₂\.₅|PM2\.?5)\s*(\d+(?:[\.,]\d+)?)",
        "pm10": r"(?:PM₁₀|PM10)\s*(\d+(?:[\.,]\d+)?)",
    }.items():
        match = re.search(pattern, s, flags=re.I)
        if match:
            try:
                values[key] = float(match.group(1).replace(",", "."))
            except Exception:
                pass
    official_level = re.search(r"официальн\w*\s+уров\w*\s*(\d)\s*/\s*4", s, flags=re.I)
    if (
        values.get("aqi", 0) >= 100
        or values.get("pm25", 0) >= 20
        or values.get("pm10", 0) >= 50
        or (official_level and int(official_level.group(1)) >= 3)
    ):
        return "poor_air"
    return ""


def _fmt_temp(value: float) -> str:
    return f"{value:.0f}" if float(value).is_integer() else f"{value:.1f}"


def _air_lines(lines: list[str]) -> list[str]:
    out: list[str] = []
    for line in lines:
        s = line.strip()
        low = s.lower()
        if not s.startswith(("🏭", "🏙")):
            continue
        if "частный датчик" in low or "safecast" in low or "радиа" in low:
            continue
        if (
            "aqi" not in low
            and "воздух по городам" not in low
            and "официальный уровень" not in low
            and "наблюдений нет" not in low
            and "оценка недоступна" not in low
        ):
            continue
        cleaned = _clean_air_line(s)
        for part in cleaned.splitlines():
            part = part.strip()
            if part and part not in out:
                out.append(part)
    return out[:2]


def _air_is_poor(lines: list[str]) -> bool:
    return any(_air_health_recommendation(line) for line in lines)


def _poor_air_advice_line(air_lines: list[str], evidence_text: str, *, forecast: bool = False) -> str:
    scope = ""
    for line in air_lines:
        match = re.search(r"наблюдение\s+в\s+([А-ЯЁа-яё-]+)", _plain(line), flags=re.I)
        if match:
            scope = f"В {match.group(1)} "
            break
    if not scope and any("воздух по городам" in _plain(line).lower() for line in air_lines):
        scope = "В отдельных городах "
    if forecast:
        scope = "По прогнозу "

    subject = f"{scope}воздух" if scope else "Воздух"
    advice = f"😷 {subject} неидеален: чувствительным людям лучше сократить интенсивную активность на улице."
    if _has_structured_dust_evidence(evidence_text, forecast_only=forecast):
        advice += " В часы подтверждённой пылевой дымки окна лучше держать закрытыми."
    return advice


def _critical_safecast_cy_line(lines: list[str]) -> str:
    for line in lines:
        s = line.strip()
        low = s.lower()
        if "safecast" not in low and "радиа" not in low and "частный датчик" not in low:
            continue
        critical = bool(re.search(r"critical|alert|опасн|критич|🔴", s, flags=re.I))
        if not critical:
            continue
        if "safecast" not in low:
            continue
        body = re.sub(r"^🧪\s*", "", s).strip()
        body = re.sub(r"^Safecast(?:\s*CY)?\s*:?\s*", "", body, flags=re.I).strip()
        return "🧪 Safecast CY: " + body
    return ""


def _morning_sea_line(lines: list[str]) -> str:
    waters: list[float] = []
    wave_value = None
    sea_lines: list[str] = []
    date_s = _date_from_title("\n".join(lines))

    for line in lines:
        s = _plain(line).replace("\u00a0", " ").strip()
        low = s.lower()
        if "закат" in low or "рассвет" in low or re.search(r"\b(?:aqi|pm₂|pm2|pm₁|pm10|гпа|hpa|давл|ветер|уф)\b", low, flags=re.I):
            continue
        if not re.search(r"🌊|\bвода\b|\bsea\b|\bволна\b", s, flags=re.I):
            continue
        sea_lines.append(s)
        if "🌊" in s:
            tail = s.split("🌊", 1)[1]
            nums: list[float] = []
            for raw_num in re.findall(r"([+-]?\d+(?:[\.,]\d+)?)", tail):
                try:
                    nums.append(float(raw_num.replace(",", ".")))
                except Exception:
                    continue
            if nums and _valid_cy_sea_temp(nums[0], date_s):
                waters.append(nums[0])
            if wave_value is None and len(nums) >= 2 and 0 <= nums[1] <= 5:
                wave_value = nums[1]

    sea_text = "\n".join(sea_lines)
    for line in sea_lines:
        for pattern in (
            r"(?:\bвода\b|\bsea\b)[^\d+-]{0,12}([+-]?\d+(?:[\.,]\d+)?)\s*°?\s*C?",
            r"([+-]?\d+(?:[\.,]\d+)?)\s*°?\s*C?\s*(?:\bвода\b|\bsea\b)",
        ):
            match = re.search(pattern, line, flags=re.I)
            if match:
                try:
                    value = float(match.group(1).replace(",", "."))
                    if _valid_cy_sea_temp(value, date_s):
                        waters.append(value)
                except Exception:
                    pass
                break

    wave = ""
    if isinstance(wave_value, (int, float)):
        wave = "спокойная" if wave_value < 0.5 else "умеренная"
    low = sea_text.lower()
    if not wave and re.search(r"спокойн|штиль|calm", low):
        wave = "спокойная"
    elif not wave and re.search(r"умерен|moderate|средн", low):
        wave = "умеренная"
    elif not wave and re.search(r"волн|wave|неспокой", low):
        wave = "умеренная"

    if waters:
        if len(waters) >= 2:
            avg = sum(waters) / len(waters)
            water_part = f"средняя вода {_fmt_temp(avg)}°C"
        elif waters:
            water_part = f"вода {_fmt_temp(waters[0])}°C"
        else:
            water_part = "вода комфортная"
        wave_part = f"волна {wave}" if wave else "волна спокойная"
        if len(waters) >= 2:
            return f"🌊 Море: {water_part}; лучше до 11:00 или после 18:30."
        return f"🌊 Море: {water_part}; {wave_part}; лучше до 11:00 или после 18:30."

    return "🌊 Море: данные о температуре воды обновляются; лучше до 11:00 или после 18:30."


def _clean_uv_line(line: str) -> str:
    s = _plain(line).strip()
    m = re.search(r"УФ-индекс\s*(\d+(?:[\.,]\d+)?)\s*\(([^)]+)\)\s*:\s*(.+)$", s, flags=re.I)
    if m:
        value = m.group(1).replace(",", ".")
        value_txt = re.sub(r"\.0$", "", value)
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
        return f"☀️ УФ {value_txt} — {label}: {advice}"
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


def _parse_target_date(date_s: str) -> dt.date | None:
    try:
        return dt.datetime.strptime(date_s, "%d.%m.%Y").date()
    except Exception:
        return None


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
    """Build wind, gust and pressure only from hourly data for the title date."""
    target_date = _parse_target_date(date_s)
    if target_date is None:
        return ""

    try:
        from weather import get_weather  # type: ignore
        wm = get_weather(CY_LAT, CY_LON) or {}
    except Exception:
        return ""

    hourly = wm.get("hourly") or {}
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


def _find_numeric_key(value, wanted: tuple[str, ...]) -> float | None:
    if isinstance(value, dict):
        for key, raw in value.items():
            key_norm = str(key).lower().replace("-", "_")
            if key_norm in wanted:
                try:
                    return float(raw)
                except Exception:
                    pass
        for raw in value.values():
            found = _find_numeric_key(raw, wanted)
            if found is not None:
                return found
    elif isinstance(value, list):
        for item in value:
            found = _find_numeric_key(item, wanted)
            if found is not None:
                return found
    return None


def _safecast_private_sensor_line() -> str:
    path = Path(os.getenv("CY_SAFECAST_FILE", "data/safecast_cy.json"))
    if not path.exists():
        return ""
    try:
        max_age_h = float(os.getenv("CY_SAFECAST_MAX_AGE_HOURS", "18"))
    except Exception:
        max_age_h = 18.0
    try:
        age_h = (dt.datetime.now(dt.timezone.utc).timestamp() - path.stat().st_mtime) / 3600.0
        if age_h > max_age_h:
            return ""
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return ""

    radiation = _find_numeric_key(data, ("radiation_usvh", "radiation_μsvh", "usvh", "u_svh", "microsievert_h"))
    cpm = _find_numeric_key(data, ("cpm", "radiation_cpm"))
    if radiation is None and cpm is None:
        return ""
    if (radiation is not None and radiation >= 1.0) or (cpm is not None and cpm >= 500):
        if radiation is not None:
            return f"🧪 Safecast CY: 🔴 {radiation:.2f} μSv/h — проверь официальные сообщения."
        return f"🧪 Safecast CY: 🔴 {cpm:.0f} CPM — проверь официальные сообщения."
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
    visibility = _morning_pick(lines, ("🌫 Видимость:",))
    uv = _morning_pick(lines, ("☀️", "🌞", "🔥"))
    sun = _morning_pick(lines, ("🌇",))
    air = _air_lines(lines) or _morning_pick(lines, ("🏭", "🏙", "🌬", "🌿", "🫁", "💨", "🟢", "🟡", "🔴", "ℹ️"))
    poor_air = _air_is_poor(air)
    radiation = _critical_safecast_cy_line(lines) or _safecast_private_sensor_line()
    quakes = _morning_pick(lines, ("🌍 Сейсмика",))
    space = [x for x in _morning_pick(lines, ("🧲",)) if "н/д" not in x]
    astro = _clean_morning_astro(lines)
    today_tips = _morning_pick(lines, ("✅ Сегодня",))
    tags = _hashtags(lines, "#Кипр #погода #здоровье #Никосия #Тродос")

    out: list[str] = [f"<b>🌅 Кипр сегодня{title_date}</b>"]

    if temp_note:
        out.append(temp_note)
    if weather_line:
        out.append(weather_line)
    for line in visibility:
        if line not in out:
            out.append(line)
    if warning:
        out.append("⚠️ " + _compact_warning(warning))
    if uv:
        out.append(_clean_uv_line(uv[0]))
    if air:
        for item in air[:2]:
            out.extend(_clean_air_line(item).splitlines())
    if radiation:
        out.append(radiation)
    out.append(_morning_sea_line(lines))
    for line in quakes:
        if line not in out:
            out.append(line)
    if space:
        out.append(_clean_kp_line(space[0]))
    if astro:
        out.append("☀️ <b>Солнце, Луна и ритм дня</b>")
        if sun:
            out.append(sun[0])
        out.extend(astro)
    elif sun:
        out.append("☀️ <b>Солнце, Луна и ритм дня</b>")
        out.append(sun[0])

    plan = _clean_today_tip(today_tips[0]) if today_tips else "вода, SPF, тень 11–16, прогулка до полудня"
    if warning:
        out.append("✅ План: " + plan + "; у моря ориентируйся на фактический ветер.")
    else:
        out.append("✅ План: " + plan + ".")
    if poor_air:
        out.append(_poor_air_advice_line(air, safe_legacy_text))

    out.append(tags)
    return "\n".join(out).strip()


def build_evening_format_v2(region_name: str, safe_legacy_text: str) -> str:
    lines = [x.rstrip() for x in str(safe_legacy_text or "").splitlines()]
    date_s = _date_from_title(safe_legacy_text)
    storm = _evening_storm_line(lines)
    sea = _section_after(lines, "Морские города")
    inland = _section_after(lines, "Континентальные города")
    air = _air_lines(lines)
    radiation = _critical_safecast_cy_line(lines) or _safecast_private_sensor_line()
    astro = _clean_evening_astro(lines)
    score = _first_line_starts(lines, ("✨ VayboMeter завтра:", "✨ VayboMeter:"))
    flags = _evening_flags(lines)
    if storm:
        flags["storm"] = True
    poor_air = bool(flags.get("poor_air"))
    # Preserve the existing score-polish inputs; PR C changes only air messaging scope.
    score_flags = dict(flags)
    score_flags["dust"] = _has_poor_air_signal("\n".join(lines))
    score = _polish_evening_score(score, score_flags)
    nuance = _evening_nuance(flags, bool(sea), bool(inland))
    confidence = _evening_confidence_line(flags)
    visibility = _morning_pick(lines, ("🌫 Видимость:",))

    title_date = f" ({date_s})" if date_s else ""
    out: list[str] = [f"<b>🌅 Кипр завтра{title_date}</b>"]

    if score:
        out.append(score)
    out.append(_evening_main_scenario(flags, score))
    if nuance:
        out.append(nuance)
    if confidence:
        out.append(confidence)
    for line in visibility:
        if line not in out:
            out.append(line)
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

    if air:
        out.extend(air)
        if radiation:
            out.append(radiation)
        out.append("")
    elif radiation:
        out.append(radiation)
        out.append("")

    if astro:
        out.append("☀️ <b>Солнце, Луна и ритм завтра</b>")
        out.extend(astro)
        out.append("")

    out.append(_evening_plan(flags))
    if poor_air:
        out.append(_poor_air_advice_line(_forecast_air_lines(lines), safe_legacy_text, forecast=True))
    out.append("#Кипр #погода #здоровье #Никосия #Тродос")
    return "\n".join(out).strip()


def build_format_v2(region_name: str, mode: str, safe_legacy_text: str) -> str:
    mode_s = (mode or "").strip().lower()
    if mode_s.startswith("morn"):
        return build_morning_format_v2(region_name, safe_legacy_text)
    return build_evening_format_v2(region_name, safe_legacy_text)
