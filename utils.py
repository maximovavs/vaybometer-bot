#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
utils.py ─ вспомогательные функции и константы, используемые во всех модулях
"""

from __future__ import annotations
import logging, math, random, time
import requests, pendulum

# ─────────────────────────── компас / погода ────────────────────────────
COMPASS = [
    "N","NNE","NE","ENE","E","ESE","SE","SSE",
    "S","SSW","SW","WSW","W","WNW","NW","NNW"
]

def compass(deg: float) -> str:
    """0–360 ° → румба компаса (N, NE …)."""
    return COMPASS[int((deg / 22.5) + .5) % 16]

def clouds_word(pc: int) -> str:
    """% облачности → «ясно»/«переменная»/«пасмурно»."""
    if pc < 25:
        return "ясно"
    if pc < 70:
        return "переменная"
    return "пасмурно"

def wind_phrase(km_h: float) -> str:
    """Скорость ветра (км/ч) → описательный текст."""
    if km_h < 2:
        return "штиль"
    if km_h < 8:
        return "слабый"
    if km_h < 14:
        return "умеренный"
    return "сильный"

def safe(v, unit: str = "") -> str:
    """Число → «xx.x unit», None/— → «—»."""
    if v in (None, "None", "—", "н/д"):
        return "н/д"
    return f"{v:.1f}{unit}" if isinstance(v, (int, float)) else f"{v}{unit}"

# ───────────────────────────── AQI и PM цвет ────────────────────────────
def aqi_color(aqi) -> str:
    """Возвращает кружок-эмодзи по шкале US-EPA."""
    if aqi == "н/д":
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

def pm_color(pm_val) -> str:
    """
    Цветная строка для PM-показателей.
    • «н/д»               → 'н/д'
    • 12 → '🟢12', 35 → '🟡35' …
    Пороги взяты из WHO/US-EPA.
    """
    if pm_val in (None, "н/д", "—"):
        return "н/д"
    v = float(pm_val)
    if v <= 15:
        mark = "🟢"
    elif v <= 30:
        mark = "🟡"
    elif v <= 55:
        mark = "🟠"
    elif v <= 110:
        mark = "🔴"
    else:
        mark = "🟣"
    return f"{mark}{int(round(v))}"

# ───────────────────────────── Факты дня ────────────────────────────────
FACTS = {
    "01-01": "1 января — на Кипре цветут миндальные деревья 🌸",
    "02-14": "14 февраля 1960 — День независимости Республики Кипр 🇨🇾",
    "03-22": "22 марта — Всемирный день воды 💧",
    "04-27": "27 апреля 1961 — открыт аэропорт Ларнаки ✈️",
    "05-11": "11 мая — День морского бриза на Кипре 🌬️",
    "06-08": "8 июня 2004 — транзит Венеры по диску Солнца 🌞",
    "07-20": "20 июля — собирают первый урожай винограда 🍇",
    "08-16": "16 августа — самый тёплый день в среднем за год 🔥",
    "09-27": "27 сентября — Всемирный день туризма 🧳",
    "10-31": "31 октября — пик бархатного сезона на Кипре 🏖️",
    "12-22": "22 декабря — на Кипре уже распускаются анемоны 🌺",
}

def get_fact(date_obj: pendulum.Date) -> str:
    """Возвращает подходящий факт по дате или дефолтную фразу."""
    return FACTS.get(date_obj.format("MM-DD"),
                     "На Кипре в году ≈340 солнечных дней ☀️")

# ───────────────────────────── иконки / эмодзи ──────────────────────────
WEATHER_ICONS = {"ясно":"☀️","переменная":"🌤️","пасмурно":"☁️","дождь":"🌧️","туман":"🌁"}
AIR_EMOJI     = {"хороший":"🟢","умеренный":"🟡","вредный":"🟠",
                 "оч. вредный":"🔴","опасный":"🟣","н/д":"⚪"}

# ─────────────────────────── K-index «светофор» ─────────────────────────
K_COLOR = { "green": range(0,4), "yellow": range(4,6), "red": range(6,10) }

def kp_emoji(kp: float) -> str:
    k = int(round(kp))
    if k in K_COLOR["green"]:
        return "🟢"
    if k in K_COLOR["yellow"]:
        return "🟡"
    return "🔴"

# ───────────────────────────── тренд давления ───────────────────────────
def pressure_trend(w: dict) -> str:
    """
    Возвращает стрелку тренда давления на ближайший час.
    >2 гПа → ↑, <-2 гПа → ↓, иначе →
    """
    series = w.get("hourly", {}).get("surface_pressure", [])
    if len(series) < 2:
        return "→"
    diff = series[1] - series[0]
    if diff >= 2:
        return "↑"
    if diff <= -2:
        return "↓"
    return "→"

# ───────────────────────────── HTTP обёртки ─────────────────────────────
def _get_retry(url: str, retries: int = 2, **params):
    """GET с повторами (экспоненциальная задержка)."""
    attempt = 0
    while attempt <= retries:
        try:
            r = requests.get(url, params=params, timeout=15,
                             headers={"User-Agent": "VayboMeter"})
            r.raise_for_status()
            return r.json()
        except Exception as e:
            attempt += 1
            if attempt > retries:
                logging.warning("%s – %s (attempts=%d)",
                                url.split('/')[2], e, attempt)
                return None
            time.sleep(0.5 * attempt)

def _get(url: str, **params):
    """Исторический алиас `_get_retry(..., retries=2)`."""
    return _get_retry(url, retries=2, **params)
