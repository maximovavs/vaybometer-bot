#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
utils.py  • вспомогательные функции и константы VayboMeter-бота
"""

from __future__ import annotations
import logging, math, time, random, requests, pendulum
from typing import Any, Dict, Optional

# ──────────────────────── Компас, облака, ветер ──────────────────────────
COMPASS = [
    "N","NNE","NE","ENE","E","ESE","SE","SSE",
    "S","SSW","SW","WSW","W","WNW","NW","NNW"
]

def compass(deg: float) -> str:
    return COMPASS[int((deg/22.5)+.5) % 16]

def clouds_word(pc: int) -> str:
    if pc < 25:  return "ясно"
    if pc < 70:  return "переменная"
    return "пасмурно"

def wind_phrase(km_h: float) -> str:
    if km_h < 2:   return "штиль"
    if km_h < 8:   return "слабый"
    if km_h < 14:  return "умеренный"
    return "сильный"

def safe(v: Any, unit: str = "") -> str:
    """None → «—»; число → форматированная строка с unit."""
    if v in (None, "None", "—"):
        return "—"
    return f"{v:.1f}{unit}" if isinstance(v, (int, float)) else f"{v}{unit}"

# ──────────────────────── AQI & PM раскраска ─────────────────────────────
def aqi_color(aqi: int | float | str) -> str:
    if aqi == "—": return "⚪"
    aqi = float(aqi)
    if aqi <=  50: return "🟢"
    if aqi <= 100: return "🟡"
    if aqi <= 150: return "🟠"
    if aqi <= 200: return "🔴"
    if aqi <= 300: return "🟣"
    return "🟤"

def pm_color(pm: Optional[float | int | str], with_unit: bool = False) -> str:
    """
    Быстрая цветовая индикация концентраций PM₂.₅ / PM₁₀.
    ▸ Возвращает, напр.,  «🟡27» или «🟢 8 µg/м³», если with_unit=True
    """
    if pm in (None, "—", "н/д"):       # нет данных
        return "⚪ н/д"
    try:
        val = float(pm)
    except (TypeError, ValueError):
        return "⚪ н/д"

    # грубое соответствие US-EPA для PM₂.₅
    if val <= 12:    col = "🟢"
    elif val <= 35:  col = "🟡"
    elif val <= 55:  col = "🟠"
    elif val <=150:  col = "🔴"
    elif val <=250:  col = "🟣"
    else:            col = "🟤"

    txt = f"{int(round(val))}"
    if with_unit:
        txt += " µg/м³"
    return f"{col}{txt}"

# ──────────────────────── «Факт дня» ─────────────────────────────────────
FACTS: Dict[str, str] = {
    "05-11": "11 мая — День морского бриза на Кипре 🌬️",
    "06-08": "8 июня 2004 — транзит Венеры по диску Солнца 🌞",
    "07-20": "20 июля — на Кипре собирают первый урожай винограда 🍇",
    "10-01": "1 октября — День Кипра 🇨🇾",
}

def get_fact(d: pendulum.Date) -> str:
    return FACTS.get(d.format("MM-DD"),
                     "На Кипре в году ≈ 340 солнечных дней ☀️")

# ──────────────────────── Иконки & цвета ────────────────────────────────
WEATHER_ICONS = {"ясно":"☀️","переменная":"🌤️","пасмурно":"☁️",
                 "дождь":"🌧️","туман":"🌁"}
AIR_EMOJI     = {"хороший":"🟢","умеренный":"🟡","вредный":"🟠",
                 "оч. вредный":"🔴","опасный":"🟣","н/д":"⚪"}

K_COLOR = {
    "green":  range(0, 4),
    "yellow": range(4, 6),
    "red":    range(6, 10),
}
def kp_emoji(kp: float) -> str:
    k = int(round(kp))
    if k in K_COLOR["green"]:   return "🟢"
    if k in K_COLOR["yellow"]:  return "🟡"
    return "🔴"

# ──────────────────────── Тренд давления ────────────────────────────────
def pressure_trend(w: Dict[str, Any]) -> str:
    """
    ↑ если ближайший час > +2 гПа, ↓ < −2 гПа, иначе →.
    w — объект, полученный из get_weather().
    """
    hp = w.get("hourly", {}).get("surface_pressure", [])
    if len(hp) < 2:
        return "→"
    diff = hp[1] - hp[0]
    if   diff >= 2:  return "↑"
    elif diff <=-2:  return "↓"
    return "→"

# ──────────────────────── HTTP-обёртки ───────────────────────────────────
_HEADERS = {
    "User-Agent": "VayboMeter/1.0 (+https://github.com/)",
    "Accept":     "application/json",
}

def _get_retry(url: str, retries: int = 2, **params) -> Optional[dict]:
    attempt = 0
    while attempt <= retries:
        try:
            r = requests.get(url, params=params, timeout=15, headers=_HEADERS)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            attempt += 1
            if attempt > retries:
                logging.warning("%s – %s (attempts=%d)",
                                url.split("/")[2], e, attempt)
                return None
            time.sleep(0.5 * attempt)     # 0.5 s, 1.0 s, …

def _get(url: str, **params) -> Optional[dict]:
    """Старый интерфейс для совместимости: 2 повторные попытки."""
    return _get_retry(url, retries=2, **params)

# ──────────────────────── module self-test ──────────────────────────────
if __name__ == "__main__":
    print("pm_color demo:",
          pm_color(8), pm_color(27), pm_color(78, True), pm_color(None))
    print("AQI demo:", aqi_color(42), aqi_color(160), aqi_color("—"))
    print("Fact today:", get_fact(pendulum.today()))
