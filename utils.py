#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import math, time, random, logging, requests, pendulum

# ── румбы компаса ────────────────────────────────────────────────
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

def safe(v, unit=""):
    if v in (None, "None", "—"):
        return "—"
    return f"{v:.1f}{unit}" if isinstance(v, (int,float)) else f"{v}{unit}"

# ── AQI цвет ─────────────────────────────────────────────────────
def aqi_color(aqi):
    if aqi == "—": return "⚪"
    aqi = float(aqi)
    if aqi<=50:  return "🟢"
    if aqi<=100: return "🟡"
    if aqi<=150: return "🟠"
    if aqi<=200: return "🔴"
    if aqi<=300: return "🟣"
    return "🟤"

# ── «Факт дня» ───────────────────────────────────────────────────
FACTS = {
    "05-11": "11 мая — День морского бриза на Кипре 🌬️",
    "06-08": "8 июня 2004 г. — транзит Венеры по диску Солнца 🌞",
    "07-20": "20 июля — на Кипре собирают первый урожай винограда 🍇",
}
def get_fact(d: pendulum.Date) -> str:
    return FACTS.get(d.format("MM-DD"), "На Кипре в году ≈340 солнечных дней ☀️")

# ── иконки ───────────────────────────────────────────────────────
WEATHER_ICONS = {"ясно":"☀️","переменная":"🌤️","пасмурно":"☁️","дождь":"🌧️","туман":"🌁"}
AIR_EMOJI     = {"хороший":"🟢","умеренный":"🟡","вредный":"🟠","оч. вредный":"🔴","опасный":"🟣","н/д":"⚪"}

# ── K-index «светофор» ───────────────────────────────────────────
K_COLOR = {                    # диапазоны включительно
    "green":  range(0,4),      # 0–3
    "yellow": range(4,6),      # 4–5
    "red":    range(6,10),     # 6–9
}
def kp_emoji(kp: float) -> str:
    k = int(round(kp))
    if k in K_COLOR["green"]:  return "🟢"
    if k in K_COLOR["yellow"]: return "🟡"
    return "🔴"

# ── тренд давления ───────────────────────────────────────────────
def pressure_trend(w: dict) -> str:
    """
    ⚙  w – объект get_weather().
    ▸ Берём массив hourly['surface_pressure']:
      • разница будущего часа – >2 гПа → ↑
      • <-2 гПа → ↓
      • иначе → →
    """
    hp = w.get("hourly", {}).get("surface_pressure", [])
    if len(hp) < 2:
        return "→"
    diff = hp[1] - hp[0]      # ближайший час
    if diff >= 2:   return "↑"
    if diff <= -2:  return "↓"
    return "→"

# ── HTTP-обёртки ─────────────────────────────────────────────────
def _get_retry(url: str, retries: int = 2, **params):
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
                logging.warning("%s – %s (attempts=%d)", url.split('/')[2], e, attempt)
                return None
            time.sleep(0.5 * attempt)   # 0.5s, 1.0s …

def _get(url: str, **params):
    return _get_retry(url, retries=2, **params)
