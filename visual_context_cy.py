#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Deterministic extraction of visual facts from a Cyprus FORMAT_V2 post."""

from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any, Optional


_CITY_ALIASES = {
    "limassol": ("limassol", "лимассол"),
    "larnaca": ("larnaca", "ларнака"),
    "paphos": ("paphos", "pafos", "пафос"),
    "nicosia": ("nicosia", "никосия"),
    "ayia_napa": ("ayia napa", "ayia-napa", "айя-напа", "айя напа"),
    "troodos": ("troodos", "троодос"),
}
_COASTAL_CITIES = {"limassol", "larnaca", "paphos", "ayia_napa"}

_COASTAL_WORDS = (
    "море", "моря", "морск", "вода", "воды", "у воды", "пляж", "побереж",
    "берег", "набереж", "марин", "coast", "sea", "beach", "promenade",
)
_WEATHER_WORDS = (
    "ясно", "солнеч", "облач", "пасмур", "дожд", "лив", "гроз", "шторм",
    "жар", "зной", "пыль", "дымк", "туман", "влаж", "ветер", "порыв",
    "уф", "uv", "aqi", "осад", "температур", "°",
)
_IGNORE_MARKERS = ("астро", "луна", "меркур", "венер", "марс", "зодиак", "факт дня")

_NUMBER = r"[-+]?\d+(?:[.,]\d+)?"
_TEMP_RE = re.compile(rf"(?<!\w)({_NUMBER})\s*°\s*[cс]?", re.I)
_RANGE_RE = re.compile(
    rf"(?<!\w)({_NUMBER})\s*(?:°\s*)?[–—-]\s*({_NUMBER})\s*°\s*[cс]?", re.I
)
_WIND_RE = re.compile(
    rf"(?:(?:ветер|ветра)\D{{0,22}})?({_NUMBER})\s*(м/с|км/ч)", re.I
)
_GUST_RE = re.compile(rf"(?:порыв\w*|gust\w*)\D{{0,18}}({_NUMBER})\s*(м/с|км/ч)", re.I)
_HUMIDITY_RE = re.compile(rf"(?:влажност\w*|humidity)\D{{0,15}}({_NUMBER})\s*%", re.I)
_UV_RE = re.compile(rf"(?:уф(?:-индекс)?|uv(?:\s*index)?)\D{{0,12}}({_NUMBER})", re.I)
_AQI_RE = re.compile(rf"\baqi\b\D{{0,12}}({_NUMBER})", re.I)
_SEA_TEMP_RE = re.compile(
    rf"(?:море|вода|температура воды|sea)\D{{0,25}}({_NUMBER})\s*°", re.I
)


@dataclass
class VisualContextCY:
    post_type: str = "morning"
    weather_main: str = "unknown"
    temp_max: Optional[float] = None
    temp_min: Optional[float] = None
    wind_max: Optional[float] = None
    gust_max: Optional[float] = None
    humidity_hint: Optional[str] = None
    uv_level: Optional[str] = None
    aqi_level: Optional[str] = None
    dust_hint: Optional[str] = None
    sea_temp: Optional[float] = None
    sea_state_hint: Optional[str] = None
    coastal_focus: bool = False
    inland_heat_focus: bool = False
    city_weather_lines: list[str] = field(default_factory=list)
    coastal_weather_lines: list[str] = field(default_factory=list)
    evidence: dict[str, list[Any]] = field(default_factory=dict)


def _plain_line(raw: str) -> str:
    line = re.sub(r"<[^>]+>", "", raw)
    line = line.replace("\xa0", " ")
    return re.sub(r"\s+", " ", line).strip()


def _number(value: str) -> float:
    return float(value.replace(",", "."))


def _to_ms(value: str, unit: str) -> float:
    result = _number(value)
    return result / 3.6 if unit.lower() == "км/ч" else result


def _level_from_number(value: float, thresholds: tuple[float, float, float]) -> str:
    if value >= thresholds[2]:
        return "extreme"
    if value >= thresholds[1]:
        return "high"
    if value >= thresholds[0]:
        return "moderate"
    return "low"


def _detect_post_type(text: str, explicit: Optional[str]) -> str:
    if explicit:
        value = explicit.strip().lower()
        if value not in {"morning", "evening"}:
            raise ValueError("post_type must be 'morning' or 'evening'")
        return value
    low = text.lower()
    morning_score = sum(token in low for token in ("доброе утро", "на сегодня", "сегодня"))
    evening_score = sum(token in low for token in ("добрый вечер", "на завтра", "завтра"))
    return "evening" if evening_score > morning_score else "morning"


def _cities_in_line(low: str) -> list[str]:
    return [
        canonical
        for canonical, aliases in _CITY_ALIASES.items()
        if any(alias in low for alias in aliases)
    ]


def _qualitative_level(line: str, kind: str) -> Optional[str]:
    low = line.lower()
    if kind == "uv":
        if any(x in low for x in ("экстрем", "очень высок")):
            return "extreme"
        if "высок" in low or "сильн" in low:
            return "high"
        if "средн" in low or "умерен" in low:
            return "moderate"
        if "низк" in low:
            return "low"
    if kind == "aqi":
        if any(x in low for x in ("опасн", "очень плох")):
            return "very_poor"
        if any(x in low for x in ("плох", "нездоров")):
            return "poor"
        if "умерен" in low or "средн" in low:
            return "moderate"
        if any(x in low for x in ("хорош", "чист")):
            return "good"
    return None


def parse_visual_context_cy(text: str, post_type: Optional[str] = None) -> VisualContextCY:
    """Parse finalized Cyprus FORMAT_V2 text without network or model calls."""
    if not isinstance(text, str):
        raise TypeError("text must be a string")

    lines = [_plain_line(raw) for raw in text.splitlines()]
    lines = [line for line in lines if line]
    evidence: dict[str, list[Any]] = {
        "weather_lines": [],
        "coastal_lines": [],
        "temp_candidates": [],
        "wind_candidates": [],
        "uv_candidates": [],
        "dust_lines": [],
        "sea_lines": [],
        "ignored_lines": [],
    }

    temps: list[float] = []
    winds: list[float] = []
    gusts: list[float] = []
    humidity_values: list[float] = []
    uv_values: list[float] = []
    aqi_values: list[float] = []
    city_lines: list[str] = []
    coastal_lines: list[str] = []
    sea_temps: list[float] = []
    sea_state_lines: list[str] = []
    dust_lines: list[str] = []
    weather_hits: set[str] = set()
    nicosia_hot = False
    troodos_relevant = False

    for line in lines:
        low = line.lower()
        cities = _cities_in_line(low)
        is_ignored = any(marker in low for marker in _IGNORE_MARKERS)
        is_weather = any(word in low for word in _WEATHER_WORDS)
        is_coastal = bool(set(cities) & _COASTAL_CITIES) or any(word in low for word in _COASTAL_WORDS)

        if is_ignored and not is_weather:
            evidence["ignored_lines"].append(line)
            continue
        if is_weather:
            evidence["weather_lines"].append(line)
        if cities and is_weather:
            city_lines.append(line)
        if is_coastal:
            coastal_lines.append(line)
            evidence["coastal_lines"].append(line)

        range_values: list[float] = []
        for match in _RANGE_RE.finditer(line):
            pair = [_number(match.group(1)), _number(match.group(2))]
            range_values.extend(pair)
            evidence["temp_candidates"].append({"line": line, "values": pair})
        if range_values:
            temps.extend(range_values)
        else:
            found_temps = [_number(match.group(1)) for match in _TEMP_RE.finditer(line)]
            if found_temps:
                temps.extend(found_temps)
                evidence["temp_candidates"].append({"line": line, "values": found_temps})

        for match in _WIND_RE.finditer(line):
            value = _to_ms(match.group(1), match.group(2))
            winds.append(value)
            evidence["wind_candidates"].append(
                {"line": line, "kind": "wind", "value_ms": round(value, 2)}
            )
        for match in _GUST_RE.finditer(line):
            value = _to_ms(match.group(1), match.group(2))
            gusts.append(value)
            evidence["wind_candidates"].append(
                {"line": line, "kind": "gust", "value_ms": round(value, 2)}
            )

        humidity_values.extend(_number(m.group(1)) for m in _HUMIDITY_RE.finditer(line))
        uv_line_values = [_number(m.group(1)) for m in _UV_RE.finditer(line)]
        if uv_line_values:
            uv_values.extend(uv_line_values)
            evidence["uv_candidates"].append({"line": line, "values": uv_line_values})
        aqi_values.extend(_number(m.group(1)) for m in _AQI_RE.finditer(line))

        line_sea_temps = [_number(m.group(1)) for m in _SEA_TEMP_RE.finditer(line)]
        if line_sea_temps:
            sea_temps.extend(line_sea_temps)
        if any(word in low for word in _COASTAL_WORDS):
            evidence["sea_lines"].append(line)
            if any(x in low for x in ("волн", "штиль", "спокойн", "бриз", "прибой")):
                sea_state_lines.append(line)

        if any(x in low for x in ("пыль", "пыльн", "дымк", "haze", "dust")):
            dust_lines.append(line)
            evidence["dust_lines"].append(line)

        if any(x in low for x in ("гроз", "шторм", "шквал")):
            weather_hits.add("storm")
        if any(x in low for x in ("дожд", "лив", "осад")):
            weather_hits.add("rain")
        if any(x in low for x in ("пыль", "пыльн", "дымк", "haze", "dust")):
            weather_hits.add("dusty")
        if any(x in low for x in ("жар", "зной", "пекло")):
            weather_hits.add("hot")
        if any(x in low for x in ("пасмур", "облач")):
            weather_hits.add("cloudy")
        if any(x in low for x in ("ясно", "солнеч", "безоблач")):
            weather_hits.add("clear")

        line_hot = any(x in low for x in ("жар", "зной", "пекло"))
        nicosia_hot = nicosia_hot or ("nicosia" in cities and line_hot)
        troodos_relevant = troodos_relevant or ("troodos" in cities and is_weather)

    temp_max = max(temps) if temps else None
    temp_min = min(temps) if temps else None
    if temp_max is not None and temp_max >= 33:
        weather_hits.add("hot")

    if "storm" in weather_hits:
        weather_main = "storm"
    elif "rain" in weather_hits:
        weather_main = "rain"
    elif "dusty" in weather_hits:
        weather_main = "dusty"
    elif "hot" in weather_hits:
        weather_main = "hot"
    elif len(weather_hits & {"clear", "cloudy"}) > 1:
        weather_main = "mixed"
    elif "cloudy" in weather_hits:
        weather_main = "cloudy"
    elif "clear" in weather_hits:
        weather_main = "clear"
    else:
        weather_main = "unknown"

    uv_level = None
    if uv_values:
        uv_level = _level_from_number(max(uv_values), (3, 6, 11))
    else:
        for line in evidence["weather_lines"]:
            if "уф" in line.lower() or re.search(r"\buv\b", line, re.I):
                uv_level = _qualitative_level(line, "uv")
                if uv_level:
                    break

    aqi_level = None
    if aqi_values:
        maximum = max(aqi_values)
        if maximum > 150:
            aqi_level = "very_poor"
        elif maximum > 100:
            aqi_level = "poor"
        elif maximum > 50:
            aqi_level = "moderate"
        else:
            aqi_level = "good"
    else:
        for line in evidence["weather_lines"]:
            if "aqi" in line.lower():
                aqi_level = _qualitative_level(line, "aqi")
                if aqi_level:
                    break

    humidity_hint = None
    if humidity_values:
        maximum = max(humidity_values)
        humidity_hint = "high" if maximum >= 70 else "moderate" if maximum >= 45 else "low"
    elif any("влаж" in line.lower() for line in evidence["weather_lines"]):
        humidity_hint = "present"

    coastal_focus = bool(coastal_lines)
    inland_heat_focus = nicosia_hot or (
        temp_max is not None
        and temp_max >= 33
        and any("nicosia" in _cities_in_line(line.lower()) for line in city_lines)
    )
    if troodos_relevant:
        evidence["weather_lines"].append("INLAND_MOUNTAIN_RELEVANCE: Troodos")

    return VisualContextCY(
        post_type=_detect_post_type(text, post_type),
        weather_main=weather_main,
        temp_max=temp_max,
        temp_min=temp_min,
        wind_max=max(winds) if winds else None,
        gust_max=max(gusts) if gusts else None,
        humidity_hint=humidity_hint,
        uv_level=uv_level,
        aqi_level=aqi_level,
        dust_hint="; ".join(dust_lines) if dust_lines else None,
        sea_temp=max(sea_temps) if sea_temps else None,
        sea_state_hint="; ".join(sea_state_lines) if sea_state_lines else None,
        coastal_focus=coastal_focus,
        inland_heat_focus=inland_heat_focus,
        city_weather_lines=city_lines,
        coastal_weather_lines=coastal_lines,
        evidence=evidence,
    )


__all__ = ["VisualContextCY", "parse_visual_context_cy"]
