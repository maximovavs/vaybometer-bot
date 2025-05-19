#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
air.py
~~~~~~

• Два бесплатных источника качества воздуха
    1) IQAir “nearest_city”   (нужен AIRVISUAL_KEY)
    2) Open-Meteo Air Quality (без ключа)

• merge_air_sources() объединяет данные, чтобы всегда вернуть
  словарь одинакового вида:
      {"lvl":"умеренный", "aqi":67, "pm25":12.3, "pm10":22.0}

  ▸ если цифры отсутствуют, ставится "н/д" / None  
  ▸ цветной уровень формируется по шкале US-EPA
"""

from __future__ import annotations
from typing import Any, Dict, Optional, Tuple
import os, logging

from utils import _get, _get_retry, aqi_color

# ── координаты (Limassol) ─────────────────────────────────────────
LAT, LON = 34.707, 33.022

# ── ключи (может не быть) ─────────────────────────────────────────
AIR_KEY = os.getenv("AIRVISUAL_KEY")      # IQAir key

# ── шкала AQI → текст ────────────────────────────────────────────
def _aqi_level(aqi: float | int) -> str:
    a = float(aqi)
    if a <=  50: return "хороший"
    if a <= 100: return "умеренный"
    if a <= 150: return "вредный"
    if a <= 200: return "оч. вредный"
    return "опасный"

# ── 1) IQAir ------------------------------------------------------
def _fetch_iqair() -> Dict[str, Any]:
    if not AIR_KEY:
        return {}
    j = _get_retry(
        "https://api.airvisual.com/v2/nearest_city",
        lat=LAT, lon=LON, key=AIR_KEY, retries=2
    )
    try:
        pol   = j["data"]["current"]["pollution"]
        aqi   = pol.get("aqius")
        pm25  = pol.get("p2")
        pm10  = pol.get("p1")
        return {"aqi": aqi, "pm25": pm25, "pm10": pm10}
    except Exception as e:
        logging.warning("IQAir parse error: %s", e)
        return {}

# ── 2) Open-Meteo Air-Quality ------------------------------------
def _fetch_openmeteo() -> Dict[str, Any]:
    j = _get_retry(
        "https://air-quality-api.open-meteo.com/v1/air-quality",
        latitude=LAT, longitude=LON,
        hourly="us_aqi,pm10,pm2_5",
        timezone="UTC",
        retries=2,
    )
    try:
        hh = j["hourly"]
        aqi  = hh["us_aqi"][0]
        pm10 = hh["pm10"][0]
        pm25 = hh["pm2_5"][0]
        # API иногда возвращает None → приводим
        return {"aqi": aqi, "pm25": pm25, "pm10": pm10}
    except Exception as e:
        logging.warning("Open-Meteo air parse error: %s", e)
        return {}

# ── объединитель --------------------------------------------------
def merge_air_sources(*sources: Dict[str, Any]) -> Dict[str, Any]:
    """
    Склеивает несколько словарей, выбирая первую доступную цифру.
    На выходе гарантирован : lvl, aqi, pm25, pm10
    """
    out: Dict[str, Any] = {"aqi": "н/д", "pm25": None, "pm10": None}

    for src in sources:
        for k in ("aqi", "pm25", "pm10"):
            if out[k] in ("н/д", None) and src.get(k) not in (None, "n/a"):
                out[k] = src[k]

    # уровень / цвет
    if out["aqi"] != "н/д":
        out["lvl"] = _aqi_level(out["aqi"])
    else:
        out["lvl"] = "н/д"

    return out

# ── публичная функция --------------------------------------------
def get_air() -> Dict[str, Any]:
    """
    Всегда возвращает полный словарь:
        {"lvl","aqi","pm25","pm10"}
    """
    iq   = _fetch_iqair()
    om   = _fetch_openmeteo()
    data = merge_air_sources(iq, om)

    # финальный цвет-эмодзи можно получить так:
    data["color"] = aqi_color(data["aqi"])
    return data

# ── K-index, SST оставляем как были -------------------------------
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


def get_sst() -> Optional[float]:
    j = _get(
        "https://marine-api.open-meteo.com/v1/marine",
        latitude=LAT, longitude=LON,
        hourly="sea_surface_temperature",
        timezone="UTC",
    )
    try:
        return round(j["hourly"]["sea_surface_temperature"][0], 1)
    except Exception:
        return None


# ── быстрый тест: python -m air -----------------------------------
if __name__ == "__main__":
    from pprint import pprint
    pprint(get_air())
