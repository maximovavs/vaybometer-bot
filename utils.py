#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
0–1. UTILS MODULE
• Утилиты для преобразования и форматирования разных показателей
• Константы для иконок погоды и качества воздуха
"""
import math
import logging
from typing import Union
import pendulum

# ─────────── CONSTANTS ──────────────────────────────────────────
WEATHER_ICONS: dict[str, str] = {
    "ясно":       "☀️",
    "переменная": "🌤️",
    "пасмурно":   "☁️",
    "дождь":      "🌧️",
    "туман":      "🌁",
}

AIR_EMOJI: dict[str, str] = {
    # Уровни по US-EPA → кружки-эмодзи
    "хороший":       "🟢",
    "умеренный":     "🟡",
    "вредный":       "🟠",
    "оч. вредный":   "🔴",
    "опасный":       "🟣",
    "н/д":           "⚪️",
}

# Факты дня: ключ = MM-DD
FACTS: dict[str, str] = {
    "05-11": "11 мая — День морского бриза на Кипре 🌬️",
    "06-08": "8 июня 2004 — транзит Венеры по диску Солнца 🌞",
    "07-20": "20 июля — на Кипре собирают первый урожай винограда 🍇",
    # добавьте по необходимости
}

# ─────────── UTILITY FUNCTIONS ─────────────────────────────────
COMPASS = ["N","NNE","NE","ENE","E","ESE","SE","SSE",
           "S","SSW","SW","WSW","W","WNW","NW","NNW"]

def compass(deg: float) -> str:
    """Преобразует градусы 0–360° → направление N/NE/E…"""
    try:
        idx = int((deg / 22.5) + 0.5) % 16
        return COMPASS[idx]
    except Exception:
        logging.debug("compass: invalid deg=%s", deg)
        return "—"


def clouds_word(pc: int) -> str:
    """%-облачности → "ясно"/"переменная"/"пасмурно""""
    if pc < 25:
        return "ясно"
    if pc < 70:
        return "переменная"
    return "пасмурно"


def wind_phrase(km_h: float) -> str:
    """Скорость ветра → "штиль"/"слабый"/"умеренный"/"сильный""""
    if km_h < 2:
        return "штиль"
    if km_h < 8:
        return "слабый"
    if km_h < 14:
        return "умеренный"
    return "сильный"


def safe(v: Union[None, str, float, int], unit: str = "") -> str:
    """Красивый вывод значения (None или '—' → '—'), иначе число+unit""""
    if v is None or v == "—":
        return "—"
    try:
        if isinstance(v, (int, float)):
            return f"{v:.1f}{unit}"
        return f"{v}{unit}"
    except Exception:
        return f"{v}{unit}"


def aqi_color(aqi: Union[int, float, str, None]) -> str:
    """AQI → цветокружок-эмодзи по EPA"""
    if aqi is None or aqi == "—":
        return AIR_EMOJI.get("н/д", "⚪️")
    try:
        val = float(aqi)
    except Exception:
        return AIR_EMOJI.get("н/д", "⚪️")
    if val <= 50:
        lvl = "хороший"
    elif val <= 100:
        lvl = "умеренный"
    elif val <= 150:
        lvl = "вредный"
    elif val <= 200:
        lvl = "оч. вредный"
    else:
        lvl = "опасный"
    return AIR_EMOJI.get(lvl, AIR_EMOJI["н/д"])


def get_fact(date_obj: pendulum.Date) -> str:
    """Возвращает факт дня по дате или запасной факт"""
    key = date_obj.format("MM-DD")
    return FACTS.get(key,
                     f"На Кипре ≈340 солнечных дней в году ☀️")
