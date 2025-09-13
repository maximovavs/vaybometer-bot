#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
air.py (Cyprus)
~~~~~~~~~~~~~~~

• Источники качества воздуха:
  1) IQAir / nearest_city  (API key: AIRVISUAL_KEY)
  2) Open-Meteo Air-Quality (без ключа)

• merge_air_sources() — объединяет словари с приоритетом IQAir → Open-Meteo
• get_air(lat, lon)      — {'lvl','aqi','pm25','pm10','src','src_emoji','src_icon'}
• get_sst(lat, lon)      — Sea Surface Temperature (по ближайшему прошедшему часу, Marine API)
• get_kp()               — (kp, state, ts_unix, src) — индекс Kp со «свежестью»

Совместимость с KLD: сигнатуры и форматы возвращаемых значений совпадают.
"""

from __future__ import annotations
import os
import time
import json
import math
import logging
from pathlib import Path
from typing import Any, Dict, Optional, Tuple, Union, List

import pendulum

from utils import _get  # ожидание: _get(url, **query) -> parsed JSON (dict)

__all__ = ("get_air", "get_sst", "get_kp")

# ───────────────────────── Константы / лог / кеш ─────────────────────────

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

# Дефолт на случай вызовов без координат — Лимассол
LAT_DEF, LON_DEF = 34.707, 33.022
AIR_KEY = os.getenv("AIRVISUAL_KEY")

# Единый сетевой таймаут (сек) — опционально, если _get поддерживает timeout
REQUEST_TIMEOUT = float(os.getenv("HTTP_TIMEOUT", "10"))

CACHE_DIR = Path.home() / ".cache" / "vaybometer"
CACHE_DIR.mkdir(parents=True, exist_ok=True)

# Kp cache (2 часа TTL, жёсткий максимум — 4 часа)
KP_CACHE = CACHE_DIR / "kp.json"
KP_TTL_SEC = 120 * 60
KP_HARD_MAX_AGE_SEC = 4 * 3600

KP_URLS = [
    # Табличный эндпоинт (3-часовой Kp)
    "https://services.swpc.noaa.gov/products/noaa-planetary-k-index.json",
    # Резервный (минутные/почасовые оценки)
    "https://services.swpc.noaa.gov/json/planetary_k_index_1m.json",
]

SRC_EMOJI = {"iqair": "📡", "openmeteo": "🛰", "n/d": "⚪"}
SRC_ICON  = {"iqair": "📡 IQAir", "openmeteo": "🛰 OM", "n/d": "⚪ н/д"}

# ───────────────────────── Безопасная HTTP-обёртка ─────────────────────────

def _safe_http_get(url: str, **kwargs) -> Optional[Dict[str, Any]]:
    """
    Пытается вызвать utils._get с таймаутом. Если у _get нет аргумента timeout,
    повторно вызывает без него. Любые исключения логируются и возвращается None.
    Предполагается, что _get возвращает JSON (dict).
    """
    try:
        try:
            return _get(url, timeout=REQUEST_TIMEOUT, **kwargs)  # type: ignore[call-arg]
        except TypeError:
            return _get(url, **kwargs)
    except Exception as e:
        logging.warning("_safe_http_get — HTTP error: %s", e)
        return None

# ───────────────────────── Утилиты AQI/Kp ──────────────────────────

def _aqi_level(aqi: Union[int, float, str, None]) -> str:
    if aqi in (None, "н/д"):
        return "н/д"
    try:
        v = float(aqi)
    except (TypeError, ValueError):
        return "н/д"
    if v <=  50: return "хороший"
    if v <= 100: return "умеренный"
    if v <= 150: return "вредный"
    if v <= 200: return "оч. вредный"
    return "опасный"

def _pick_nearest_hour(arr_time: List[str], arr_val: List[Any]) -> Optional[float]:
    """
    Берём ближайший прошедший час относительно текущего момента (UTC).
    """
    if not arr_time or not arr_val or len(arr_time) != len(arr_val):
        return None
    try:
        now_iso = time.strftime("%Y-%m-%dT%H:00", time.gmtime())
        idxs = [i for i, t in enumerate(arr_time) if isinstance(t, str) and t <= now_iso]
        idx = max(idxs) if idxs else 0
        v = arr_val[idx]
        if not isinstance(v, (int, float)):
            return None
        v = float(v)
        return v if (math.isfinite(v) and v >= 0) else None
    except Exception:
        return None

def _kp_state(kp: float) -> str:
    if kp < 3.0: return "спокойно"
    if kp < 5.0: return "неспокойно"
    return "буря"

# ───────────────────────── Источники AQI ───────────────────────────

def _src_iqair(lat: float, lon: float) -> Optional[Dict[str, Any]]:
    if not AIR_KEY:
        return None
    j = _safe_http_get(
        "https://api.airvisual.com/v2/nearest_city",
        lat=lat, lon=lon, key=AIR_KEY,
    )
    if not j or "data" not in j:
        return None
    try:
        pol = (j.get("data", {}) or {}).get("current", {}).get("pollution", {}) or {}
        aqi_val  = pol.get("aqius")
        pm25_val = pol.get("p2")   # может отсутствовать
        pm10_val = pol.get("p1")
        return {
            "aqi":  float(aqi_val)  if isinstance(aqi_val,  (int, float)) else None,
            "pm25": float(pm25_val) if isinstance(pm25_val, (int, float)) else None,
            "pm10": float(pm10_val) if isinstance(pm10_val, (int, float)) else None,
            "src": "iqair",
        }
    except Exception as e:
        logging.warning("IQAir parse error: %s", e)
        return None

def _src_openmeteo(lat: float, lon: float) -> Optional[Dict[str, Any]]:
    j = _safe_http_get(
        "https://air-quality-api.open-meteo.com/v1/air-quality",
        latitude=lat, longitude=lon,
        hourly="pm10,pm2_5,us_aqi", timezone="UTC",
    )
    if not j or "hourly" not in j:
        return None
    try:
        h = j["hourly"]
        times = h.get("time", []) or []
        aqi_val  = _pick_nearest_hour(times, h.get("us_aqi", []) or [])
        pm25_val = _pick_nearest_hour(times, h.get("pm2_5", []) or [])
        pm10_val = _pick_nearest_hour(times, h.get("pm10", [])  or [])
        aqi_norm: Union[float, str] = float(aqi_val)  if isinstance(aqi_val,  (int, float)) and math.isfinite(aqi_val)  and aqi_val  >= 0 else "н/д"
        pm25_norm = float(pm25_val) if isinstance(pm25_val, (int, float)) and math.isfinite(pm25_val) and pm25_val >= 0 else None
        pm10_norm = float(pm10_val) if isinstance(pm10_val, (int, float)) and math.isfinite(pm10_val) and pm10_val >= 0 else None
        return {"aqi": aqi_norm, "pm25": pm25_norm, "pm10": pm10_norm, "src": "openmeteo"}
    except Exception as e:
        logging.warning("Open-Meteo AQ parse error: %s", e)
        return None

# ───────────────────────── Merge AQI ───────────────────────────────

def merge_air_sources(src1: Optional[Dict[str, Any]], src2: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Соединяет данные двух источников AQI (приоритет src1 → src2).
    Возвращает {'lvl','aqi','pm25','pm10','src','src_emoji','src_icon'}.
    """
    aqi_val: Union[float, str, None] = "н/д"
    src_tag: str = "n/d"

    # AQI источник
    for s in (src1, src2):
        if not s:
            continue
        v = s.get("aqi")
        if isinstance(v, (int, float)) and math.isfinite(v) and v >= 0:
            aqi_val = float(v)
            src_tag = s.get("src") or src_tag
            break

    # PM first-non-null
    pm25 = None
    pm10 = None
    for s in (src1, src2):
        if not s:
            continue
        if pm25 is None and isinstance(s.get("pm25"), (int, float)) and math.isfinite(s["pm25"]):
            pm25 = float(s["pm25"])
        if pm10 is None and isinstance(s.get("pm10"), (int, float)) and math.isfinite(s["pm10"]):
            pm10 = float(s["pm10"])

    lvl = _aqi_level(aqi_val)
    src_emoji = SRC_EMOJI.get(src_tag, SRC_EMOJI["n/d"])
    src_icon  = SRC_ICON.get(src_tag,  SRC_ICON["n/d"])

    return {
        "lvl": lvl,
        "aqi": aqi_val,
        "pm25": pm25,
        "pm10": pm10,
        "src": src_tag,
        "src_emoji": src_emoji,
        "src_icon": src_icon,
    }

def get_air(lat: Optional[float] = None, lon: Optional[float] = None) -> Dict[str, Any]:
    """
    Универсально: можно вызывать без координат (дефолт Лимассол) или с lat/lon.
    """
    lat = LAT_DEF if lat is None else float(lat)
    lon = LON_DEF if lon is None else float(lon)
    try:
        src1 = _src_iqair(lat, lon)
    except Exception:
        src1 = None
    try:
        src2 = _src_openmeteo(lat, lon)
    except Exception:
        src2 = None
    return merge_air_sources(src1, src2)

# ───────────────────────── SST (по ближайшему часу) ─────────────────

def get_sst(lat: Optional[float] = None, lon: Optional[float] = None) -> Optional[float]:
    """
    Sea Surface Temperature по ближайшему прошедшему часу (UTC).
    """
    lat = LAT_DEF if lat is None else float(lat)
    lon = LON_DEF if lon is None else float(lon)

    j = _safe_http_get(
        "https://marine-api.open-meteo.com/v1/marine",
        latitude=lat, longitude=lon,
        hourly="sea_surface_temperature", timezone="UTC",
    )
    if not j or "hourly" not in j:
        return None
    try:
        h = j["hourly"]
        times = h.get("time", []) or []
        vals  = h.get("sea_surface_temperature", []) or []
        v = _pick_nearest_hour(times, vals)
        return float(v) if isinstance(v, (int, float)) else None
    except Exception as e:
        logging.warning("Marine SST parse error: %s", e)
        return None

# ───────────────────────── Kp + кеш (TTL 120 мин) ───────────────────

def _load_kp_cache() -> tuple[Optional[float], Optional[int], Optional[str]]:
    try:
        data = json.loads(KP_CACHE.read_text(encoding="utf-8"))
        return data.get("kp"), data.get("ts"), data.get("src")
    except Exception:
        return None, None, None

def _save_kp_cache(kp: float, ts: int, src: str) -> None:
    try:
        KP_CACHE.write_text(
            json.dumps({"kp": kp, "ts": int(ts), "src": src}, ensure_ascii=False),
            encoding="utf-8"
        )
    except Exception as e:
        logging.warning("Kp cache write error: %s", e)

def _fetch_kp_data(url: str, attempts: int = 3, backoff: float = 2.0) -> Optional[Any]:
    data = None
    for i in range(attempts):
        try:
            data = _safe_http_get(url)
        except Exception:
            data = None
        if data:
            break
        try:
            time.sleep(backoff ** i)
        except Exception:
            pass
    return data

def _parse_kp_from_table(data: Any) -> tuple[Optional[float], Optional[int]]:
    """
    products/noaa-planetary-k-index.json
    Формат: [ ["time_tag","kp_index"], ["2025-08-30 09:00:00","2.67"], ... ]
    Берём последнюю строку и преобразуем время в ts (UTC).
    """
    try:
        if not isinstance(data, list) or len(data) < 2 or not isinstance(data[0], list):
            return None, None
        for row in reversed(data[1:]):
            if not isinstance(row, list) or len(row) < 2:
                continue
            tstr = str(row[0]).replace("Z", "").replace("T", " ")
            val  = float(str(row[-1]).replace(",", "."))
            try:
                dt = pendulum.parse(tstr, tz="UTC")  # 'YYYY-MM-DD HH:MM:SS'
                ts = int(dt.int_timestamp)
            except Exception:
                ts = int(time.time())
            return val, ts
    except Exception:
        pass
    return None, None

def _parse_kp_from_dicts(data: Any) -> tuple[Optional[float], Optional[int]]:
    """
    json/planetary_k_index_1m.json
    Формат: [{time_tag:"2025-08-30T10:27:00Z", kp_index:3.0}, ...]
    """
    try:
        if not isinstance(data, list) or not data or not isinstance(data[0], dict):
            return None, None
        for item in reversed(data):
            raw = item.get("kp_index") or item.get("estimated_kp") or item.get("kp")
            tstr = item.get("time_tag") or item.get("time_tag_estimated")
            if raw is None or not tstr:
                continue
            val = float(str(raw).replace(",", "."))
            dt = pendulum.parse(str(tstr).replace(" ", "T"), tz="UTC")
            return val, int(dt.int_timestamp)
    except Exception:
        pass
    return None, None

def get_kp() -> tuple[Optional[float], str, Optional[int], str]:
    """
    Возвращает (kp_value, state, ts_unix, src_tag)
    src_tag ∈ {"swpc_table","swpc_1m","cache","n/d"}
    """
    now_ts = int(time.time())

    # 1) Табличный 3-часовой Kp
    data = _fetch_kp_data(KP_URLS[0])
    if data:
        kp, ts = _parse_kp_from_table(data)
        if isinstance(kp, (int, float)) and isinstance(ts, int):
            _save_kp_cache(kp, ts, "swpc_table")
            return kp, _kp_state(kp), ts, "swpc_table"

    # 2) Резерв — 1m JSON
    data = _fetch_kp_data(KP_URLS[1])
    if data:
        kp, ts = _parse_kp_from_dicts(data)
        if isinstance(kp, (int, float)) and isinstance(ts, int):
            _save_kp_cache(kp, ts, "swpc_1m")
            return kp, _kp_state(kp), ts, "swpc_1m"

    # 3) Кэш, если он не старый
    c_kp, c_ts, c_src = _load_kp_cache()
    if isinstance(c_kp, (int, float)) and isinstance(c_ts, int):
        age = now_ts - c_ts
        if age <= KP_TTL_SEC or age <= KP_HARD_MAX_AGE_SEC:
            return c_kp, _kp_state(c_kp), c_ts, (c_src or "cache")

    return None, "н/д", None, "n/d"


# ────────── CLI-тестирование ────────────────────────────────────────
if __name__ == "__main__":
    from pprint import pprint
    print("Air Limassol:", end=" "); pprint(get_air())
    print("SST Limassol:", get_sst())
    print("Kp:", get_kp())
