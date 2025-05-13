#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
air.py — получение данных по качеству воздуха, пыльце, температуре воды и K-индексу.
"""

import os
import logging
from typing import Optional, Tuple

from utils import _get, aqi_color

# Координаты Лимассола (используются для всех запросов в этом модуле)
LAT, LON = 34.707, 33.022

# Ключи из окружения
AIR_KEY   = os.getenv("AIRVISUAL_KEY")   # AQI / PM
AMBEE_KEY = os.getenv("TOMORROW_KEY")    # пыльца Tomorrow.io

def get_air() -> Optional[dict]:
    """
    Возвращает словарь:
      {
        "aqi":   <число>,
        "lvl":   <строка: 'хороший', 'умеренный', ...>,
        "pm25":  <число или None>,
        "pm10":  <число или None>,
      }
    или None, если нет ключа или произошла ошибка.
    """
    if not AIR_KEY:
        return None

    j = _get(
        "http://api.airvisual.com/v2/nearest_city",
        lat=LAT, lon=LON, key=AIR_KEY
    )
    if not j or "data" not in j:
        return None

    pol  = j["data"]["current"]["pollution"]
    aqi  = pol.get("aqius")
    pm25 = pol.get("p2")
    pm10 = pol.get("p1")

    return {
        "aqi":   aqi,
        "lvl":   aqi_color(aqi),
        "pm25":  pm25,
        "pm10":  pm10,
    }

def get_pollen() -> Optional[dict]:
    """
    Возвращает словарь:
      {"treeIndex":…, "grassIndex":…, "weedIndex":…}
    или None, если нет ключа или ошибка.
    """
    if not AMBEE_KEY:
        return None

    d = _get(
        "https://api.tomorrow.io/v4/timelines",
        apikey=AMBEE_KEY,
        location=f"{LAT},{LON}",
        fields="treeIndex,grassIndex,weedIndex",
        timesteps="1d",
        units="metric",
    )
    try:
        return d["data"]["timelines"][0]["intervals"][0]["values"]
    except Exception as e:
        logging.warning("Pollen: %s", e)
        return None

def get_sst() -> Optional[float]:
    """
    Возвращает температуру поверхности моря (°C) или None.
    """
    j = _get(
        "https://marine-api.open-meteo.com/v1/marine",
        latitude=LAT,
        longitude=LON,
        hourly="sea_surface_temperature",
        timezone="UTC",
    )
    try:
        return round(j["hourly"]["sea_surface_temperature"][0], 1)
    except Exception:
        return None

def get_kp() -> Tuple[Optional[float], str]:
    """
    Возвращает кортеж (kp_value, state), где state ∈ {"спокойный","повышенный","буря","н/д"}.
    """
    j = _get("https://services.swpc.noaa.gov/products/noaa-planetary-k-index.json")
    try:
        kp = float(j[-1][1])
    except Exception:
        return None, "н/д"

    if kp < 4:
        state = "спокойный"
    elif kp < 5:
        state = "повышенный"
    else:
        state = "буря"

    return kp, state
