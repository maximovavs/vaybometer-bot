#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os, logging
from typing import Optional, Tuple, Dict, Any
from utils import _get

# ── координаты (Limassol) ────────────────────────────────────────
LAT, LON = 34.707, 33.022

# ── ключи ────────────────────────────────────────────────────────
AIR_KEY   = os.getenv("AIRVISUAL_KEY")   # IQAir
AMBEE_KEY = os.getenv("TOMORROW_KEY")    # Tomorrow.io

# ── градации AQI по US-EPA → текстовый уровень ───────────────────
def _aqi_level(aqi: float | int) -> str:
    if aqi <= 50:   return "хороший"
    if aqi <= 100:  return "умеренный"
    if aqi <= 150:  return "вредный"
    if aqi <= 200:  return "оч. вредный"
    return "опасный"

# ─────────────────────────────────────────────────────────────────
def get_air() -> Dict[str, Any]:
    """
    Возвращает **всегда** словарь с ключами
      {"lvl", "aqi", "pm25", "pm10"}
    Значение "н/д" (или None) ставится, если данные недоступны.
    """
    # базовое «нет данных»
    empty = {"lvl": "н/д", "aqi": "н/д", "pm25": None, "pm10": None}

    if not AIR_KEY:
        return empty

    j = _get("https://api.airvisual.com/v2/nearest_city",
             lat=LAT, lon=LON, key=AIR_KEY)
    if not j or "data" not in j:
        return empty

    try:
        pol   = j["data"]["current"]["pollution"]
        aqi   = pol.get("aqius")            # может быть None
        pm25  = pol.get("p2")
        pm10  = pol.get("p1")
        if aqi is None:
            return empty
        return {
            "lvl":  _aqi_level(aqi),
            "aqi":  aqi,
            "pm25": pm25,
            "pm10": pm10,
        }
    except Exception as e:
        logging.warning("Air fetch error: %s", e)
        return empty

# ─────────────────────────────────────────────────────────────────
def get_pollen() -> Optional[dict]:
    if not AMBEE_KEY:
        return None
    d = _get("https://api.tomorrow.io/v4/timelines",
             apikey=AMBEE_KEY,
             location=f"{LAT},{LON}",
             fields="treeIndex,grassIndex,weedIndex",
             timesteps="1d", units="metric")
    try:
        return d["data"]["timelines"][0]["intervals"][0]["values"]
    except Exception as e:
        logging.warning("Pollen fetch error: %s", e)
        return None

# ─────────────────────────────────────────────────────────────────
def get_sst() -> Optional[float]:
    j = _get("https://marine-api.open-meteo.com/v1/marine",
             latitude=LAT, longitude=LON,
             hourly="sea_surface_temperature", timezone="UTC")
    try:
        return round(j["hourly"]["sea_surface_temperature"][0], 1)
    except Exception:
        return None

# ─────────────────────────────────────────────────────────────────
def get_kp() -> Tuple[Optional[float], str]:
    j = _get("https://services.swpc.noaa.gov/products/noaa-planetary-k-index.json")
    try:
        kp_val = float(j[-1][1])
    except Exception:
        return None, "н/д"
    if kp_val < 4:   state = "спокойный"
    elif kp_val < 5: state = "повышенный"
    else:            state = "буря"
    return kp_val, state
