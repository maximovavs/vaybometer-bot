#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Pure Cyprus visibility/fog classification helpers.

This module performs no network, filesystem, Telegram, image-provider or LLM
operations. Callers provide already-fetched weather and optional air data.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date as Date, datetime, timedelta
import math
import re
from typing import Any, Mapping, Optional
from zoneinfo import ZoneInfo


DENSE_FOG_MAX_M = 500.0
FOG_MAX_M = 1000.0
MIST_MAX_M = 3000.0
REDUCED_VISIBILITY_MAX_M = 6000.0

VISIBILITY_CONDITIONS = {
    "dense_fog",
    "fog",
    "mist",
    "reduced_visibility",
    "dust_haze",
    "mixed_visibility",
    "clear",
}


@dataclass(frozen=True)
class CyprusVisibilityContext:
    current_visibility_m: Optional[float] = None
    morning_min_visibility_m: Optional[float] = None
    humidity_pct: Optional[float] = None
    temperature_c: Optional[float] = None
    dew_point_c: Optional[float] = None
    dew_point_spread_c: Optional[float] = None
    weather_code: Optional[int] = None
    weather_code_source: Optional[str] = None
    aqi: Optional[float] = None
    pm25: Optional[float] = None
    pm10: Optional[float] = None
    condition: str = "clear"
    evidence_source: str = "unavailable"
    observation_time: Optional[str] = None
    target_date: Optional[str] = None
    confidence: str = "low"
    location_label: str = "Лимассол"
    classification_reason: str = "no usable visibility evidence"

    @property
    def effective_visibility_m(self) -> Optional[float]:
        if self.evidence_source.startswith("current"):
            return self.current_visibility_m
        if self.evidence_source.startswith("hourly_morning"):
            return self.morning_min_visibility_m
        if self.morning_min_visibility_m is not None:
            return self.morning_min_visibility_m
        return self.current_visibility_m


def normalize_number(value: Any, *, non_negative: bool = False) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, str) and not value.strip():
        return None
    try:
        number = float(str(value).replace(",", "."))
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    if non_negative and number < 0:
        return None
    return number


def normalize_visibility_m(value: Any) -> Optional[float]:
    return normalize_number(value, non_negative=True)


def dew_point_spread_c(temperature_c: Any, dew_point_c: Any) -> Optional[float]:
    temperature = normalize_number(temperature_c)
    dew_point = normalize_number(dew_point_c)
    if temperature is None or dew_point is None:
        return None
    return max(0.0, temperature - dew_point)


def _weather_code(value: Any) -> Optional[int]:
    number = normalize_number(value, non_negative=True)
    return int(number) if number is not None else None


def _first_present(*values: Any) -> Any:
    return next((value for value in values if value is not None), None)


def _air_value(air_data: Optional[Mapping[str, Any]], *keys: str) -> Optional[float]:
    data = air_data if isinstance(air_data, Mapping) else {}
    for key in keys:
        if key in data:
            return normalize_number(data.get(key), non_negative=True)
    return None


def _target_date_value(target_date: Any, post_type: str, tz_name: str) -> Date:
    if isinstance(target_date, datetime):
        return target_date.date()
    if isinstance(target_date, Date):
        return target_date
    if isinstance(target_date, str) and target_date.strip():
        try:
            return Date.fromisoformat(target_date.strip()[:10])
        except ValueError:
            pass
    today = datetime.now(ZoneInfo(tz_name)).date()
    return today if post_type.startswith("morn") else today + timedelta(days=1)


def _local_datetime(value: Any, tz_name: str) -> Optional[datetime]:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is not None:
        try:
            parsed = parsed.astimezone(ZoneInfo(tz_name))
        except Exception:
            pass
    return parsed


def _support_flags(
    *,
    humidity_pct: Optional[float],
    spread_c: Optional[float],
    weather_code: Optional[int],
    aqi: Optional[float],
    pm25: Optional[float],
    pm10: Optional[float],
) -> dict[str, bool]:
    fog_code = weather_code in {45, 48}
    wet_support = bool(
        fog_code
        or (humidity_pct is not None and humidity_pct >= 90)
        or (spread_c is not None and spread_c <= 2.0)
    )
    strong_wet_support = bool(
        fog_code
        or (humidity_pct is not None and humidity_pct >= 93)
        or (spread_c is not None and spread_c <= 1.5)
    )
    pollution_support = bool(
        (aqi is not None and aqi >= 100)
        or (pm25 is not None and pm25 >= 35)
        or (pm10 is not None and pm10 >= 80)
    )
    dry_support = bool(
        humidity_pct is not None
        and humidity_pct <= 60
        and (spread_c is None or spread_c >= 4.0)
    )
    return {
        "fog_code": fog_code,
        "wet_support": wet_support,
        "strong_wet_support": strong_wet_support,
        "pollution_support": pollution_support,
        "dry_support": dry_support,
    }


def classify_visibility_values(
    *,
    visibility_m: Any,
    humidity_pct: Any = None,
    temperature_c: Any = None,
    dew_point_c: Any = None,
    weather_code: Any = None,
    aqi: Any = None,
    pm25: Any = None,
    pm10: Any = None,
) -> tuple[str, str, str]:
    visibility = normalize_visibility_m(visibility_m)
    humidity = normalize_number(humidity_pct, non_negative=True)
    temperature = normalize_number(temperature_c)
    dew_point = normalize_number(dew_point_c)
    spread = dew_point_spread_c(temperature, dew_point)
    code = _weather_code(weather_code)
    aqi_value = normalize_number(aqi, non_negative=True)
    pm25_value = normalize_number(pm25, non_negative=True)
    pm10_value = normalize_number(pm10, non_negative=True)
    support = _support_flags(
        humidity_pct=humidity,
        spread_c=spread,
        weather_code=code,
        aqi=aqi_value,
        pm25=pm25_value,
        pm10=pm10_value,
    )

    evidence: list[str] = []
    if support["fog_code"]:
        evidence.append(f"WMO {code}")
    if humidity is not None:
        evidence.append(f"RH {humidity:g}%")
    if spread is not None:
        evidence.append(f"spread {spread:g}°C")
    if support["pollution_support"]:
        evidence.append("pollution support")
    evidence_text = ", ".join(evidence) or "limited supporting evidence"

    if visibility is not None:
        if visibility <= REDUCED_VISIBILITY_MAX_M and support["wet_support"] and support["pollution_support"]:
            return "mixed_visibility", "high", f"visibility {visibility:g} m with wet and pollution support ({evidence_text})"
        if visibility <= DENSE_FOG_MAX_M and support["strong_wet_support"]:
            return "dense_fog", "high", f"visibility {visibility:g} m with strong wet support ({evidence_text})"
        if visibility <= FOG_MAX_M and support["wet_support"]:
            return "fog", "high", f"visibility {visibility:g} m with wet support ({evidence_text})"
        if FOG_MAX_M < visibility <= MIST_MAX_M and support["wet_support"]:
            return "mist", "high", f"visibility {visibility:g} m with wet support ({evidence_text})"
        if visibility <= REDUCED_VISIBILITY_MAX_M and support["dry_support"] and support["pollution_support"]:
            return "dust_haze", "high", f"visibility {visibility:g} m with dry pollution support ({evidence_text})"
        if visibility <= REDUCED_VISIBILITY_MAX_M:
            return "reduced_visibility", "medium", f"visibility {visibility:g} m without sufficient fog or dust evidence ({evidence_text})"
        return "clear", "high", f"visibility {visibility:g} m is above reduced-visibility threshold"

    if support["fog_code"] and support["wet_support"]:
        return "fog", "medium", f"WMO fog code without numeric visibility ({evidence_text})"
    if support["dry_support"] and support["pollution_support"]:
        return "dust_haze", "medium", f"dry pollution support without numeric visibility ({evidence_text})"
    if support["wet_support"] and support["pollution_support"]:
        return "mixed_visibility", "medium", f"wet and pollution support without numeric visibility ({evidence_text})"
    return "clear", "low", f"no numeric visibility and insufficient alert evidence ({evidence_text})"


def _record_from_mapping(data: Mapping[str, Any], *, source: str) -> dict[str, Any]:
    temperature = normalize_number(data.get("temperature_2m"))
    dew_point = normalize_number(_first_present(data.get("dew_point_2m"), data.get("dewpoint_2m")))
    return {
        "visibility": normalize_visibility_m(data.get("visibility")),
        "humidity": normalize_number(data.get("relative_humidity_2m"), non_negative=True),
        "temperature": temperature,
        "dew_point": dew_point,
        "spread": dew_point_spread_c(temperature, dew_point),
        "weather_code": _weather_code(_first_present(data.get("weather_code"), data.get("weathercode"))),
        "time": str(data.get("time") or "") or None,
        "source": source,
    }


def get_cyprus_visibility_context(
    weather_data: Optional[Mapping[str, Any]],
    *,
    post_type: str = "morning",
    target_date: Any = None,
    tz: str = "Asia/Nicosia",
    air_data: Optional[Mapping[str, Any]] = None,
    location_label: str = "Лимассол",
) -> CyprusVisibilityContext:
    payload = weather_data if isinstance(weather_data, Mapping) else {}
    target = _target_date_value(target_date, post_type, tz)
    current_data = payload.get("current") if isinstance(payload.get("current"), Mapping) else {}
    hourly = payload.get("hourly") if isinstance(payload.get("hourly"), Mapping) else {}
    current_record = _record_from_mapping(current_data, source="current")

    times = hourly.get("time_local") or hourly.get("time") or []

    def hourly_value(key: str, index: int) -> Any:
        values = hourly.get(key)
        if not isinstance(values, list) or index < 0 or index >= len(values):
            return None
        return values[index]

    morning_records: list[dict[str, Any]] = []
    if isinstance(times, list):
        for index, raw_time in enumerate(times):
            local_dt = _local_datetime(raw_time, tz)
            if local_dt is None or local_dt.date() != target or not (4 <= local_dt.hour <= 10):
                continue
            record = _record_from_mapping(
                {
                    "visibility": hourly_value("visibility", index),
                    "relative_humidity_2m": hourly_value("relative_humidity_2m", index),
                    "temperature_2m": hourly_value("temperature_2m", index),
                    "dew_point_2m": _first_present(
                        hourly_value("dew_point_2m", index),
                        hourly_value("dewpoint_2m", index),
                    ),
                    "weather_code": _first_present(
                        hourly_value("weather_code", index),
                        hourly_value("weathercode", index),
                    ),
                    "time": raw_time,
                },
                source="hourly_morning",
            )
            morning_records.append(record)

    numeric_morning = [record for record in morning_records if record["visibility"] is not None]
    morning_min_record = min(numeric_morning, key=lambda item: item["visibility"]) if numeric_morning else None
    morning_min = morning_min_record["visibility"] if morning_min_record else None
    current_visibility = current_record["visibility"]

    numeric_candidates: list[dict[str, Any]] = []
    if morning_min_record:
        numeric_candidates.append(morning_min_record)
    if post_type.startswith("morn") and current_visibility is not None:
        numeric_candidates.append(current_record)
    selected = min(numeric_candidates, key=lambda item: item["visibility"]) if numeric_candidates else None

    if selected is None:
        fallback_records = list(morning_records)
        if post_type.startswith("morn"):
            fallback_records.append(current_record)
        selected = next((record for record in fallback_records if record["weather_code"] in {45, 48}), None)
        if selected is None:
            selected = next(
                (
                    record
                    for record in fallback_records
                    if record["humidity"] is not None or record["spread"] is not None
                ),
                None,
            )

    aqi = _air_value(air_data, "aqi")
    pm25 = _air_value(air_data, "pm25", "pm2_5", "pm2.5")
    pm10 = _air_value(air_data, "pm10")
    selected = selected or {
        "visibility": None,
        "humidity": None,
        "temperature": None,
        "dew_point": None,
        "spread": None,
        "weather_code": None,
        "time": None,
        "source": "unavailable",
    }
    condition, confidence, reason = classify_visibility_values(
        visibility_m=selected["visibility"],
        humidity_pct=selected["humidity"],
        temperature_c=selected["temperature"],
        dew_point_c=selected["dew_point"],
        weather_code=selected["weather_code"],
        aqi=aqi,
        pm25=pm25,
        pm10=pm10,
    )
    source = str(selected.get("source") or "unavailable")
    if any(value is not None for value in (aqi, pm25, pm10)):
        source += "+air_quality"

    return CyprusVisibilityContext(
        current_visibility_m=current_visibility,
        morning_min_visibility_m=morning_min,
        humidity_pct=selected["humidity"],
        temperature_c=selected["temperature"],
        dew_point_c=selected["dew_point"],
        dew_point_spread_c=selected["spread"],
        weather_code=selected["weather_code"],
        weather_code_source=selected["source"] if selected["weather_code"] is not None else None,
        aqi=aqi,
        pm25=pm25,
        pm10=pm10,
        condition=condition,
        evidence_source=source,
        observation_time=selected["time"],
        target_date=target.isoformat(),
        confidence=confidence,
        location_label=location_label,
        classification_reason=reason,
    )


def build_cyprus_visibility_line(
    context: CyprusVisibilityContext,
    *,
    post_type: str = "morning",
) -> Optional[str]:
    timing = "утром" if post_type.startswith("morn") else "завтра утром"
    place = _visibility_location_phrase(context.location_label)
    evidence_time = _visibility_time_phrase(context)
    scope = f"{timing}{place}{evidence_time}"
    value = context.effective_visibility_m
    distance = f", местами около {int(round(value))} м" if value is not None else ""
    if context.condition == "mixed_visibility":
        return f"🌫 Видимость: {scope} снижена{distance}; возможна смесь влажной дымки и загрязнения воздуха."
    if context.condition == "dust_haze":
        return f"🌫 Видимость: {scope} возможна сухая пылевая дымка{distance}; ориентируйтесь на фактическую дальность обзора."
    if context.condition == "dense_fog":
        fog_timing = "сильный утренний туман" if post_type.startswith("morn") else "завтра утром сильный туман"
        return f"🌫 Видимость: {fog_timing}{place}{evidence_time} — местами около {int(round(value))} м." if value is not None else f"🌫 Видимость: {fog_timing}{place}{evidence_time}."
    if context.condition == "fog":
        return f"🌫 Видимость: {scope} туман{distance}; дальние объекты и побережье местами плохо различимы."
    if context.condition == "mist":
        return f"🌫 Видимость: {scope} влажная дымка{distance}; на дорогах и у моря видимость снижена."
    if context.condition == "reduced_visibility":
        return f"🌫 Видимость: {scope} местами снижена{distance}; на дорогах и у моря нужна дополнительная дистанция."
    return None


def _visibility_location_phrase(location_label: Any) -> str:
    label = str(location_label or "").strip()
    if not label:
        return ""
    forms = {
        "лимассол": "в Лимассоле",
        "ларнака": "в Ларнаке",
        "пафос": "в Пафосе",
        "никосия": "в Никосии",
    }
    return " " + forms.get(label.casefold(), f"в районе города {label}")


def _visibility_time_phrase(context: CyprusVisibilityContext) -> str:
    local_dt = _local_datetime(context.observation_time, "Asia/Nicosia")
    if local_dt is None:
        return ""
    time_label = local_dt.strftime("%H:%M")
    if context.evidence_source.startswith("current"):
        return f" (данные на {time_label})"
    if context.evidence_source.startswith("hourly_morning"):
        return f" (прогноз на {time_label})"
    return ""


def _structured_visibility_lines(text: Any) -> list[str]:
    lines: list[str] = []
    for raw_line in str(text or "").splitlines():
        line = re.sub(r"</?b>", "", raw_line, flags=re.I).strip()
        if re.match(r"^🌫\s*Видимость\s*:", line, flags=re.I):
            lines.append(line)
    return lines


def has_structured_visibility_alert(text: Any) -> bool:
    """Return true only for the explicit line backed by visibility context."""
    return any(
        re.search(r"дымк|туман|снижен|fog|haze", line, flags=re.I)
        for line in _structured_visibility_lines(text)
    )


def visibility_condition_from_text(text: str) -> str:
    low = "\n".join(_structured_visibility_lines(text)).lower()
    if not low:
        return "clear"
    if "смесь влажной дымки и загрязнения" in low:
        return "mixed_visibility"
    if "сухая пылевая дымка" in low or "пылевая дымка" in low:
        return "dust_haze"
    if "сильный утренний туман" in low or "сильный туман" in low:
        return "dense_fog"
    if "видимость:" in low and " туман" in low:
        return "fog"
    if "видимость:" in low and "влажная дымка" in low:
        return "mist"
    if "видимость:" in low and "снижен" in low:
        return "reduced_visibility"
    return "clear"


def visibility_penalty(context_or_condition: Any) -> float:
    if isinstance(context_or_condition, CyprusVisibilityContext):
        condition = context_or_condition.condition
    elif hasattr(context_or_condition, "visibility_condition"):
        condition = str(getattr(context_or_condition, "visibility_condition") or "clear")
    else:
        condition = str(context_or_condition or "clear")
    if condition in {"dense_fog", "fog", "mixed_visibility"}:
        return 0.5
    if condition in {"mist", "reduced_visibility", "dust_haze"}:
        return 0.2
    return 0.0


def visibility_air_penalty(context_or_condition: Any, aqi_penalty: Any) -> float:
    existing_air = normalize_number(aqi_penalty, non_negative=True) or 0.0
    return max(existing_air, visibility_penalty(context_or_condition))


def visibility_diagnostics(
    context: CyprusVisibilityContext,
    *,
    aqi_penalty: float = 0.0,
    fog_text_added: bool,
    fog_visual_rule: bool,
) -> dict[str, Any]:
    return {
        "condition": context.condition,
        "confidence": context.confidence,
        "current_visibility_m": context.current_visibility_m,
        "morning_min_visibility_m": context.morning_min_visibility_m,
        "humidity_pct": context.humidity_pct,
        "temperature_c": context.temperature_c,
        "dew_point_c": context.dew_point_c,
        "dew_point_spread_c": context.dew_point_spread_c,
        "weather_code": context.weather_code,
        "weather_code_source": context.weather_code_source,
        "aqi": context.aqi,
        "pm25": context.pm25,
        "pm10": context.pm10,
        "evidence_source": context.evidence_source,
        "observation_time": context.observation_time,
        "target_date": context.target_date,
        "location_label": context.location_label,
        "classification_reason": context.classification_reason,
        "score_penalty": visibility_air_penalty(context, aqi_penalty),
        "fog_text_added": bool(fog_text_added),
        "fog_visual_rule": bool(fog_visual_rule),
        "dust_vs_fog_classification": context.condition,
    }


__all__ = [
    "CyprusVisibilityContext",
    "DENSE_FOG_MAX_M",
    "FOG_MAX_M",
    "MIST_MAX_M",
    "REDUCED_VISIBILITY_MAX_M",
    "VISIBILITY_CONDITIONS",
    "build_cyprus_visibility_line",
    "classify_visibility_values",
    "dew_point_spread_c",
    "get_cyprus_visibility_context",
    "has_structured_visibility_alert",
    "normalize_number",
    "normalize_visibility_m",
    "visibility_air_penalty",
    "visibility_condition_from_text",
    "visibility_diagnostics",
    "visibility_penalty",
]
