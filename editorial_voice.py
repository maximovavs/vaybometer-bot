#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Deterministic editorial voice helpers for Cyprus VayboMeter posts."""
from __future__ import annotations

import hashlib
from datetime import date
from typing import Any, Iterable


CYPRUS_MORNING_VARIANTS = {
    "LOCAL_WEATHER": [
        "жарко; у моря порывисто, а в горах возможны локальные изменения погоды.",
        "день лучше держать гибким: побережье, центр острова и горы могут ощущаться по-разному.",
    ],
    "HOT_UV": [
        "солнце сегодня диктует темп, и это нормально — тело считает такую погоду работой.",
        "день ощущается тяжелее, чем показывает термометр: жара забирает силы незаметно.",
        "силы уходят быстрее обычного, и усталость к вечеру будет честной, а не от лени.",
        "это день про выносливость, а не про скорость.",
    ],
    "WINDY_COAST": [
        "у воды будет легче, но порывы могут быстро менять ощущение комфорта.",
        "ветер сегодня заметно меняет картину дня: на солнце жарко, на ветру обманчиво прохладно.",
        "море сегодня живое и шумное — это ощущается даже с берега.",
    ],
    "POOR_AIR": [
        "воздух сегодня заметно тяжелее обычного, и дыхание это чувствует.",
        "сегодня стоит ориентироваться не только на температуру, но и на собственное самочувствие.",
        "при чувствительности к пыли такой день ощущается более утомительным, чем выглядит.",
    ],
    "CALM": [
        "день держится ровно, без резких перепадов.",
        "погода сегодня предсказуемая — без сюрпризов в обе стороны.",
    ],
}

CYPRUS_EVENING_VARIANTS = {
    "LOCAL_WEATHER": [
        "погода завтра будет разной в разных концах острова — одного ощущения дня не будет.",
        "побережье, центр и горы завтра проживут день по-своему.",
    ],
    "HOT_UV": [
        "завтрашняя жара ощущается сильнее, чем выглядит в прогнозе.",
        "день обещает быть выматывающим — солнце заберёт больше сил, чем кажется утром.",
        "завтра тело будет уставать быстрее обычного, и это ожидаемо.",
    ],
    "WINDY_COAST": [
        "у моря завтра будет легче, но ветер заметно меняет ощущение комфорта.",
        "завтра побережье будет ветреным и живым — тепло почувствуется иначе, чем в центре.",
        "ветер завтра станет главным ощущением дня у воды.",
    ],
    "POOR_AIR": [
        "если воздух останется тяжёлым, завтрашний день будет ощущаться более вязким.",
        "при чувствительности к пыли такой день утомляет заметнее обычного.",
        "температура завтра — не единственный ориентир: воздух тоже влияет на самочувствие.",
    ],
    "CALM": [
        "завтрашний день выглядит предсказуемым — резких сюрпризов погода не обещает.",
        "завтра погода держится ровно, без заметных перепадов.",
    ],
}

CYPRUS_WEEKLY_VARIANTS = [
    "Не пытаться прожить неделю на максимальной мощности. Лучший результат даст ритм: активное утро, дневная пауза и более свободный вечер.",
    "Эта неделя про разумное распределение сил. Не всё нужно успеть до того, как тело попросит остановиться.",
    "Сейчас особенно важно не путать продуктивность с постоянной активностью. Паузы тоже будут частью хорошего результата.",
]


def deterministic_variant(region: str, date_value: Any, scenario: str, variants: Iterable[str]) -> str:
    choices = list(variants)
    if not choices:
        return ""
    seed = hashlib.sha256(f"{region}|{date_value}|{scenario}".encode("utf-8")).hexdigest()
    return choices[int(seed[:8], 16) % len(choices)]


def _num(value: Any) -> float | None:
    try:
        if value in (None, "", "н/д"):
            return None
        return float(str(value).replace(",", "."))
    except Exception:
        return None


def _scenario(conditions: dict[str, Any]) -> str:
    aqi = _num(conditions.get("aqi"))
    pm25 = _num(conditions.get("pm25"))
    pm10 = _num(conditions.get("pm10"))
    if (
        conditions.get("poor_air")
        or isinstance(aqi, (int, float)) and aqi >= 100
        or isinstance(pm25, (int, float)) and pm25 >= 20
        or isinstance(pm10, (int, float)) and pm10 >= 50
    ):
        return "POOR_AIR"

    if conditions.get("rain"):
        return "LOCAL_WEATHER"

    max_temp = _num(conditions.get("max_temp"))
    uv = _num(conditions.get("uv"))
    if conditions.get("heat") or conditions.get("uv_high") or isinstance(max_temp, (int, float)) and max_temp >= 32 or isinstance(uv, (int, float)) and uv >= 6:
        return "HOT_UV"

    gust = _num(conditions.get("gust"))
    wind = _num(conditions.get("wind"))
    if conditions.get("wind") or isinstance(gust, (int, float)) and gust >= 10 or isinstance(wind, (int, float)) and wind >= 6:
        return "WINDY_COAST"

    return "CALM"


def build_morning_human_line(region: str, date_value: Any, conditions: dict[str, Any]) -> str:
    scenario = _scenario(conditions)
    phrase = deterministic_variant(region, date_value, scenario, CYPRUS_MORNING_VARIANTS[scenario])
    return f"💬 По ощущениям дня: {phrase}"


def build_evening_human_line(region: str, date_value: Any, conditions: dict[str, Any]) -> str:
    scenario = _scenario(conditions)
    phrase = deterministic_variant(region, date_value, f"EVENING_{scenario}", CYPRUS_EVENING_VARIANTS[scenario])
    return f"💬 Настрой на завтра: {phrase}"


def build_weekly_meaning(region: str, start_date: date | str, metrics: dict[str, Any]) -> str:
    scenario = "WEEKLY_HOT_UV" if _num(metrics.get("tmax_max")) and float(metrics["tmax_max"]) >= 32 else "WEEKLY"
    return deterministic_variant(region, start_date, scenario, CYPRUS_WEEKLY_VARIANTS)
