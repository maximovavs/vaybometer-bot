#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
air.py
~~~~~~

• Два открытых источника качества воздуха
  1) IQAir / nearest_city  (нужен AIRVISUAL_KEY, но может быть None)
  2) Open-Meteo Air-Quality (без ключа)

• merge_air_sources()  ⇒ объединяет словари, приоритет IQAir → Open-Meteo
• get_air()            ⇒ всегда возвращает dict с ключами
                          {'lvl','aqi','pm25','pm10'} (с «н/д» / None)
"""

from __future__ import annotations
import os
import logging
from typing import Dict, Any, Optional

from utils import _get, aqi_color

# ────────── координаты (Limassol) ────────────────────────────────
LAT, LON = 34.707, 33.022

# ────────── API-ключи ────────────────────────────────────────────
AIR_KEY = os.getenv("AIRVISUAL_KEY")  # IQAir

# ────────── helpers ──────────────────────────────────────────────
def _aqi_level(aqi: float | int | str) -> str:
    if aqi in ("н/д", None):
        return "н/д"
    aqi = float(aqi)
    if aqi <=  50: return "хороший"
    if aqi <= 100: return "умеренный"
    if aqi <= 150: return "вредный"
    if aqi <= 200: return "оч. вредный"
    return "опасный"

# ────────── источники ────────────────────────────────────────────
def _src_iqair() -> Optional[Dict[str, Any]]:
    """IQAir nearest_city — возвращает словарь или None."""
    if not AIR_KEY:
        return None
    j = _get(
        "https://api.airvisual.com/v2/nearest_city",
        lat=LAT, lon=LON, key=AIR_KEY
    )
    if not j or "data" not in j:
        return None
    try:
        pol = j["data"]["current"]["pollution"]
        return {
            "aqi":  pol.get("aqius", "н/д"),
            "pm25": pol.get("p2"   , None),
            "pm10": pol.get("p1"   , None),
        }
    except Exception as e:
        logging.warning("IQAir source error: %s", e)
        return None

def _src_openmeteo() -> Optional[Dict[str, Any]]:
    """Open-Meteo Air-Quality — возвращает словарь или None."""
    j = _get(
        "https://air-quality-api.open-meteo.com/v1/air-quality",
        latitude=LAT, longitude=LON,
        hourly="pm10,pm2_5,us_aqi", timezone="UTC"
    )
    if not j or "hourly" not in j:
        return None
    try:
        aqi_arr  = j["hourly"]["us_aqi"]
        pm25_arr = j["hourly"]["pm2_5"]
        pm10_arr = j["hourly"]["pm10"]
        aqi  = aqi_arr[0]  if aqi_arr  else "н/д"
        pm25 = pm25_arr[0] if pm25_arr else None
        pm10 = pm10_arr[0] if pm10_arr else None
        # иногда "-1" означает отсутствие данных
        aqi  = aqi  if isinstance(aqi, (int, float)) and aqi >= 0 else "н/д"
        pm25 = pm25 if isinstance(pm25, (int, float)) and pm25 >= 0 else None
        pm10 = pm10 if isinstance(pm10, (int, float)) and pm10 >= 0 else None
        return {"aqi": aqi, "pm25": pm25, "pm10": pm10}
    except Exception as e:
        logging.warning("Open-Meteo AQ source error: %s", e)
        return None

# ────────── слияние ──────────────────────────────────────────────
def merge_air_sources(
    src1: Optional[Dict[str,Any]],
    src2: Optional[Dict[str,Any]]
) -> Dict[str, Any]:
    """
    Приоритет src1; дополняем тем, чего в нём нет или что = None/"н/д".
    Результат всегда имеет ключи 'aqi','pm25','pm10','lvl'.
    """
    base = {"aqi": "н/д", "pm25": None, "pm10": None}
    for k in ("aqi","pm25","pm10"):
        v1 = src1.get(k) if src1 else None
        if v1 not in (None, "н/д"):
            base[k] = v1
        else:
            v2 = src2.get(k) if src2 else None
            base[k] = v2 if v2 not in (None, "н/д") else base[k]
    base["lvl"] = _aqi_level(base["aqi"])
    return base

# ────────── основной API ─────────────────────────────────────────
def get_air() -> Dict[str, Any]:
    """
    Returns:
      {
        'lvl':  <строка уровня>,
        'aqi':  <число или "н/д">,
        'pm25': <число или None>,
        'pm10': <число или None>
      }
    """
    iq = _src_iqair()
    om = _src_openmeteo()
    return merge_air_sources(iq, om)

# ──────── лёгкий тест:  python -m air ────────────────────────────
if __name__ == "__main__":
    from pprint import pprint
    pprint(get_air())
