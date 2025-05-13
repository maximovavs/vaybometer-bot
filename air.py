#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
air.py

• get_air() → возвращает словарь:
    {
      "aqi":   float|int,
      "lvl":   str,          # уровень качества по шкале US-EPA
      "pm25":  float|None,   # концентрация PM2.5
      "pm10":  float|None    # концентрация PM10
    }
  или None, если данных нет.

• get_kp() → возвращает кортеж (kp_value: float, state: str),
    где state ∈ {"спокойный","повышенный","буря","н/д"}.
"""

import os
import logging
from typing import Optional, Tuple

from utils import _get, aqi_color

AIR_KEY = os.getenv("AIRVISUAL_KEY")

def get_air() -> Optional[dict]:
    """
    Запрашивает ближайший город из IQAir и возвращает:
      - aqi   (AQI по US-EPA)
      - lvl   (название категории качества воздуха)
      - pm25  (PM2.5, µg/m³)
      - pm10  (PM10, µg/m³)
    """
    if not AIR_KEY:
        return None

    j = _get(
        "https://api.airvisual.com/v2/nearest_city",
        lat=os.getenv("LAT"), lon=os.getenv("LON"),
        key=AIR_KEY
    )
    if not j or "data" not in j:
        return None

    try:
        pol  = j["data"]["current"]["pollution"]
        aqi  = pol.get("aqius")
        pm25 = pol.get("p2")
        pm10 = pol.get("p1")
    except Exception as e:
        logging.warning("get_air: unexpected response format: %s", e)
        return None

    return {
        "aqi":   aqi,
        "lvl":   aqi_color(aqi),
        "pm25":  pm25,
        "pm10":  pm10,
    }

def get_kp() -> Tuple[Optional[float], str]:
    """
    Возвращает текущее значение планетарного K-индекса и его категорию:
      - <4   → "спокойный"
      - 4–5  → "повышенный"
      - ≥5   → "буря"
      - None → "н/д"
    """
    j = _get("https://services.swpc.noaa.gov/products/noaa-planetary-k-index.json")
    if not j:
        return None, "н/д"

    try:
        kp_val = float(j[-1][1])
    except Exception as e:
        logging.warning("get_kp: cannot parse Kp value: %s", e)
        return None, "н/д"

    if kp_val < 4:
        state = "спокойный"
    elif kp_val < 5:
        state = "повышенный"
    else:
        state = "буря"

    return kp_val, state
