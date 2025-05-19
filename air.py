#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
air.py
~~~~~~

• get_air()  → ВСЕГДА возвращает словарь
      {"lvl", "aqi", "pm25", "pm10"}
  – числовые поля заполняются значениями,
    а при отсутствии данных получают строку «н/д».

• get_sst()  → Sea-Surface-Temperature (Open-Meteo Marine API).
• get_kp()   → планетарный K-index (NOAA).

Пыльца вынесена в pollen.py.
"""

from __future__ import annotations

import os
import logging
from typing import Dict, Any, Optional, Tuple

from utils import _get

# ── координаты (Limassol) ────────────────────────────────────────
LAT, LON = 34.707, 33.022

# ── возможные ключи ──────────────────────────────────────────────
AIR_KEY = os.getenv("AIRVISUAL_KEY")       # IQAir / AirVisual

# ── US-EPA градации AQI → текстовый уровень ──────────────────────
def _aqi_level(aqi: float | int) -> str:
    if aqi <=  50: return "хороший"
    if aqi <= 100: return "умеренный"
    if aqi <= 150: return "вредный"
    if aqi <= 200: return "оч. вредный"
    return "опасный"

# ═══════════════════════ ИСТОЧНИК 1 • IQAir ══════════════════════
def _src_airvisual() -> Dict[str, Any] | None:
    """
    IQAir (требует `AIRVISUAL_KEY`).
    Возвращает {'aqi','pm25','pm10'} либо None.
    """
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
            "aqi":  pol.get("aqius"),
            "pm25": pol.get("p2"),
            "pm10": pol.get("p1"),
        }
    except Exception as e:
        logging.warning("AirVisual parse error: %s", e)
        return None

# ═══════════════════════ ИСТОЧНИК 2 • Open-Meteo AQ ═════════════
def _src_openmeteo() -> Dict[str, Any] | None:
    """
    Бесплатный Open-Meteo Air-Quality.
    Возвращает {'aqi','pm25','pm10'} либо None.
    """
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

# ═════════════════════ СЛИЯНИЕ И НОРМАЛИЗАЦИЯ ═══════════════════
def _merge_air_sources(*srcs: Dict[str, Any] | None) -> Dict[str, Any]:
    """
    Сливает данные по приоритету источников (первый важнее).
    Любое отсутствующее числовое поле → "н/д".
    """
    merged: Dict[str, Any] = {"aqi": None, "pm25": None, "pm10": None}

    for src in srcs:
        if not src:
            continue
        for k in merged:
            if merged[k] is None and src.get(k) not in (None, "-", "—"):
                merged[k] = src[k]

    for k, v in merged.items():
        if v is None:
            merged[k] = "н/д"
    return merged

# ═════════════════════ ПУБЛИЧНАЯ ТОЧКА ВХОДА ════════════════════
def get_air() -> Dict[str, Any]:
    """
    Возвращает словарь:
        {"lvl": str, "aqi": int|str, "pm25": float|str, "pm10": float|str}
    Все поля гарантированы.
    """
    raw = _merge_air_sources(
        _src_airvisual(),    # приоритет 1
        _src_openmeteo(),    # приоритет 2 (всегда без ключа)
    )

    # уровень-текст
    try:
        lvl = _aqi_level(float(raw["aqi"])) if raw["aqi"] != "н/д" else "н/д"
    except Exception:
        lvl = "н/д"

    raw["lvl"] = lvl
    return raw

# ═════════════════════ TEMPERATURE OF SEA SURFACE ════════════════
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

# ═════════════════════ PLANETARY K-INDEX NOAA ════════════════════
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

# ────────────────────────── локальный тест ───────────────────────
if __name__ == "__main__":
    from pprint import pprint
    pprint(get_air())
