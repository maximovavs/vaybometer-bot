#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Deterministic editorial voice helpers for Cyprus VayboMeter posts."""
from __future__ import annotations

import hashlib
from datetime import date
from typing import Any, Iterable


CYPRUS_MORNING_VARIANTS = {
    "HOT_UV": [
        "солнце будет задавать правила. Всё важное лучше сделать утром, а днём перейти в более спокойный режим.",
        "день не для геройства на солнце. Активное утро и мягкий вечер дадут больше, чем дневной рывок.",
        "силы будут расходоваться быстрее обычного, поэтому паузы сегодня — часть нормального плана.",
        "лучше прожить день в два окна: активное утро и более свободный вечер.",
    ],
    "WINDY_COAST": [
        "у воды будет легче, но порывы могут быстро менять ощущение комфорта.",
        "море зовёт, но конкретное место лучше выбирать уже по фактическому ветру.",
        "побережье подойдёт для прогулки, если найти защищённый маршрут.",
    ],
    "POOR_AIR": [
        "воздух сегодня не самый лёгкий, поэтому спокойная прогулка будет лучше интенсивной активности.",
        "особенно важно ориентироваться не только на температуру, но и на собственное самочувствие.",
        "при чувствительности к пыли лучше сократить активную улицу и оставить больше времени на помещение.",
    ],
    "CALM": [
        "день располагает к обычным делам и морю без лишней спешки.",
        "погода достаточно ровная — можно планировать свободнее, сохранив дневную паузу.",
    ],
}

CYPRUS_EVENING_VARIANTS = {
    "HOT_UV": [
        "день потребует не скорости, а хорошего распределения сил: важное лучше оставить на утро.",
        "завтра солнце задаст ритм — активное утро и спокойный вечер будут комфортнее дневного рывка.",
        "лучше заранее разделить день на два окна и оставить полдень для паузы.",
    ],
    "WINDY_COAST": [
        "у моря будет легче, но конкретное место лучше выбрать по фактическим порывам.",
        "побережье подойдёт для прогулки, если утром найти защищённый маршрут.",
        "морские планы лучше подтвердить по ветру уже утром.",
    ],
    "POOR_AIR": [
        "если воздух останется тяжёлым, спокойная прогулка будет лучше интенсивной активности.",
        "при чувствительности к пыли завтра лучше оставить больше времени для помещения.",
        "температура — не единственный ориентир: утром стоит проверить и качество воздуха.",
    ],
    "CALM": [
        "день выглядит достаточно ровным — планы можно держать свободнее.",
        "завтра можно двигаться без лишней спешки, сохранив дневную паузу.",
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
