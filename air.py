#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
air.py
~~~~~~

• Два открытых источника качества воздуха
  1) IQAir / nearest_city  (нужен AIRVISUAL_KEY, но можно быть None)
  2) Open-Meteo Air-Quality (без ключа)

• merge_air_sources()  ⇒ объединяет словари, приоритет IQAir-->Open-Meteo
• get_air()            ⇒ всегда {'lvl','aqi','pm25','pm10'}  (с «н/д»)
"""

from __future__ import annotations
import os, logging
from typing import Dict, Any, Optional

from utils import _get, aqi_color   # aqi_color уже есть в utils.py

# ────────── координаты (Limassol) ────────────────────────────────
LAT, LON = 34.707, 33.022

# ────────── API-ключи ────────────────────────────────────────────
AIR_KEY = os.getenv("AIRVISUAL_KEY")        # может быть None

# ────────── helpers ──────────────────────────────────────────────
def _aqi_level(aqi: float | int) -> str:
    if aqi == "н/д":        return "н/д"
    aqi = float(aqi)
    if aqi <=  50: return "хороший"
    if aqi <= 100: return "умеренный"
    if aqi <= 150: return "вредный"
    if aqi <= 200: return "оч. вредный"
    return "опасный"

# ────────── источники ────────────────────────────────────────────
def _src_iqair() -> Dict[str, Any] | None:
    """IQAir nearest_city — возвращает словарь или None."""
    if not AIR_KEY:
        return None
    j = _get(
        "https://api.airvisual.com/v2/nearest_city",
        lat=LAT, lon=LON, key=AIR_KEY
    )
    try:
        pol = j["data"]["current"]["pollution"]
        return {
            "aqi":   pol.get("aqius", "н/д"),
            "pm25":  pol.get("p2"   , None),
            "pm10":  pol.get("p1"   , None),
        }
    except Exception as e:
        logging.warning("IQAir source error: %s", e)
        return None


def _src_openmeteo() -> Dict[str, Any] | None:
    """Open-Meteo Air-Quality — возвращает словарь или None."""
    j = _get(
        "https://air-quality-api.open-meteo.com/v1/air-quality",
        latitude=LAT, longitude=LON,
        hourly="pm10,pm2_5,us_aqi", timezone="UTC"
    )
    try:
        aqi  = j["hourly"]["us_aqi"][0]
        pm25 = j["hourly"]["pm2_5"][0]
        pm10 = j["hourly"]["pm10" ][0]
        # иногда -1 для «нет данных»
        aqi  = aqi  if aqi  >= 0 else "н/д"
        pm25 = pm25 if pm25 >= 0 else None
        pm10 = pm10 if pm10 >= 0 else None
        return {"aqi": aqi, "pm25": pm25, "pm10": pm10}
    except Exception as e:
        logging.warning("Open-Meteo AQ source error: %s", e)
        return None


# ────────── слияние ──────────────────────────────────────────────
def merge_air_sources(src1: Dict[str,Any]|None,
                      src2: Dict[str,Any]|None) -> Dict[str,Any]:
    """Приоритет src1; дополняем тем, чего в нём нет / None."""
    base = {"aqi": "н/д", "pm25": None, "pm10": None}
    for k in base:
        base[k] = (
            (src1 or {}).get(k) if (src1 and (src1.get(k) not in (None,"н/д")))
            else (src2 or {}).get(k, base[k])
        )
    base["lvl"] = _aqi_level(base["aqi"])
    return base


# ────────── основной API ─────────────────────────────────────────
def get_air() -> Dict[str,Any]:
    """Публичная точка входа — всегда отдаёт полный словарь."""
    return merge_air_sources(_src_iqair(), _src_openmeteo())


# ──────── лёгкий тест:  python -m air ────────────────────────────
if __name__ == "__main__":          # ← для локальной проверки
    from pprint import pprint
    pprint(get_air())
