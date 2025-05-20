#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
air.py
~~~~~~

• Два источника качества воздуха:
  1) IQAir / nearest_city  (API key AIRVISUAL_KEY, опционально)
  2) Open-Meteo Air-Quality (без ключа)

• merge_air_sources() — объединяет словари, приоритет IQAir → Open-Meteo
• get_air() — возвращает dict {'lvl','aqi','pm25','pm10'}
• get_sst() — текущая температура поверхности моря (SST)
• get_kp() — текущий индекс геомагнитной активности Kp с retry и кешем
"""
from __future__ import annotations
import os
import logging
import time
import json
from pathlib import Path
from typing import Dict, Any, Optional, Tuple

from utils import _get

# ────────── Константы ───────────────────────────────────────────────
LAT, LON = 34.707, 33.022  # Limassol
AIR_KEY = os.getenv("AIRVISUAL_KEY")

# Путь для кеша Kp
CACHE_DIR = Path.home() / ".cache" / "vaybometer"
CACHE_DIR.mkdir(parents=True, exist_ok=True)
KP_CACHE = CACHE_DIR / "kp.json"

# URL-источники Kp-индекса
KP_URLS = [
    "https://services.swpc.noaa.gov/products/geospace/geospace-real-time.json",
    "https://services.swpc.noaa.gov/json/planetary_k_index_1m.json"
]

# ────────── Хелперы ─────────────────────────────────────────────────

def _aqi_level(aqi: float | int | str) -> str:
    if aqi in ("н/д", None):
        return "н/д"
    aqi = float(aqi)
    if aqi <= 50:
        return "хороший"
    if aqi <= 100:
        return "умеренный"
    if aqi <= 150:
        return "вредный"
    if aqi <= 200:
        return "оч. вредный"
    return "опасный"


def _kp_state(kp: float) -> str:
    if kp < 3:
        return "спокойно"
    if kp < 5:
        return "неспокойно"
    return "буря"

# ────────── Источники AQ ────────────────────────────────────────────

def _src_iqair() -> Optional[Dict[str, Any]]:
    if not AIR_KEY:
        return None
    data = _get("https://api.airvisual.com/v2/nearest_city", lat=LAT, lon=LON, key=AIR_KEY)
    if not data or "data" not in data:
        return None
    try:
        p = data["data"]["current"]["pollution"]
        return {"aqi": p.get("aqius", "н/д"),
                "pm25": p.get("p2", None),
                "pm10": p.get("p1", None)}
    except Exception as e:
        logging.warning("IQAir parse error: %s", e)
        return None


def _src_openmeteo() -> Optional[Dict[str, Any]]:
    data = _get(
        "https://air-quality-api.open-meteo.com/v1/air-quality",
        latitude=LAT, longitude=LON,
        hourly="pm10,pm2_5,us_aqi", timezone="UTC"
    )
    if not data or "hourly" not in data:
        return None
    try:
        h = data["hourly"]
        aqi = h["us_aqi"][0] if h["us_aqi"] else "н/д"
        pm25 = h["pm2_5"][0] if h["pm2_5"] else None
        pm10 = h["pm10"][0] if h["pm10"] else None
        aqi = aqi if isinstance(aqi, (int, float)) and aqi >= 0 else "н/д"
        pm25 = pm25 if isinstance(pm25, (int, float)) and pm25 >= 0 else None
        pm10 = pm10 if isinstance(pm10, (int, float)) and pm10 >= 0 else None
        return {"aqi": aqi, "pm25": pm25, "pm10": pm10}
    except Exception as e:
        logging.warning("Open-Meteo AQ parse error: %s", e)
        return None


def merge_air_sources(src1: Optional[Dict[str, Any]], src2: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    base = {"aqi": "н/д", "pm25": None, "pm10": None}
    for k in ("aqi", "pm25", "pm10"):
        v1 = src1.get(k) if src1 else None
        if v1 not in (None, "н/д"):
            base[k] = v1
        else:
            v2 = src2.get(k) if src2 else None
            base[k] = v2 if v2 not in (None, "н/д") else base[k]
    base["lvl"] = _aqi_level(base["aqi"])
    return base


def get_air() -> Dict[str, Any]:
    return merge_air_sources(_src_iqair(), _src_openmeteo())

# ────────── SST (Sea Surface Temp) ─────────────────────────────────
def get_sst() -> Optional[float]:
    data = _get(
        "https://api.open-meteo.com/v1/forecast",
        latitude=LAT, longitude=LON,
        hourly="soil_temperature_0cm", timezone="UTC",
        cell_selection="sea",
    )
    if not data or "hourly" not in data:
        return None
    try:
        arr = data["hourly"].get("soil_temperature_0cm", [])
        val = arr[0] if arr else None
        return float(val) if isinstance(val, (int, float)) else None
    except Exception as e:
        logging.warning("SST parse error: %s", e)
        return None

# ────────── Kp-индекс с retry и кешем ──────────────────────────────
def _load_kp_cache() -> Tuple[Optional[float], Optional[float]]:
    """Возвращает (kp, ts) из кеша или (None, None)"""
    try:
        j = json.loads(KP_CACHE.read_text())
        return j.get("kp"), j.get("ts")
    except Exception:
        return None, None

def _save_kp_cache(kp: float) -> None:
    KP_CACHE.write_text(json.dumps({"kp": kp, "ts": int(time.time())}, ensure_ascii=False))

def _fetch_kp_data(url: str, attempts: int = 3, backoff: float = 2.0) -> Optional[Any]:
    for i in range(attempts):
        data = _get(url)
        if data:
            return data
        time.sleep(backoff ** i)
    return None

def get_kp() -> Tuple[Optional[float], str]:
    kp_val: Optional[float] = None
    for url in KP_URLS:
        data = _fetch_kp_data(url)
        if not data:
            continue
        try:
            raw = None
            if isinstance(data, list):
                entry = data[1] if isinstance(data[0][0], str) else data[0]
                raw = entry[-1]
            else:
                raw = data.get("kp_index") or data.get("Kp")
            kp_val = float(raw)
            _save_kp_cache(kp_val)
            return kp_val, _kp_state(kp_val)
        except Exception as e:
            logging.warning("Kp parse error %s: %s", url, e)
    # фоллбэк на кеш
    cached_kp, ts = _load_kp_cache()
    if cached_kp is not None:
        return cached_kp, _kp_state(cached_kp)
    return None, "н/д"

# ────────── standalone тест ─────────────────────────────────────────
if __name__ == "__main__":
    from pprint import pprint
    print("Air:", end=" "); pprint(get_air())
    print("SST:", get_sst())
    print("Kp:", get_kp())
