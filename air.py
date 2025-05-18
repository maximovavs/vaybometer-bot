#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
air.py
~~~~~~

• get_air()   → всегда    {"lvl", "aqi", "pm25", "pm10"}
• get_pollen() (позднее переедет в pollen.py)
• get_sst()   → Sea-Surface-Temperature   (Open-Meteo Marine API)
• get_kp()    → планетарный K-index NOAA

Все числовые поля, если данных нет, получают строку «н/д».
"""

from __future__ import annotations

import os
import logging
from typing import Dict, Any, Optional, Tuple

from utils import _get

# ─────────────────────────── координаты (Limassol) ──────────────
LAT, LON = 34.707, 33.022

# ─────────────────────────── возможные ключи ─────────────────────
AIR_KEY   = os.getenv("AIRVISUAL_KEY")   # IQAir / AirVisual
AMBEE_KEY = os.getenv("TOMORROW_KEY")    # Tomorrow.io  (пока здесь)

# ─────────────────────────── US-EPA градации → уровень ───────────
def _aqi_level(aqi: float | int) -> str:
    if aqi <=  50: return "хороший"
    if aqi <= 100: return "умеренный"
    if aqi <= 150: return "вредный"
    if aqi <= 200: return "оч. вредный"
    return "опасный"

# ═══════════════════════ источники AQI ═══════════════════════════
def _src_airvisual() -> Dict[str, Any] | None:
    """IQAir (AirVisual).  Вернёт {'aqi','pm25','pm10'} либо None."""
    if not AIR_KEY:
        return None

    j = _get(
        "https://api.airvisual.com/v2/nearest_city",
        lat=LAT, lon=LON, key=AIR_KEY,
    )
    if not j or "data" not in j:
        return None

    try:
        pol = j["data"]["current"]["pollution"]
        return {
            "aqi":  pol.get("aqius"),  # может быть None
            "pm25": pol.get("p2"),
            "pm10": pol.get("p1"),
        }
    except Exception as e:
        logging.warning("AirVisual parse error: %s", e)
        return None


def _src_openmeteo() -> Dict[str, Any] | None:
    """Open-Meteo Air-Quality.  Возвращает {'aqi','pm25','pm10'} либо None."""
    j = _get(
        "https://air-quality-api.open-meteo.com/v1/air-quality",
        latitude=LAT, longitude=LON,
        timezone="UTC",
        hourly="us_aqi,pm2_5,pm10",
    )
    try:
        h = j["hourly"]
        return {
            "aqi":  h["us_aqi"][0],
            "pm25": h["pm2_5"][0],
            "pm10": h["pm10"][0],
        }
    except Exception as e:
        logging.warning("Open-Meteo AQ parse error: %s", e)
        return None

# ═════════════════════ объединение и нормализация ════════════════
def _merge_air_sources(*srcs: Dict[str, Any] | None) -> Dict[str, Any]:
    """Сливает данные нескольких источников по приоритету их порядка."""
    merged: Dict[str, Any] = {"aqi": None, "pm25": None, "pm10": None}

    for src in srcs:
        if not src:
            continue
        for k in merged:
            if merged[k] is None and src.get(k) not in (None, "-", "—"):
                merged[k] = src[k]

    # остающиеся None → «н/д»
    for k, v in merged.items():
        if v is None:
            merged[k] = "н/д"
    return merged

# ═════════════════════ публичная точка входа ═════════════════════
def get_air() -> Dict[str, Any]:
    """
    Всегда возвращает:
      {"lvl": str, "aqi": int|str, "pm25": float|str, "pm10": float|str}
    """
    raw = _merge_air_sources(
        _src_airvisual(),          # приоритет 1
        _src_openmeteo(),          # приоритет 2 (без ключа)
    )

    # текстовый уровень
    try:
        lvl = _aqi_level(float(raw["aqi"])) if raw["aqi"] != "н/д" else "н/д"
    except Exception:
        lvl = "н/д"

    raw["lvl"] = lvl
    return raw

# ═══════════════════════ пыльца (переедет в pollen.py) ═══════════
def get_pollen() -> Optional[dict]:
    if not AMBEE_KEY:
        return None
    d = _get(
        "https://api.tomorrow.io/v4/timelines",
        apikey=AMBEE_KEY,
        location=f"{LAT},{LON}",
        fields="treeIndex,grassIndex,weedIndex",
        timesteps="1d", units="metric",
    )
    try:
        return d["data"]["timelines"][0]["intervals"][0]["values"]
    except Exception as e:
        logging.warning("Pollen fetch error: %s", e)
        return None

# ═══════════════════════ температура воды ════════════════════════
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

# ═══════════════════════ планетарный K-index ═════════════════════
def get_kp() -> Tuple[Optional[float], str]:
    j = _get("https://services.swpc.noaa.gov/products/noaa-planetary-k-index.json")
    try:
        kp_val = float(j[-1][1])
    except Exception:
        return None, "н/д"

    if kp_val < 4:
        state = "спокойный"
    elif kp_val < 5:
        state = "повышенный"
    else:
        state = "буря"
    return kp_val, state

# ─────────────────────────── локальный тест ──────────────────────
if __name__ == "__main__":
    from pprint import pprint
    pprint(get_air())
