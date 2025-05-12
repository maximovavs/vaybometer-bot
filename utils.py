#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import logging
import requests
import pendulum
import math

# ─────────── HTTP / общие утилиты ─────────────────────────────
HEADERS = {"User-Agent": "VayboMeter/5.4"}

def _get(url: str, **params) -> dict | None:
    try:
        r = requests.get(url, params=params, timeout=15, headers=HEADERS)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        host = url.split("/")[2]
        logging.warning("%s – %s", host, e)
        return None

# ─────────── компас, облака, ветер ────────────────────────────
COMPASS = ["N","NNE","NE","ENE","E","ESE","SE","SSE",
           "S","SSW","SW","WSW","W","WNW","NW","NNW"]

def compass(deg: float) -> str:
    return COMPASS[int((deg/22.5)+.5) % 16]

def clouds_word(pc: int) -> str:
    return "ясно"      if pc < 25 else \
           "переменная" if pc < 70 else \
           "пасмурно"

def wind_phrase(km_h: float) -> str:
    return ("штиль"     if km_h < 2  else
            "слабый"    if km_h < 8  else
            "умеренный" if km_h < 14 else
            "сильный")

# ─────────── AQI → цвет / safe format ─────────────────────────
def aqi_color(aqi: int|float|str) -> str:
    if aqi in (None, "—"):
        return "⚪️"
    a = float(aqi)
    return ("🟢" if a <= 50   else
            "🟡" if a <=100  else
            "🟠" if a <=150  else
            "🔴" if a <=200  else
            "🟣" if a <=300  else
            "🟤")

def safe(v, unit: str="") -> str:
    if v in (None, "None", "—"):
        return "—"
    if isinstance(v, (int, float)):
        return f"{v:.1f}{unit}"
    return f"{v}{unit}"

# ─────────── факт дня ──────────────────────────────────────────
FACTS: dict[str,str] = {
    "05-11": "11 мая — День морского бриза на Кипре 🌬️",
    "06-08": "8 июня 2004 г. — транзит Венеры по диску Солнца 🌞",
    "07-20": "20 июля — первый урожай винограда на Кипре 🍇",
}

def get_fact(date_obj: pendulum.Date) -> str:
    key = date_obj.format("MM-DD")
    return FACTS.get(key, "На Кипре ≈340 солнечных дней в году ☀️")
