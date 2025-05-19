#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
air.py
~~~~~~

• Два открытых источника качества воздуха
  1) IQAir / nearest_city  (нужен AIRVISUAL_KEY, но может быть None)
  2) Open-Meteo Air-Quality (без ключа)

• merge_air_sources()  ⇒ объединяет словари, приоритет IQAir → Open-Meteo
• get_air()            ⇒ dict {'lvl','aqi','pm25','pm10'}
• get_sst()            ⇒ текущая температура поверхности моря (Sea Surface Temp)
• get_kp()             ⇒ текущий индекс геомагнитной активности Kp и его состояние
"""

from __future__ import annotations
import os
import logging
from typing import Dict, Any, Optional, Tuple

from utils import _get

# ────────── координаты Лимассола ───────────────────────────────────
LAT, LON = 34.707, 33.022

# ────────── API-ключи ───────────────────────────────────────────────
AIR_KEY = os.getenv("AIRVISUAL_KEY")  # IQAir, при отсутствии – None

# ────────── хелперы ─────────────────────────────────────────────────
def _aqi_level(aqi: float | int | str) -> str:
    """Переводит числовой AQI в текстовый уровень."""
    if aqi in ("н/д", None):
        return "н/д"
    aqi = float(aqi)
    if aqi <=  50:
        return "хороший"
    if aqi <= 100:
        return "умеренный"
    if aqi <= 150:
        return "вредный"
    if aqi <= 200:
        return "оч. вредный"
    return "опасный"

def _kp_state(kp: float) -> str:
    """Дает текстовое состояние по значению Kp."""
    if kp < 3:
        return "спокойно"
    if kp < 5:
        return "неспокойно"
    return "буря"

# ────────── источники AQ ────────────────────────────────────────────
def _src_iqair() -> Optional[Dict[str, Any]]:
    """IQAir nearest_city — возвращает dict или None."""
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
            "pm25": pol.get("p2",    None),
            "pm10": pol.get("p1",    None),
        }
    except Exception as e:
        logging.warning("IQAir source error: %s", e)
        return None


def _src_openmeteo() -> Optional[Dict[str, Any]]:
    """Open-Meteo Air-Quality — возвращает dict или None."""
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

# ────────── слияние источников ────────────────────────────────────
def merge_air_sources(
    src1: Optional[Dict[str,Any]],
    src2: Optional[Dict[str,Any]]
) -> Dict[str, Any]:
    """
    Приоритет src1; дополняем тем, чего в нём нет или что = None/"н/д".
    Всегда возвращает dict с ключами 'aqi','pm25','pm10','lvl'.
    """
    base = {"aqi": "н/д", "pm25": None, "pm10": None}
    for k in ("aqi","pm25","pm10"):  # type: ignore
        v1 = src1.get(k) if src1 else None
        if v1 not in (None, "н/д"):
            base[k] = v1
        else:
            v2 = src2.get(k) if src2 else None
            base[k] = v2 if v2 not in (None, "н/д") else base[k]
    base["lvl"] = _aqi_level(base["aqi"])
    return base

# ────────── публичные API ──────────────────────────────────────────
def get_air() -> Dict[str, Any]:
    """
    Возвращает текущее качество воздуха:
      {
        'lvl':  <уровень, строка>,
        'aqi':  <число или "н/д">,
        'pm25': <число или None>,
        'pm10': <число или None>
      }
    """
    iq = _src_iqair()
    om = _src_openmeteo()
    return merge_air_sources(iq, om)


def get_sst() -> Optional[float]:
    """
    Возвращает текущую температуру поверхности моря (Mediterranean) в точке LAT, LON,
    либо None, если данные недоступны.
    """
    j = _get(
        "https://api.open-meteo.com/v1/forecast",
        latitude=LAT, longitude=LON,
        hourly="soil_temperature_0cm",
        timezone="UTC",
        cell_selection="sea",
    )
    if not j or "hourly" not in j:
        return None
    try:
        arr = j["hourly"].get("soil_temperature_0cm", [])
        val = arr[0] if arr else None
        return float(val) if isinstance(val, (int, float)) else None
    except Exception as e:
        logging.warning("SST source error: %s", e)
        return None


def get_kp() -> Tuple[Optional[float], str]:
    """
    Возвращает текущий индекс Kp и его состояние:
      (kp_value: float | None, state: str)
    """
    j = _get(
        "https://services.swpc.noaa.gov/products/geospace/geospace-real-time.json"
    )
    if not j or not isinstance(j, list):
        return None, "н/д"
    try:
        # Пропустим заголовок, если он есть
        first = j[1] if isinstance(j[0][0], str) else j[0]
        raw = first[-1]
        kp = float(raw)
        return kp, _kp_state(kp)
    except Exception as e:
        logging.warning("Kp source error: %s", e)
        return None, "н/д"

# ────────── тестирование при запуске ───────────────────────────────
if __name__ == "__main__":
    from pprint import pprint
    print("Air:", end=" "); pprint(get_air())
    print("SST:", get_sst())
    print("Kp: ", get_kp())
