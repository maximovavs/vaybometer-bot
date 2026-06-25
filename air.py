#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
air.py
~~~~~~

• Источники качества воздуха:
  1) IQAir / nearest_city  (API key: AIRVISUAL_KEY)
  2) Open-Meteo Air-Quality (без ключа)

• merge_air_sources() — объединяет словари с приоритетом IQAir → Open-Meteo
• get_air(lat, lon)      — {'lvl','aqi','pm25','pm10','src','src_emoji','src_icon'}
• get_sst(lat, lon)      — Sea Surface Temperature (по ближайшему часу)
• get_kp()               — (kp, state, ts_unix, src) — индекс Kp с «свежестью»
• get_solar_wind()       — {'bz','bt','speed_kms','density','ts','status','src'}

Особенности:
- Open-Meteo: берём значения по ближайшему прошедшему часу (UTC).
- SST: то же правило ближайшего часа.
- Kp: парсим ПОСЛЕДНЕЕ значение из SWPC; кэш 120 мин, жёсткий максимум 4 ч.
- Солнечный ветер: SWPC 5-минутные продукты (mag/plasma); кэш 10 мин.
- Источник AQI возвращаем как:
    'src' ∈ {'iqair','openmeteo','n/d'},
    'src_emoji' ∈ {'📡','🛰','⚪'},
    'src_icon'  ∈ {'📡 IQAir','🛰 OM','⚪ н/д'}.
"""

from __future__ import annotations
import os
import time
import json
import math
import logging
import re
from html import unescape
from pathlib import Path
from typing import Any, Dict, Optional, Tuple, Union, List

import pendulum
import requests

from utils import _get  # HTTP-обёртка (_get_retry внутри)

__all__ = ("get_air", "get_air_for_cities", "get_sst", "get_kp", "get_solar_wind")

# ───────────────────────── Константы / лог / кеш ─────────────────────────

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

AIR_KEY = os.getenv("AIRVISUAL_KEY")

# Единый сетевой таймаут (сек) — можно переопределить переменной окружения HTTP_TIMEOUT
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
    # Табличный эндпоинт (3-часовой Kp)
    "https://services.swpc.noaa.gov/products/noaa-planetary-k-index.json",
    # Резервный (минутные/почасовые оценки)
    "https://services.swpc.noaa.gov/json/planetary_k_index_1m.json",
]

# 5-минутные продукты DSCOVR/ACE
SWP_MAG_5M = "https://services.swpc.noaa.gov/products/solar-wind/mag-5-minute.json"
SWP_PLA_5M = "https://services.swpc.noaa.gov/products/solar-wind/plasma-5-minute.json"

SRC_EMOJI = {"cy_official": "🇨🇾", "iqair": "📡", "openmeteo": "🛰", "n/d": "⚪"}
SRC_ICON  = {"cy_official": "🇨🇾 AirQuality CY", "iqair": "📡 IQAir", "openmeteo": "🛰 OM", "n/d": "⚪ н/д"}

CY_AIRQUALITY_URLS = (
    "https://www.airquality.gov.cy/",
    "https://www.airquality.dli.mlsi.gov.cy/",
)
CY_AIRQUALITY_TTL_SEC = 10 * 60
_CY_AIRQUALITY_CACHE: tuple[float, list[Dict[str, Any]]] | None = None

_CY_STATION_COORDS = {
    "Nicosia - Traffic Station": (35.170, 33.360),
    "Limassol - Traffic Station": (34.707, 33.022),
    "Larnaca - Traffic Station": (34.916, 33.624),
    "Paphos - Traffic Station": (34.776, 32.424),
    "Paralimni - Traffic Station": (35.037, 33.981),
    "Ayia Marina Xyliatou Background Station": (35.039, 33.057),
    "Zygi Industrial Station": (34.730, 33.340),
}

_CY_CITY_STATION_HINTS = {
    "limassol": ("limassol",),
    "larnaca": ("larnaca",),
    "nicosia": ("nicosia",),
    "pafos": ("paphos",),
    "paphos": ("paphos",),
    "ayia napa": ("paralimni",),
    "agia napa": ("paralimni",),
    "protaras": ("paralimni",),
    "paralimni": ("paralimni",),
}

_POLLUTANT_LABELS = {
    "pm25": "PM₂.₅",
    "pm10": "PM₁₀",
    "no2": "NO₂",
    "o3": "O₃",
    "so2": "SO₂",
    "co": "CO",
}

_CY_LIMITS = {
    "pm25": (15.0, 25.0, 50.0),
    "pm10": (45.0, 50.0, 100.0),
    "no2": (40.0, 100.0, 200.0),
    "o3": (100.0, 140.0, 180.0),
    "so2": (100.0, 250.0, 350.0),
    "co": (4000.0, 7000.0, 15000.0),
}

# ───────────────────────── Безопасная HTTP-обёртка ─────────────────────────

def _safe_http_get(url: str, **kwargs) -> Optional[Dict[str, Any]]:
    """
    Пытается вызвать utils._get с таймаутом. Если у _get нет аргумента timeout,
    повторно вызывает без него. Любые исключения логируются и возвращается None.
    """
    try:
        try:
            return _get(url, timeout=REQUEST_TIMEOUT, **kwargs)
        except TypeError:
            # если твой _get не поддерживает timeout
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
    if v <= 50: return "хороший"
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
        if not isinstance(v, (int, float)):
            return None
        v = float(v)
        return v if (math.isfinite(v) and v >= 0) else None
    except Exception:
        return None

# ───────────────────────── Источники AQI ───────────────────────────

def _float_or_none(value: Any) -> Optional[float]:
    try:
        if value in (None, "", "н/д"):
            return None
        v = float(str(value).replace(",", ".").strip())
        return v if math.isfinite(v) and v >= 0 else None
    except Exception:
        return None


def _cy_pollutant_level(key: str, value: Optional[float]) -> int:
    if value is None:
        return 0
    limits = _CY_LIMITS.get(key)
    if not limits:
        return 0
    if value <= limits[0]:
        return 1
    if value <= limits[1]:
        return 2
    if value <= limits[2]:
        return 3
    return 4


def _cy_dominant_pollutant(data: Dict[str, Any]) -> tuple[Optional[str], int]:
    best_key: Optional[str] = None
    best_level = 0
    best_ratio = -1.0
    for key, limits in _CY_LIMITS.items():
        value = _float_or_none(data.get(key))
        if value is None:
            continue
        level = _cy_pollutant_level(key, value)
        ratio = value / limits[min(max(level, 1), 3) - 1] if level else 0.0
        if level > best_level or (level == best_level and ratio > best_ratio):
            best_key, best_level, best_ratio = key, level, ratio
    return (_POLLUTANT_LABELS.get(best_key, best_key) if best_key else None), best_level


def _cy_official_aqi_from_level(level: int) -> Union[float, str]:
    return {1: 25.0, 2: 75.0, 3: 125.0, 4: 175.0}.get(level, "н/д")


def air_cleanliness_label(air_data: Dict[str, Any]) -> str:
    pollutant = air_data.get("dominant_pollutant")
    level = int(air_data.get("pollution_level") or 0)
    if level <= 1:
        return "🟢 чисто"
    if level == 2:
        return f"🟡 {pollutant}" if pollutant in ("PM₂.₅", "PM₁₀") else "🟡 нормально"
    if level == 3:
        return f"🟠 {pollutant}" if pollutant else "🟠 пыль/умеренно"
    return "🔴 плохо"


def _parse_cy_observed_at(value: str) -> tuple[Optional[str], Optional[int]]:
    if not value:
        return None, None
    raw = value.strip()
    try:
        dt_obj = pendulum.from_format(raw, "DD/MM/YYYY HH:mm", tz="Asia/Nicosia")
        fresh_min = max(0, int((pendulum.now("Asia/Nicosia") - dt_obj).total_minutes()))
        return dt_obj.to_iso8601_string(), fresh_min
    except Exception:
        return raw, None


def _normalize_cy_pollutant(raw: str) -> Optional[str]:
    s = (raw or "").strip().upper()
    s = s.replace("₂", "2").replace("₁", "1").replace("₀", "0")
    s = s.replace("₃", "3").replace("₅", "5").replace(".", "")
    s = re.sub(r"\s+", "", s)
    return {
        "PM25": "pm25",
        "PM10": "pm10",
        "NO2": "no2",
        "O3": "o3",
        "SO2": "so2",
        "CO": "co",
    }.get(s)


def _normalize_cy_station_name(line: str) -> str:
    return re.sub(r"^[#\s]+", "", line or "").strip()


def _parse_cy_airquality_official_html(html_text: str) -> list[Dict[str, Any]]:
    """Parse station measurements embedded in the official AirQuality CY page."""
    if not html_text:
        return []
    text = re.sub(r"(?i)<br\s*/?>", "\n", html_text)
    text = re.sub(r"<[^>]+>", "\n", text)
    text = unescape(text)
    lines = [re.sub(r"\s+", " ", line).strip() for line in text.splitlines()]
    lines = [line for line in lines if line]

    stations: list[Dict[str, Any]] = []
    seen: set[str] = set()
    pollutant_re = re.compile(
        r"^(PM\s*2[.,]?5|PM\s*10|PM₂\.₅|PM₁₀|NO₂|NO2|O₃|O3|SO₂|SO2|CO)\s*[:\-]\s*([0-9]+(?:[.,][0-9]+)?)",
        re.IGNORECASE,
    )
    pollutant_label_re = re.compile(
        r"^(PM\s*2[.,]?5|PM\s*10|PM₂\.₅|PM₁₀|NO₂|NO2|O₃|O3|SO₂|SO2|CO)\s*[:\-]?\s*$",
        re.IGNORECASE,
    )
    value_re = re.compile(r"([0-9]+(?:[.,][0-9]+)?)")
    for idx, line in enumerate(lines):
        station_name = _normalize_cy_station_name(line)
        if "station" not in station_name.lower() or len(station_name) > 90:
            continue
        if station_name.lower().startswith(("updated ", "pollution ")):
            continue

        data: Dict[str, Any] = {"station": station_name, "src": "cy_official"}
        observed_raw = ""
        window = lines[idx + 1: idx + 36]
        pos = 0
        while pos < len(window):
            next_line = window[pos]
            if "station" in next_line.lower() and pollutant_re.match(next_line) is None:
                break
            if next_line.lower().startswith("updated on"):
                observed_raw = next_line.split(":", 1)[-1].strip()
                break
            match = pollutant_re.match(next_line)
            if match:
                key = _normalize_cy_pollutant(match.group(1))
                value = _float_or_none(match.group(2))
                if key and value is not None:
                    data[key] = value
                    pos += 1
                    continue

            label_match = pollutant_label_re.match(next_line)
            if label_match and pos + 1 < len(window):
                key = _normalize_cy_pollutant(label_match.group(1))
                value_match = value_re.search(window[pos + 1])
                value = _float_or_none(value_match.group(1) if value_match else None)
                if key and value is not None:
                    data[key] = value
                    pos += 2
                    continue
            pos += 1

        if not any(key in data for key in _CY_LIMITS):
            continue
        dominant, level = _cy_dominant_pollutant(data)
        data["dominant_pollutant"] = dominant
        data["pollution_level"] = level
        data["aqi"] = _cy_official_aqi_from_level(level)
        data["lvl"] = _aqi_level(data["aqi"])
        data["clean_label"] = air_cleanliness_label(data)
        observed_at, fresh_min = _parse_cy_observed_at(observed_raw)
        data["observed_at"] = observed_at
        data["fresh_min"] = fresh_min
        key = station_name.lower()
        if key not in seen:
            stations.append(data)
            seen.add(key)
    return stations


def _fetch_cy_airquality_official_stations() -> list[Dict[str, Any]]:
    global _CY_AIRQUALITY_CACHE
    now = time.time()
    if _CY_AIRQUALITY_CACHE and now - _CY_AIRQUALITY_CACHE[0] <= CY_AIRQUALITY_TTL_SEC:
        return list(_CY_AIRQUALITY_CACHE[1])
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 VayboMeter/1.0",
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "en,el;q=0.8",
        }
        for url in CY_AIRQUALITY_URLS:
            try:
                resp = requests.get(url, timeout=REQUEST_TIMEOUT, headers=headers)
                resp.raise_for_status()
                stations = _parse_cy_airquality_official_html(resp.text)
                if stations:
                    _CY_AIRQUALITY_CACHE = (now, stations)
                    return stations
            except Exception as e:
                logging.warning("AirQuality CY official fetch error (%s): %s", url, e)
        return []
    except Exception as e:
        logging.warning("AirQuality CY official fetch/parse error: %s", e)
        return []


def _station_distance_km(station: Dict[str, Any], lat: float, lon: float) -> float:
    coords = _CY_STATION_COORDS.get(str(station.get("station") or ""))
    if not coords:
        return 9999.0
    la, lo = coords
    return math.hypot((lat - la) * 111.0, (lon - lo) * 92.0)


def _pick_cy_station(stations: list[Dict[str, Any]], lat: float, lon: float, city: Optional[str] = None) -> Optional[Dict[str, Any]]:
    if not stations:
        return None
    city_key = (city or "").strip().lower()
    hints = _CY_CITY_STATION_HINTS.get(city_key, ())
    if hints:
        for hint in hints:
            for station in stations:
                if hint in str(station.get("station") or "").lower():
                    return station
        return None
    return min(stations, key=lambda station: _station_distance_km(station, lat, lon), default=None)


def _src_cy_airquality_official(lat: float, lon: float, city: Optional[str] = None) -> Optional[Dict[str, Any]]:
    station = _pick_cy_station(_fetch_cy_airquality_official_stations(), lat, lon, city=city)
    if not station:
        return None
    return dict(station)


def _src_iqair(lat: float, lon: float) -> Optional[Dict[str, Any]]:
    if not AIR_KEY:
        return None
    resp = _safe_http_get(
        "https://api.airvisual.com/v2/nearest_city",
        lat=lat, lon=lon, key=AIR_KEY,
    )
    if not resp or "data" not in resp:
        return None
    try:
        pol = (resp.get("data", {}) or {}).get("current", {}).get("pollution", {}) or {}
        aqi_val  = pol.get("aqius")
        # В публичном API обычно нет микрограммов PM, оставляем None если нет
        pm25_val = pol.get("p2")   # если ключа нет — будет None (ок)
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
    resp = _safe_http_get(
        "https://air-quality-api.open-meteo.com/v1/air-quality",
        latitude=lat, longitude=lon,
        hourly="pm10,pm2_5,us_aqi", timezone="UTC",
    )
    if not resp or "hourly" not in resp:
        return None
    try:
        h = resp["hourly"]
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

def merge_air_sources(*sources: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Соединяет данные двух источников AQI (приоритет src1 → src2).
    Возвращает {'lvl','aqi','pm25','pm10','src','src_emoji','src_icon'}.
    """
    aqi_val: Union[float, str, None] = "н/д"
    src_tag: str = "n/d"

    # AQI источник
    for s in sources:
        if not s:
            continue
        v = s.get("aqi")
        if isinstance(v, (int, float)) and math.isfinite(v) and v >= 0:
            aqi_val = float(v)
            src_tag = s.get("src") or src_tag
            break

    values: Dict[str, Any] = {}
    for key in ("pm25", "pm10", "no2", "o3", "so2", "co"):
        for s in sources:
            if not s:
                continue
            value = s.get(key)
            if isinstance(value, (int, float)) and math.isfinite(value):
                values[key] = float(value)
                break

    meta: Dict[str, Any] = {}
    for s in sources:
        if not s:
            continue
        if s.get("src") == src_tag:
            for key in ("station", "observed_at", "fresh_min", "dominant_pollutant", "pollution_level", "clean_label"):
                if s.get(key) is not None:
                    meta[key] = s.get(key)
            break

    lvl = _aqi_level(aqi_val)
    src_emoji = SRC_EMOJI.get(src_tag, SRC_EMOJI["n/d"])
    src_icon  = SRC_ICON.get(src_tag,  SRC_ICON["n/d"])

    out = {
        "lvl": lvl,
        "aqi": aqi_val,
        "pm25": values.get("pm25"),
        "pm10": values.get("pm10"),
        "no2": values.get("no2"),
        "o3": values.get("o3"),
        "so2": values.get("so2"),
        "co": values.get("co"),
        "src": src_tag,
        "src_emoji": src_emoji,
        "src_icon": src_icon,
    }
    out.update(meta)
    if "clean_label" not in out:
        dominant, level = _cy_dominant_pollutant(out)
        out["dominant_pollutant"] = dominant
        out["pollution_level"] = level
        out["clean_label"] = air_cleanliness_label(out)
    return out

def get_air(lat: float, lon: float) -> Dict[str, Any]:
    try:
        src0 = _src_cy_airquality_official(lat, lon)
    except Exception:
        src0 = None
    try:
        src1 = _src_iqair(lat, lon)
    except Exception:
        src1 = None
    try:
        src2 = _src_openmeteo(lat, lon)
    except Exception:
        src2 = None
    return merge_air_sources(src0, src1, src2)


def get_air_for_cities(city_pairs: List[tuple[str, tuple[float, float]]]) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    for city, coords in city_pairs or []:
        try:
            lat, lon = float(coords[0]), float(coords[1])
        except Exception:
            continue
        try:
            official = _src_cy_airquality_official(lat, lon, city=city)
        except Exception:
            official = None
        try:
            iqair = _src_iqair(lat, lon)
        except Exception:
            iqair = None
        try:
            om = _src_openmeteo(lat, lon)
        except Exception:
            om = None
        merged = merge_air_sources(official, iqair, om)
        if merged.get("src") != "n/d" or merged.get("pm25") is not None or merged.get("pm10") is not None:
            out[str(city)] = merged
    return out

# ───────────────────────── SST (по ближайшему часу) ─────────────────

def get_sst(lat: float, lon: float) -> Optional[float]:
    resp = _safe_http_get(
        "https://marine-api.open-meteo.com/v1/marine",
        latitude=lat, longitude=lon,
        hourly="sea_surface_temperature", timezone="UTC",
    )
    if not resp or "hourly" not in resp:
        return None
    try:
        h = resp["hourly"]
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
        data = _safe_http_get(url)
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
        if age <= KP_TTL_SEC:
            return c_kp, _kp_state(c_kp), c_ts, (c_src or "cache")
        if age <= KP_HARD_MAX_AGE_SEC:
            return c_kp, _kp_state(c_kp), c_ts, (c_src or "cache")

    return None, "н/д", None, "n/d"

# ───────────────────────── Солнечный ветер (5-мин) ─────────────────

def _load_sw_cache() -> Optional[Dict[str, Any]]:
    try:
        data = json.loads(SW_CACHE.read_text(encoding="utf-8"))
        return data
    except Exception:
        return None

def _save_sw_cache(obj: Dict[str, Any]) -> None:
    try:
        SW_CACHE.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")
    except Exception as e:
        logging.warning("SW cache write error: %s", e)

def _parse_table_latest(rowset: Any, want: List[str]) -> tuple[Optional[Dict[str, float]], Optional[int]]:
    """
    Универсальный парсер табличных продуктов SWPC:
    Первый элемент — список названий колонок; далее — строки.
    Возвращает словарь {col:value} и ts (UTC) по последней валидной строке.
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
    """
    Примитивная эвристика:
      - опаснее всего Bz < -6 nT
      - скорость > 600 км/с повышенная
      - плотность > 15 см^-3 добавляет «напряжённости»
    """
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
    """
    Возвращает: {'bz','bt','speed_kms','density','ts','status','src'}
    Источник — SWPC 5-minute (mag/plasma). Кэш 10 мин.
    """
    now_ts = int(time.time())

    # 1) читаем оба продукта
    mag = _safe_http_get(SWP_MAG_5M)
    pla = _safe_http_get(SWP_PLA_5M)

    bz = bt = v = n = None
    ts_list: List[int] = []
    src = "swpc_5m"

    # магнетометр: интересны time_tag, bz_gsm, bt
    if mag:
        vals, ts = _parse_table_latest(mag, ["time_tag", "bz_gsm", "bt"])
        if vals:
            bz = vals.get("bz_gsm", bz)
            bt = vals.get("bt", bt)
        if ts: ts_list.append(ts)

    # плазма: speed, density
    if pla:
        vals, ts = _parse_table_latest(pla, ["time_tag", "speed", "density"])
        if vals:
            v = vals.get("speed", v)
            n = vals.get("density", n)
        if ts: ts_list.append(ts)

    if ts_list:
        ts = max(ts_list)
        status = _solar_wind_status(bz, v, n)
        obj = {"bz": bz, "bt": bt, "speed_kms": v, "density": n, "ts": ts, "status": status, "src": src}
        _save_sw_cache(obj)
        return obj

    # 2) кэш (10 мин)
    cached = _load_sw_cache()
    if cached and isinstance(cached.get("ts"), int) and (now_ts - int(cached["ts"]) <= SW_TTL_SEC):
        cached["src"] = "cache"
        return cached

    return {}

# ───────────────────────── CLI ─────────────────────────────────────

if __name__ == "__main__":
    from pprint import pprint
    print("=== Пример get_air (Лимассол) ===")
    pprint(get_air(34.68, 33.04))
    print("\n=== Пример get_sst (Лимассол) ===")
    print(get_sst(34.68, 33.04))
    print("\n=== Пример get_kp ===")
    print(get_kp())
    print("\n=== Пример get_solar_wind ===")
    pprint(get_solar_wind())
