#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
air.py
~~~~~~

KLD-подобный интерфейс:

- get_air(lat=None, lon=None) -> {"lvl","aqi","pm25","pm10"} (+ служебные: "src","src_emoji","src_icon")
- get_sst(lat, lon) -> float|None

Дополнительно (как у тебя):
- get_kp() -> (kp, state, ts_unix, src)
- get_solar_wind() -> {'bz','bt','speed_kms','density','ts','status','src'}

Все сетевые функции безопасны: на ошибки/таймауты возвращают None/пустые значения без исключений.
"""

from __future__ import annotations
import os, time, json, math, logging
from pathlib import Path
from typing import Any, Dict, Optional, Tuple, Union, List

import pendulum

from utils import _get  # лёгкая HTTP-обёртка (requests.get)

__all__ = ("get_air", "get_sst", "get_kp", "get_solar_wind")

# ───────────────────────── Константы / лог / кэш ─────────────────────────

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

AIR_KEY = os.getenv("AIRVISUAL_KEY")
REQUEST_TIMEOUT = float(os.getenv("HTTP_TIMEOUT", "10"))

CACHE_DIR = Path.home() / ".cache" / "vaybometer"
CACHE_DIR.mkdir(parents=True, exist_ok=True)

# Kp cache (2 часа TTL, жёсткий максимум — 4 часа)
KP_CACHE = CACHE_DIR / "kp.json"
KP_TTL_SEC = 120 * 60
KP_HARD_MAX_AGE_SEC = 4 * 3600

# Солнечный ветер — кэш 10 мин
SW_CACHE = CACHE_DIR / "solar_wind.json"
SW_TTL_SEC = 10 * 60

KP_URLS = [
    "https://services.swpc.noaa.gov/products/noaa-planetary-k-index.json",   # табличный 3-часовой
    "https://services.swpc.noaa.gov/json/planetary_k_index_1m.json",         # резервный 1m
]

# 5-минутные продукты DSCOVR/ACE
SWP_MAG_5M = "https://services.swpc.noaa.gov/products/solar-wind/mag-5-minute.json"
SWP_PLA_5M = "https://services.swpc.noaa.gov/products/solar-wind/plasma-5-minute.json"

SRC_EMOJI = {"iqair": "📡", "openmeteo": "🛰", "n/d": "⚪"}
SRC_ICON  = {"iqair": "📡 IQAir", "openmeteo": "🛰 OM", "n/d": "⚪ н/д"}

# ───────────────────── Безопасная HTTP-обёртка (JSON) ─────────────────────

def _safe_http_get(url: str, **kwargs) -> Optional[Any]:
    """
    Всегда возвращает распарсенный JSON (dict/list) или None.

    Поддерживает как вызов с params=..., так и свободные именованные аргументы
    (latitude=..., longitude=..., ...) — они будут свёрнуты в params.
    """
    params = kwargs.pop("params", None)
    if params is None:
        # сворачиваем прочие именованные аргументы в query params
        params = dict(kwargs)
        kwargs = {}
    try:
        try:
            resp = _get(url, params=params, timeout=REQUEST_TIMEOUT, **kwargs)
        except TypeError:
            # если utils._get не принимает timeout/**kwargs
            resp = _get(url, params=params)
        if hasattr(resp, "json"):
            try:
                return resp.json()
            except Exception:
                return None
        # на случай, если utils._get уже возвращает JSON
        return resp if isinstance(resp, (dict, list)) else None
    except Exception as e:
        logging.warning("_safe_http_get — HTTP error: %s", e)
        return None

# ───────────────────────── Утилиты AQI ──────────────────────────

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
    if not arr_time or not arr_val or len(arr_time) != len(arr_val):
        return None
    try:
        now_iso = time.strftime("%Y-%m-%dT%H:00", time.gmtime())
        idxs = [i for i, t in enumerate(arr_time) if isinstance(t, str) and t <= now_iso]
        idx = max(idxs) if idxs else 0
        v = arr_val[idx]
        v = float(v)
        return v if (math.isfinite(v) and v >= 0) else None
    except Exception:
        return None

# ───────────────────── Источники качества воздуха ─────────────────────

def _src_iqair(lat: float, lon: float) -> Optional[Dict[str, Any]]:
    if not AIR_KEY:
        logging.warning("IQAir: не задан AIRVISUAL_KEY — переключаюсь на Open-Meteo AQ.")
        return None
    j = _safe_http_get(
        "https://api.airvisual.com/v2/nearest_city",
        lat=lat, lon=lon, key=AIR_KEY,
    )
    if not isinstance(j, dict) or "data" not in j:
        return None
    try:
        pol = (j.get("data", {}) or {}).get("current", {}).get("pollution", {}) or {}
        aqi_val  = pol.get("aqius")
        # В публичном API PM чаще недоступны — вернутся None (это ок).
        pm25_val = pol.get("p2")
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
    if not isinstance(j, dict) or "hourly" not in j:
        return None
    try:
        h = j["hourly"]
        times   = h.get("time", []) or []
        aqi_val = _pick_nearest_hour(times, h.get("us_aqi", []) or [])
        pm25    = _pick_nearest_hour(times, h.get("pm2_5", [])  or [])
        pm10    = _pick_nearest_hour(times, h.get("pm10", [])   or [])
        aqi_norm: Union[float, str] = float(aqi_val) if isinstance(aqi_val, (int, float)) and math.isfinite(aqi_val) and aqi_val >= 0 else "н/д"
        pm25_norm = float(pm25) if isinstance(pm25, (int, float)) and math.isfinite(pm25) and pm25 >= 0 else None
        pm10_norm = float(pm10) if isinstance(pm10, (int, float)) and math.isfinite(pm10) and pm10 >= 0 else None
        return {"aqi": aqi_norm, "pm25": pm25_norm, "pm10": pm10_norm, "src": "openmeteo"}
    except Exception as e:
        logging.warning("Open-Meteo AQ parse error: %s", e)
        return None

def merge_air_sources(src1: Optional[Dict[str, Any]], src2: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Приоритет src1 → src2. Возвращает KLD-подобный словарь.
    """
    aqi_val: Union[float, str, None] = "н/д"
    src_tag: str = "n/d"

    for s in (src1, src2):
        if not s:
            continue
        v = s.get("aqi")
        if isinstance(v, (int, float)) and math.isfinite(v) and v >= 0:
            aqi_val = float(v)
            src_tag = s.get("src") or src_tag
            break

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
    return {
        "lvl": lvl,
        "aqi": aqi_val,
        "pm25": pm25,
        "pm10": pm10,
        "src": src_tag,
        "src_emoji": SRC_EMOJI.get(src_tag, "⚪"),
        "src_icon":  SRC_ICON.get(src_tag,  "⚪ н/д"),
    }

def get_air(lat: Optional[float] = None, lon: Optional[float] = None) -> Dict[str, Any]:
    """
    KLD-контракт: lat/lon необязательны. Если не заданы — используем Лимассол.
    """
    if lat is None or lon is None:
        # дефолт — Лимассол
        lat, lon = 34.707, 33.022
    try:
        s1 = _src_iqair(lat, lon)
    except Exception:
        s1 = None
    try:
        s2 = _src_openmeteo(lat, lon)
    except Exception:
        s2 = None
    return merge_air_sources(s1, s2)

# ───────────────────────── SST (по ближайшему часу) ─────────────────

def get_sst(lat: float, lon: float) -> Optional[float]:
    j = _safe_http_get(
        "https://marine-api.open-meteo.com/v1/marine",
        latitude=lat, longitude=lon,
        hourly="sea_surface_temperature", timezone="UTC",
    )
    if not isinstance(j, dict) or "hourly" not in j:
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

# ───────────────────────── Kp + кэш (TTL 120 мин) ───────────────────

def _load_kp_cache() -> tuple[Optional[float], Optional[int], Optional[str]]:
    try:
        data = json.loads(KP_CACHE.read_text(encoding="utf-8"))
        return data.get("kp"), data.get("ts"), data.get("src")
    except Exception:
        return None, None, None

def _save_kp_cache(kp: float, ts: int, src: str) -> None:
    try:
        KP_CACHE.write_text(json.dumps({"kp": kp, "ts": int(ts), "src": src}, ensure_ascii=False), encoding="utf-8")
    except Exception as e:
        logging.warning("Kp cache write error: %s", e)

def _fetch_kp_data(url: str, attempts: int = 3, backoff: float = 2.0) -> Optional[Any]:
    data = None
    for i in range(attempts):
        data = _safe_http_get(url)
        if data:
            break
        try: time.sleep(backoff ** i)
        except Exception: pass
    return data

def _parse_kp_from_table(data: Any) -> tuple[Optional[float], Optional[int]]:
    # products/noaa-planetary-k-index.json
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
    # json/planetary_k_index_1m.json
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

def _kp_state(kp: float) -> str:
    if kp < 3.0: return "спокойно"
    if kp < 5.0: return "неспокойно"
    return "буря"

def get_kp() -> tuple[Optional[float], str, Optional[int], str]:
    """
    Возвращает (kp_value, state, ts_unix, src_tag)
    src_tag ∈ {"swpc_table","swpc_1m","cache","n/d"}
    """
    now_ts = int(time.time())

    data = _fetch_kp_data(KP_URLS[0])
    if data:
        kp, ts = _parse_kp_from_table(data)
        if isinstance(kp, (int, float)) and isinstance(ts, int):
            _save_kp_cache(kp, ts, "swpc_table")
            return kp, _kp_state(kp), ts, "swpc_table"

    data = _fetch_kp_data(KP_URLS[1])
    if data:
        kp, ts = _parse_kp_from_dicts(data)
        if isinstance(kp, (int, float)) and isinstance(ts, int):
            _save_kp_cache(kp, ts, "swpc_1m")
            return kp, _kp_state(kp), ts, "swpc_1m"

    c_kp, c_ts, c_src = _load_kp_cache()
    if isinstance(c_kp, (int, float)) and isinstance(c_ts, int):
        age = now_ts - c_ts
        if age <= KP_TTL_SEC:
            return c_kp, _kp_state(c_kp), c_ts, (c_src or "cache")
        if age <= KP_HARD_MAX_AGE_SEC:
            return c_kp, _kp_state(c_kp), c_ts, (c_src or "cache")

    return None, "н/д", None, "n/d"

# ─────────────────────── Солнечный ветер (5-мин) ───────────────────────

def _load_sw_cache() -> Optional[Dict[str, Any]]:
    try:
        return json.loads(SW_CACHE.read_text(encoding="utf-8"))
    except Exception:
        return None

def _save_sw_cache(obj: Dict[str, Any]) -> None:
    try:
        SW_CACHE.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8"))
    except Exception as e:
        logging.warning("SW cache write error: %s", e)

def _parse_table_latest(rowset: Any, want: List[str]) -> tuple[Optional[Dict[str, float]], Optional[int]]:
    """
    Табличные продукты SWPC: первая строка — заголовок. Возвращает {col:value}, ts.
    """
    try:
        if not isinstance(rowset, list) or len(rowset) < 2 or not isinstance(rowset[0], list):
            return None, None
        header = rowset[0]
        idx = {name: header.index(name) for name in want if name in header}
        for row in reversed(rowset[1:]):
            if not isinstance(row, list) or len(row) < len(header):
                continue
            tstr = row[idx.get("time_tag")] if "time_tag" in idx else row[0]
            try:
                dt = pendulum.parse(str(tstr).replace(" ", "T"), tz="UTC")
                ts = int(dt.int_timestamp)
            except Exception:
                ts = int(time.time())
            values: Dict[str, float] = {}
            ok = False
            for col in want:
                if col == "time_tag":
                    continue
                j = idx.get(col)
                if j is None or j >= len(row):
                    continue
                try:
                    val = float(str(row[j]).replace(",", "."))
                    if math.isfinite(val):
                        values[col] = val
                        ok = True
                except Exception:
                    continue
            if ok:
                return values, ts
    except Exception:
        pass
    return None, None

def _solar_wind_status(bz: Optional[float], v: Optional[float], n: Optional[float]) -> str:
    flags = 0
    if isinstance(bz, (int, float)):
        if bz < -6: flags += 2
        elif bz < -2: flags += 1
    if isinstance(v, (int, float)):
        if v > 700: flags += 2
        elif v > 600: flags += 1
    if isinstance(n, (int, float)):
        if n > 20: flags += 2
        elif n > 15: flags += 1
    if flags >= 4: return "напряжённо"
    if flags >= 2: return "умеренно"
    return "спокойно"

def get_solar_wind() -> Dict[str, Any]:
    now_ts = int(time.time())

    mag = _safe_http_get(SWP_MAG_5M)
    pla = _safe_http_get(SWP_PLA_5M)

    bz = bt = v = n = None
    ts_list: List[int] = []
    src = "swpc_5m"

    if mag:
        vals, ts = _parse_table_latest(mag, ["time_tag", "bz_gsm", "bt"])
        if vals:
            bz = vals.get("bz_gsm", bz)
            bt = vals.get("bt", bt)
        if ts: ts_list.append(ts)

    if pla:
        vals, ts = _parse_table_latest(pla, ["time_tag", "speed", "density"])
        if vals:
            v = vals.get("speed", v)
            n = vals.get("density", n)
        if ts: ts_list.append(ts)

    if ts_list:
        ts = max(ts_list)
        obj = {"bz": bz, "bt": bt, "speed_kms": v, "density": n, "ts": ts, "status": _solar_wind_status(bz, v, n), "src": src}
        _save_sw_cache(obj)
        return obj

    cached = _load_sw_cache()
    if cached and isinstance(cached.get("ts"), int) and (now_ts - int(cached["ts"]) <= SW_TTL_SEC):
        cached["src"] = "cache"
        return cached

    return {}
