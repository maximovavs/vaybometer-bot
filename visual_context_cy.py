#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Deterministic extraction of visual facts from a Cyprus FORMAT_V2 post."""

from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any, Mapping, Optional

from visibility_context import (
    VISIBILITY_CONDITIONS,
    normalize_number,
    normalize_visibility_m,
    visibility_condition_from_text,
)


_CITY_ALIASES = {
    "limassol": ("limassol", "лимассол"),
    "larnaca": ("larnaca", "ларнака"),
    "paphos": ("paphos", "pafos", "пафос"),
    "nicosia": ("nicosia", "никосия"),
    "ayia_napa": ("ayia napa", "ayia-napa", "айя-напа", "айя напа"),
    "troodos": ("troodos", "троодос"),
}
_CITY_DISPLAY = {
    "limassol": "Лимассол",
    "larnaca": "Ларнака",
    "paphos": "Пафос",
    "nicosia": "Никосия",
    "ayia_napa": "Айя-Напа",
    "troodos": "Троодос",
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
_DAY_NIGHT_TEMP_RE = re.compile(
    rf"(?<!\w)({_NUMBER})\s*/\s*({_NUMBER})\s*°\s*[cс]?", re.I
)
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
_SEA_EMOJI_TEMP_RE = re.compile(rf"🌊\s*({_NUMBER})\s*°?\s*[cс]?", re.I)
_STORM_NEGATION_RE = re.compile(
    r"шторм\w*\s+не\s+ожида|без\s+шторма|штормов\w*\s+предупрежден\w*\s+нет|риск\s+шторма\s+низк",
    re.I,
)
_STORM_POSITIVE_RE = re.compile(r"\b(?:шторм\w*|шквал\w*|гроз\w*)\b|thunderstorm|squall|storm", re.I)
_PRECIP_FACTUAL_RE = re.compile(r"дожд\w*|ливн\w*|морос\w*|rain|showers?", re.I)
_PRECIP_EXPLICIT_RE = re.compile(
    r"осад\w*\s+(?:прогнозир\w*|ожида\w*)|ожида\w*\s+осад\w*|местами\s+осад\w*",
    re.I,
)
_PRECIP_NEGATION_RE = re.compile(
    r"осад\w*\s+не\s+ожида|без\s+осад\w*|дожд\w*\s+не\s+буд|вероятност\w*\s+осад\w*\s+низк",
    re.I,
)
_PRECIP_UNCERTAINTY_RE = re.compile(
    r"ветер\s*/\s*осад\w*\s+лучше\s+провер|осад\w*\s+лучше\s+провер|осад\w*\s+уточн|проверить\s+осад\w*|осад\w*\s+и\s+порыв\w*\s+требу\w*\s+гибк",
    re.I,
)
_DUST_RE = re.compile(
    r"пыл\w*|сахар\w+\s+пыл\w*|задымлен\w*|\bдым\s*/\s*смог\b|(?<![а-яё])дым(?!к|[а-яё])|(?<![а-яё])смог(?![а-яё])|dust|smoke|smog",
    re.I,
)
_HAZE_RE = re.compile(r"дымк\w*|туман\w*|fog|haze", re.I)
_VISIBILITY_METERS_RE = re.compile(r"(?:менее|около|до)?\s*(\d+(?:[.,]\d+)?)\s*м\b", re.I)
_MOON_PHASE_PREFIXES = ("🌑", "🌒", "🌓", "🌔", "🌕", "🌖", "🌗", "🌘", "🌙")
_DERIVED_SUMMARY_PREFIXES = (
    "✨ VayboMeter",
    "🧭 Главное",
    "⚠️ Нюанс",
    "⚠️ Главный нюанс",
    "💬 Настрой",
    "🎯 Уверенность",
    "✅ План",
    "☀️",
    "💚",
    "⚫️",
)


@dataclass
class VisualContextCY:
    post_type: str = "morning"
    weather_main: str = "unknown"
    primary_weather: str = "unknown"
    hazards: list[str] = field(default_factory=list)
    visual_forecast_period: str = "representative_daytime"
    scene_focus: str = "island_wide"
    temp_max: Optional[float] = None
    temp_min: Optional[float] = None
    wind_max: Optional[float] = None
    gust_max: Optional[float] = None
    humidity_hint: Optional[str] = None
    uv_level: Optional[str] = None
    aqi_level: Optional[str] = None
    dust_hint: Optional[str] = None
    visibility_haze: bool = False
    visibility_condition: str = "clear"
    visibility_forecast_window: str = "none"
    current_visibility_m: Optional[float] = None
    morning_min_visibility_m: Optional[float] = None
    humidity_pct: Optional[float] = None
    temperature_c: Optional[float] = None
    dew_point_c: Optional[float] = None
    dew_point_spread_c: Optional[float] = None
    weather_code: Optional[int] = None
    weather_code_source: Optional[str] = None
    observation_time: Optional[str] = None
    confidence: Optional[str] = None
    classification_reason: Optional[str] = None
    location_label: Optional[str] = None
    visibility_evidence: Optional[str] = None
    dust_vs_fog_classification: str = "clear"
    actual_precipitation: bool = False
    coastal_precipitation: bool = False
    inland_precipitation: bool = False
    inland_thunder_risk: bool = False
    strong_wind: bool = False
    severe_wind: bool = False
    explicit_storm: bool = False
    sea_temp: Optional[float] = None
    sea_temp_min: Optional[float] = None
    sea_temp_max: Optional[float] = None
    sea_state_hint: Optional[str] = None
    coastal_focus: bool = False
    inland_heat_focus: bool = False
    inland_max_temp: Optional[float] = None
    hottest_city: Optional[str] = None
    coastal_temp_min: Optional[float] = None
    coastal_temp_max: Optional[float] = None
    city_weather_lines: list[str] = field(default_factory=list)
    coastal_weather_lines: list[str] = field(default_factory=list)
    evidence: dict[str, Any] = field(default_factory=dict)

    @property
    def visibility_m(self) -> Optional[float]:
        """Compatibility alias without fabricating either source measurement."""
        if self.morning_min_visibility_m is not None:
            return self.morning_min_visibility_m
        return self.current_visibility_m


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


def _normalized_sea_state(lines: list[str]) -> Optional[str]:
    low = " ".join(lines).lower()
    if not low:
        return None
    if _STORM_NEGATION_RE.search(low):
        low = _STORM_NEGATION_RE.sub(" ", low)
    if any(token in low for token in ("шторм", "бурн", "сильн", "неспокойн", "прибой")):
        return "rough"
    if any(token in low for token in ("волн", "бриз")):
        return "breezy"
    if any(token in low for token in ("штиль", "спокойн")):
        return "calm"
    return "present"


def _has_actual_storm_signal(line: str) -> bool:
    if _is_derived_summary_line(line):
        return False
    if _STORM_NEGATION_RE.search(str(line or "")):
        return False
    return bool(_STORM_POSITIVE_RE.search(str(line or "")))


def _is_derived_summary_line(line: str) -> bool:
    stripped = str(line or "").strip()
    return stripped.startswith(_DERIVED_SUMMARY_PREFIXES) or stripped.startswith(_MOON_PHASE_PREFIXES)


def _has_actual_precipitation(line: str) -> bool:
    text = str(line or "")
    low = text.lower()
    if _is_derived_summary_line(text):
        return False
    if _PRECIP_NEGATION_RE.search(low) or _PRECIP_UNCERTAINTY_RE.search(low):
        return False
    if _PRECIP_FACTUAL_RE.search(low) or _PRECIP_EXPLICIT_RE.search(low):
        return True
    if "гроз" in low and any(token in low for token in ("дожд", "лив", "осад", "мокр", "rain", "wet")):
        return True
    return False


def _has_dust_signal(line: str) -> bool:
    text = re.sub(r"пыльца\w*", "", str(line or ""), flags=re.I)
    return bool(_DUST_RE.search(text))


def _has_visibility_haze(line: str) -> bool:
    return bool(_HAZE_RE.search(str(line or ""))) and not _has_dust_signal(line)


def _visibility_facts(lines: list[str]) -> dict[str, Any]:
    visibility_lines = [line for line in lines if line.startswith("🌫 Видимость:")]
    if not visibility_lines:
        return {
            "condition": "clear",
            "evidence": None,
            "reported_visibility_m": None,
            "classification_reason": "no finalized visibility line",
        }
    line = visibility_lines[0]
    match = _VISIBILITY_METERS_RE.search(line)
    reported_visibility = normalize_visibility_m(match.group(1)) if match else None
    return {
        "condition": visibility_condition_from_text(line),
        "evidence": line,
        # The compact human line describes an effective/local minimum, not
        # necessarily the current observation. Keep it diagnostic-only unless
        # the sidecar metadata supplies the source-specific measurements.
        "reported_visibility_m": reported_visibility,
        "classification_reason": "parsed from finalized visibility line",
    }


def _visibility_metadata_values(metadata: Optional[Mapping[str, Any]]) -> dict[str, Any]:
    data = metadata if isinstance(metadata, Mapping) else {}
    condition = str(data.get("condition") or data.get("visibility_condition") or "").strip()
    return {
        "condition": condition if condition in VISIBILITY_CONDITIONS else None,
        "current_visibility_m": normalize_visibility_m(data.get("current_visibility_m")),
        "morning_min_visibility_m": normalize_visibility_m(data.get("morning_min_visibility_m")),
        "humidity_pct": normalize_number(data.get("humidity_pct"), non_negative=True),
        "temperature_c": normalize_number(data.get("temperature_c")),
        "dew_point_c": normalize_number(data.get("dew_point_c")),
        "dew_point_spread_c": normalize_number(data.get("dew_point_spread_c"), non_negative=True),
        "weather_code": (
            int(value)
            if (value := normalize_number(data.get("weather_code"), non_negative=True)) is not None
            else None
        ),
        "weather_code_source": str(data.get("weather_code_source") or "").strip() or None,
        "observation_time": str(data.get("observation_time") or "").strip() or None,
        "confidence": str(data.get("confidence") or "").strip() or None,
        "classification_reason": str(data.get("classification_reason") or "").strip() or None,
        "location_label": str(data.get("location_label") or "").strip() or None,
        "evidence_source": str(data.get("evidence_source") or "").strip() or None,
    }


def _visibility_forecast_window(
    post_type: str,
    condition: str,
    finalized_visibility_line: Optional[str],
) -> str:
    if condition == "clear":
        return "none"
    if post_type == "morning":
        return "current_morning"
    if post_type == "evening" and "завтра утром" in str(finalized_visibility_line or "").lower():
        return "tomorrow_morning"
    return "none"


def _visual_forecast_period(
    post_type: str,
    visibility_window: str,
    factual_weather_lines: list[str],
) -> str:
    if visibility_window in {"current_morning", "tomorrow_morning"}:
        return visibility_window
    timed_event = re.compile(
        r"дожд\w*|ливн\w*|морос\w*|осад\w*|шторм\w*|гроз\w*|шквал\w*|"
        r"туман\w*|дымк\w*|пыл\w*|fog|rain|storm|thunder|squall|dust",
        re.I,
    )
    for line in factual_weather_lines:
        if _is_derived_summary_line(line) or not timed_event.search(line):
            continue
        low = line.lower()
        if post_type == "morning" and re.search(r"утр\w*|this morning", low):
            return "current_morning"
        if post_type == "evening" and re.search(r"завтра\s+утр\w*|tomorrow morning", low):
            return "tomorrow_morning"
        if re.search(r"ноч\w*|overnight|at night", low):
            return "overnight"
        if re.search(r"вечер\w*|вечером|in the evening", low):
            return "evening"
    return "representative_daytime"


def parse_visual_context_cy(
    text: str,
    post_type: Optional[str] = None,
    visibility_metadata: Optional[Mapping[str, Any]] = None,
) -> VisualContextCY:
    """Parse finalized Cyprus FORMAT_V2 text without network or model calls."""
    if not isinstance(text, str):
        raise TypeError("text must be a string")

    lines = [_plain_line(raw) for raw in text.splitlines()]
    lines = [line for line in lines if line]
    evidence: dict[str, Any] = {
        "weather_lines": [],
        "coastal_lines": [],
        "temp_candidates": [],
        "wind_candidates": [],
        "uv_candidates": [],
        "dust_lines": [],
        "haze_lines": [],
        "visibility_lines": [],
        "precipitation_lines": [],
        "coastal_precipitation_lines": [],
        "inland_precipitation_lines": [],
        "generic_precipitation_lines": [],
        "inland_thunder_lines": [],
        "coastal_storm_lines": [],
        "inland_storm_lines": [],
        "generic_storm_lines": [],
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
    city_day_temps: dict[str, float] = {}
    coastal_day_temps: list[float] = []
    dust_lines: list[str] = []
    haze_lines: list[str] = []
    actual_precipitation = False
    coastal_precipitation = False
    inland_precipitation = False
    inland_thunder_risk = False
    weather_hits: set[str] = set()
    nicosia_hot = False
    troodos_relevant = False
    visibility_facts = _visibility_facts(lines)
    visibility_metadata_values = _visibility_metadata_values(visibility_metadata)
    visibility_condition = (
        visibility_metadata_values["condition"] or visibility_facts["condition"]
    )
    resolved_post_type = _detect_post_type(text, post_type)
    visibility_forecast_window = _visibility_forecast_window(
        resolved_post_type,
        visibility_condition,
        visibility_facts["evidence"],
    )
    if visibility_condition == "dust_haze":
        weather_hits.add("dusty")

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
        if line.startswith("🌫 Видимость:"):
            evidence["visibility_lines"].append(line)
        if cities and is_weather:
            city_lines.append(line)
        if is_coastal:
            coastal_lines.append(line)
            evidence["coastal_lines"].append(line)

        day_night_values: list[float] = []
        for match in _DAY_NIGHT_TEMP_RE.finditer(line):
            pair = [_number(match.group(1)), _number(match.group(2))]
            day_night_values.extend(pair)
            evidence["temp_candidates"].append({"line": line, "values": pair})
            if cities:
                for city in cities:
                    city_day_temps[city] = max(city_day_temps.get(city, pair[0]), pair[0])
            if is_coastal:
                coastal_day_temps.append(pair[0])
        range_values: list[float] = []
        if not day_night_values:
            for match in _RANGE_RE.finditer(line):
                pair = [_number(match.group(1)), _number(match.group(2))]
                range_values.extend(pair)
                evidence["temp_candidates"].append({"line": line, "values": pair})
                if is_coastal:
                    coastal_day_temps.extend(pair)
        if day_night_values:
            temps.extend(day_night_values)
        elif range_values:
            temps.extend(range_values)
        else:
            found_temps = [_number(match.group(1)) for match in _TEMP_RE.finditer(line)]
            if found_temps:
                temps.extend(found_temps)
                evidence["temp_candidates"].append({"line": line, "values": found_temps})
                if cities:
                    for city in cities:
                        city_day_temps[city] = max(city_day_temps.get(city, found_temps[0]), found_temps[0])
                if is_coastal:
                    coastal_day_temps.append(found_temps[0])

        for match in _WIND_RE.finditer(line):
            value = _to_ms(match.group(1), match.group(2))
            winds.append(value)
            evidence["wind_candidates"].append(
                {"line": line, "kind": "wind", "value_ms": round(value, 2)}
            )
        line_gusts: list[float] = []
        for match in _GUST_RE.finditer(line):
            value = _to_ms(match.group(1), match.group(2))
            gusts.append(value)
            line_gusts.append(value)
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
        line_sea_temps.extend(_number(m.group(1)) for m in _SEA_EMOJI_TEMP_RE.finditer(line))
        if line_sea_temps:
            sea_temps.extend(line_sea_temps)
        if any(word in low for word in _COASTAL_WORDS):
            evidence["sea_lines"].append(line)
            if any(x in low for x in ("волн", "штиль", "спокойн", "бриз", "прибой")):
                sea_state_lines.append(line)

        line_has_precipitation = _has_actual_precipitation(line)
        line_has_dust = _has_dust_signal(line)
        line_has_haze = _has_visibility_haze(line)
        is_troodos_or_mountain = "troodos" in cities or any(x in low for x in ("тродос", "горы", "горн", "mountain"))

        if line_has_precipitation:
            actual_precipitation = True
            evidence["precipitation_lines"].append(line)
            if is_coastal:
                coastal_precipitation = True
                evidence["coastal_precipitation_lines"].append(line)
            elif cities or is_troodos_or_mountain:
                inland_precipitation = True
                evidence["inland_precipitation_lines"].append(line)
            else:
                coastal_precipitation = True
                evidence["generic_precipitation_lines"].append(line)

        if line_has_dust:
            dust_lines.append(line)
            evidence["dust_lines"].append(line)
        elif line_has_haze:
            haze_lines.append(line)
            evidence["haze_lines"].append(line)

        line_has_storm = _has_actual_storm_signal(line)
        if line_has_storm:
            if is_coastal:
                evidence["coastal_storm_lines"].append(line)
            elif cities or is_troodos_or_mountain:
                inland_thunder_risk = True
                evidence["inland_storm_lines"].append(line)
            else:
                evidence["generic_storm_lines"].append(line)
        if line_has_precipitation:
            weather_hits.add("rain")
        if line_has_dust:
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

    coastal_focus = bool(coastal_lines)
    weather_code_source = str(visibility_metadata_values["weather_code_source"] or "").lower()
    structured_storm = bool(
        visibility_metadata_values["weather_code"] in {95, 96, 99}
        and (
            resolved_post_type == "morning"
            or any(token in weather_code_source for token in ("forecast", "hourly", "tomorrow"))
        )
    )
    explicit_storm = bool(
        structured_storm
        or evidence["coastal_storm_lines"]
        or evidence["generic_storm_lines"]
        or evidence["inland_storm_lines"]
    )

    if "rain" in weather_hits:
        weather_main = "rain"
    elif "dusty" in weather_hits:
        weather_main = "dusty"
    elif (
        visibility_forecast_window in {"current_morning", "tomorrow_morning"}
        and visibility_condition in {"dense_fog", "fog", "mist"}
    ):
        weather_main = "fog"
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

    if resolved_post_type == "morning" and aqi_level in {"poor", "very_poor"} and not dust_lines:
        dust_lines.append("poor AQI/PM visibility signal")

    humidity_hint = None
    if humidity_values:
        maximum = max(humidity_values)
        humidity_hint = "high" if maximum >= 70 else "moderate" if maximum >= 45 else "low"
    elif any("влаж" in line.lower() for line in evidence["weather_lines"]):
        humidity_hint = "present"

    inland_heat_candidate = nicosia_hot or (
        temp_max is not None
        and temp_max >= 33
        and any("nicosia" in _cities_in_line(line.lower()) for line in city_lines)
    )
    inland_heat_focus = inland_heat_candidate
    if troodos_relevant:
        evidence["weather_lines"].append("INLAND_MOUNTAIN_RELEVANCE: Troodos")

    gust_max = max(gusts) if gusts else None
    wind_max = max(winds) if winds else None
    strongest_wind = max(
        [value for value in (wind_max, gust_max) if isinstance(value, (int, float))],
        default=None,
    )
    strong_wind = bool(strongest_wind is not None and strongest_wind >= 9)
    severe_wind = bool(gust_max is not None and gust_max >= 15)
    if coastal_focus and inland_heat_focus:
        scene_focus = "coast_inland_contrast"
    elif coastal_focus:
        scene_focus = "coastal"
    elif inland_heat_focus:
        scene_focus = "inland"
    else:
        scene_focus = "island_wide"
    visual_forecast_period = _visual_forecast_period(
        resolved_post_type,
        visibility_forecast_window,
        list(evidence["weather_lines"]),
    )
    hazards: list[str] = []
    if inland_heat_candidate or (temp_max is not None and temp_max >= 33):
        hazards.append("heat")
    if severe_wind:
        hazards.append("severe_wind")
    elif strong_wind:
        hazards.append("strong_wind")
    if explicit_storm:
        hazards.append("storm")
    if actual_precipitation:
        hazards.append("precipitation")
    if dust_lines or visibility_condition == "dust_haze":
        hazards.append("dust")
    if visibility_condition in {"dense_fog", "fog", "mist"}:
        hazards.append("fog")
    hottest_city_key = max(city_day_temps, key=city_day_temps.get) if city_day_temps else None

    return VisualContextCY(
        post_type=resolved_post_type,
        weather_main=weather_main,
        primary_weather=weather_main,
        hazards=hazards,
        visual_forecast_period=visual_forecast_period,
        scene_focus=scene_focus,
        temp_max=temp_max,
        temp_min=temp_min,
        wind_max=wind_max,
        gust_max=gust_max,
        humidity_hint=humidity_hint,
        uv_level=uv_level,
        aqi_level=aqi_level,
        dust_hint="; ".join(dust_lines) if dust_lines else None,
        visibility_haze=bool(haze_lines),
        visibility_condition=visibility_condition,
        visibility_forecast_window=visibility_forecast_window,
        current_visibility_m=visibility_metadata_values["current_visibility_m"],
        morning_min_visibility_m=visibility_metadata_values["morning_min_visibility_m"],
        humidity_pct=visibility_metadata_values["humidity_pct"],
        temperature_c=visibility_metadata_values["temperature_c"],
        dew_point_c=visibility_metadata_values["dew_point_c"],
        dew_point_spread_c=visibility_metadata_values["dew_point_spread_c"],
        weather_code=visibility_metadata_values["weather_code"],
        weather_code_source=visibility_metadata_values["weather_code_source"],
        observation_time=visibility_metadata_values["observation_time"],
        confidence=visibility_metadata_values["confidence"],
        classification_reason=(
            visibility_metadata_values["classification_reason"]
            or visibility_facts["classification_reason"]
        ),
        location_label=visibility_metadata_values["location_label"],
        visibility_evidence=(
            visibility_metadata_values["evidence_source"]
            or visibility_facts["evidence"]
        ),
        dust_vs_fog_classification=visibility_condition,
        actual_precipitation=actual_precipitation,
        coastal_precipitation=coastal_precipitation,
        inland_precipitation=inland_precipitation,
        inland_thunder_risk=inland_thunder_risk,
        strong_wind=strong_wind,
        severe_wind=severe_wind,
        explicit_storm=explicit_storm,
        sea_temp=max(sea_temps) if sea_temps else None,
        sea_temp_min=min(sea_temps) if sea_temps else None,
        sea_temp_max=max(sea_temps) if sea_temps else None,
        sea_state_hint=_normalized_sea_state(sea_state_lines),
        coastal_focus=coastal_focus,
        inland_heat_focus=inland_heat_focus,
        inland_max_temp=city_day_temps.get("nicosia"),
        hottest_city=_CITY_DISPLAY.get(hottest_city_key) if hottest_city_key else None,
        coastal_temp_min=min(coastal_day_temps) if coastal_day_temps else None,
        coastal_temp_max=max(coastal_day_temps) if coastal_day_temps else None,
        city_weather_lines=city_lines,
        coastal_weather_lines=coastal_lines,
        evidence=evidence,
    )


__all__ = ["VisualContextCY", "parse_visual_context_cy"]
