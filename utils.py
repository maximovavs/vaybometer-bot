#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import math
import requests
import logging
import random
import pendulum

# ── румбы для компаса ───────────────────────────────────────────
COMPASS = [
    "N","NNE","NE","ENE","E","ESE","SE","SSE",
    "S","SSW","SW","WSW","W","WNW","NW","NNW"
]

def compass(deg: float) -> str:
    """Числовой угол 0–360° → направление N/NE/E…."""
    return COMPASS[int((deg / 22.5) + .5) % 16]

def clouds_word(pc: int) -> str:
    """Процент облачности → 'ясно'/'переменная'/'пасмурно'."""
    if pc < 25:
        return "ясно"
    if pc < 70:
        return "переменная"
    return "пасмурно"

def wind_phrase(km_h: float) -> str:
    """Скорость ветра → 'штиль'/'слабый'/'умеренный'/'сильный'."""
    if km_h < 2:
        return "штиль"
    if km_h < 8:
        return "слабый"
    if km_h < 14:
        return "умеренный"
    return "сильный"

def safe(v, unit: str = "") -> str:
    """Нормализует None → '—', число → форматированную строку."""
    if v in (None, "None", "—"):
        return "—"
    if isinstance(v, (int, float)):
        return f"{v:.1f}{unit}"
    return f"{v}{unit}"

# ── AQI → цветокружок-эмодзи по US-EPA ─────────────────────────────
def aqi_color(aqi: int | float | str) -> str:
    """AQI → 🟢🟡🟠🔴🟣 (строка)."""
    if aqi == "—":
        return "⚪"
    aqi = float(aqi)
    if aqi <= 50:
        return "🟢"
    if aqi <= 100:
        return "🟡"
    if aqi <= 150:
        return "🟠"
    if aqi <= 200:
        return "🔴"
    if aqi <= 300:
        return "🟣"
    return "🟤"

# ── «Факт дня» по дате или запасной ────────────────────────────
FACTS = {
    "05-11": "11 мая — День морского бриза на Кипре 🌬️",
    "06-08": "8 июня 2004 г. — транзит Венеры по диску Солнца 🌞",
    "07-20": "20 июля — на Кипре собирают первый урожай винограда 🍇",
}

def get_fact(date_obj: pendulum.Date) -> str:
    """
    Возвращает факт по ключу MM-DD из словаря FACTS
    или запасной факт.
    """
    key = date_obj.format("MM-DD")
    return FACTS.get(key, "На Кипре в году ≈340 солнечных дней ☀️")

# ── Эмодзи-иконки ────────────────────────────────────────────────
WEATHER_ICONS = {
    "ясно":       "☀️",
    "переменная": "🌤️",
    "пасмурно":   "☁️",
    "дождь":      "🌧️",
    "туман":      "🌁",
}

AIR_EMOJI = {
    "хороший":      "🟢",
    "умеренный":    "🟡",
    "вредный":      "🟠",
    "оч. вредный":  "🔴",
    "опасный":      "🟣",
    "н/д":          "⚪",
}

# ── Универсальный HTTP-геттер с логированием ────────────────────
def _get(url: str, **params) -> dict | None:
    """
    Выполняет GET-запрос, возвращает распарсенный JSON
    или None при ошибке.
    """
    try:
        r = requests.get(url, params=params, timeout=15, headers={"User-Agent": "VayboMeter"})
        r.raise_for_status()
        return r.json()
    except Exception as e:
        host = url.split("/")[2]
        logging.warning("%s – %s", host, e)
        return None
