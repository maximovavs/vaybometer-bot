#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
weather.py — Open‑Meteo wrapper with caching + fallbacks (VayboMeter).

Goals:
- Single stable entrypoint: get_weather(lat, lon, tz_name=None, cache_ttl_sec=None)
- Resilient HTTP: retries + timeouts, graceful fallback to cached payload
- Normalized schema: {current, hourly, daily} and backward‑compatible aliases:
    * hourly: wind_speed_10m + windspeed_10m, wind_direction_10m + winddirection_10m,
              wind_gusts_10m + windgusts_10m, weather_code + weathercode
    * daily:  weather_code + weathercode
- Time strings are returned in ISO 8601 with timezone offset (safer parsing with pendulum).
  Original Open‑Meteo "local" times are preserved in *_local fields.

Env (optional):
  OPEN_METEO_URL            default: https://api.open-meteo.com/v1/forecast
  WEATHER_TIMEOUT_SEC       default: 12
  WEATHER_RETRIES           default: 2   (additional attempts after the first)
  WEATHER_RETRY_BACKOFF     default: 1.6
  WEATHER_CACHE_TTL_SEC     default: 1800
  WEATHER_TZ_DEFAULT        default: "auto"
  WEATHER_DEBUG             default: 0/1

This module is intentionally dependency-light:
- uses requests if installed, otherwise urllib
- uses pendulum if installed, otherwise keeps times as returned by Open‑Meteo
"""

from __future__ import annotations

import json
import logging
import os
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    import requests  # type: ignore
except Exception:
    requests = None  # type: ignore

try:
    import pendulum  # type: ignore
except Exception:
    pendulum = None  # type: ignore


LOG = logging.getLogger(__name__)
if not LOG.handlers:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")


OPEN_METEO_URL = (os.getenv("OPEN_METEO_URL") or "https://api.open-meteo.com/v1/forecast").strip()

TIMEOUT_SEC = float(os.getenv("WEATHER_TIMEOUT_SEC", "12") or "12")
RETRIES = int(os.getenv("WEATHER_RETRIES", "2") or "2")
BACKOFF = float(os.getenv("WEATHER_RETRY_BACKOFF", "1.6") or "1.6")
CACHE_TTL_SEC = int(os.getenv("WEATHER_CACHE_TTL_SEC", "1800") or "1800")

TZ_DEFAULT = (os.getenv("WEATHER_TZ_DEFAULT") or "auto").strip()
DEBUG = str(os.getenv("WEATHER_DEBUG", "")).strip().lower() in ("1", "true", "yes", "on")


CACHE_DIR = Path(".cache")
CACHE_DIR.mkdir(parents=True, exist_ok=True)


# ---------- Variables (attempted in order: most rich -> safer) ----------
HOURLY_FULL = [
    "temperature_2m",
    "relative_humidity_2m",
    "apparent_temperature",
    "precipitation_probability",
    "rain",
    "showers",
    "snowfall",
    "weather_code",
    "wind_speed_10m",
    "wind_direction_10m",
    "wind_gusts_10m",
    "surface_pressure",
    "uv_index",
    "uv_index_clear_sky",
    "thunderstorm_probability",
]

DAILY_FULL = [
    "temperature_2m_max",
    "temperature_2m_min",
    "weather_code",
    "sunrise",
    "sunset",
    "precipitation_sum",
    "rain_sum",
    "snowfall_sum",
    "precipitation_probability_max",
    "wind_speed_10m_max",
    "wind_gusts_10m_max",
    "wind_direction_10m_dominant",
    "uv_index_max",
    "uv_index_clear_sky_max",
    "relative_humidity_2m_min",
    "relative_humidity_2m_max",
]

# First fallback: drop "thunderstorm_probability" (can be unsupported in some configs).
HOURLY_NO_TSTORM = [v for v in HOURLY_FULL if v != "thunderstorm_probability"]

# Minimal safe set.
HOURLY_MIN = [
    "temperature_2m",
    "relative_humidity_2m",
    "weather_code",
    "wind_speed_10m",
    "wind_direction_10m",
    "wind_gusts_10m",
    "surface_pressure",
    "rain",
    "uv_index",
]

DAILY_MIN = [
    "temperature_2m_max",
    "temperature_2m_min",
    "weather_code",
    "sunrise",
    "sunset",
    "uv_index_max",
    "relative_humidity_2m_min",
    "relative_humidity_2m_max",
]


@dataclass(frozen=True)
class _AttemptSpec:
    hourly: List[str]
    daily: List[str]
    current_mode: str  # "current" or "current_weather"


ATTEMPTS: List[_AttemptSpec] = [
    _AttemptSpec(hourly=HOURLY_FULL, daily=DAILY_FULL, current_mode="current"),
    _AttemptSpec(hourly=HOURLY_NO_TSTORM, daily=DAILY_FULL, current_mode="current"),
    _AttemptSpec(hourly=HOURLY_MIN, daily=DAILY_MIN, current_mode="current"),
    # Compatibility: older "current_weather=true"
    _AttemptSpec(hourly=HOURLY_NO_TSTORM, daily=DAILY_FULL, current_mode="current_weather"),
    _AttemptSpec(hourly=HOURLY_MIN, daily=DAILY_MIN, current_mode="current_weather"),
]


# ---------- Cache helpers ----------
def _norm_coord(x: float) -> str:
    # stable key with 3 decimals (~100m)
    return f"{float(x):.3f}"


def _cache_path(lat: float, lon: float, tz_name: str) -> Path:
    key = f"weather_{_norm_coord(lat)}_{_norm_coord(lon)}_{tz_name.replace('/', '-')}.json"
    return CACHE_DIR / key


def _now_ts() -> int:
    return int(time.time())


def _read_cache(path: Path) -> Optional[Dict[str, Any]]:
    try:
        if not path.exists():
            return None
        obj = json.loads(path.read_text("utf-8"))
        if not isinstance(obj, dict):
            return None
        return obj
    except Exception:
        return None


def _write_cache(path: Path, payload: Dict[str, Any]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def _unwrap_cached(obj: Dict[str, Any]) -> Tuple[Optional[Dict[str, Any]], Optional[int]]:
    """
    Cache schema:
      { "fetched_at": 1700000000, "data": {...} }
    """
    if not isinstance(obj, dict):
        return None, None
    fetched_at = obj.get("fetched_at")
    data = obj.get("data")
    if isinstance(fetched_at, (int, float)) and isinstance(data, dict):
        return data, int(fetched_at)
    # allow raw payload in cache (older)
    if isinstance(obj, dict) and ("hourly" in obj or "daily" in obj):
        return obj, None
    return None, None


# ---------- HTTP layer ----------
def _http_get_json(url: str, timeout_sec: float) -> Dict[str, Any]:
    if requests is not None:
        r = requests.get(url, timeout=timeout_sec, headers={"User-Agent": "VayboMeter/1.0"})
        r.raise_for_status()
        return r.json()
    with urllib.request.urlopen(url, timeout=timeout_sec) as resp:
        raw = resp.read().decode("utf-8")
    return json.loads(raw)


def _build_url(
    lat: float,
    lon: float,
    tz_name: str,
    spec: _AttemptSpec,
    forecast_days: int = 8,
) -> str:
    params: Dict[str, Any] = {
        "latitude": float(lat),
        "longitude": float(lon),
        "timezone": tz_name or TZ_DEFAULT,
        "forecast_days": int(forecast_days),
        "hourly": ",".join(spec.hourly),
        "daily": ",".join(spec.daily),
    }

    if spec.current_mode == "current":
        params["current"] = ",".join(
            [
                "temperature_2m",
                "relative_humidity_2m",
                "apparent_temperature",
                "weather_code",
                "wind_speed_10m",
                "wind_direction_10m",
                "wind_gusts_10m",
                "surface_pressure",
            ]
        )
    else:
        params["current_weather"] = "true"

    qs = urllib.parse.urlencode(params, safe=",:")
    return f"{OPEN_METEO_URL}?{qs}"


# ---------- Normalization ----------
def _localize_time_list(times: List[Any], tz_name: str) -> Tuple[List[str], List[str]]:
    """
    Returns (time_with_offset, time_local_original_str).
    If pendulum is unavailable or tz is 'auto', returns input strings for both.
    """
    local_strs = [str(t) for t in (times or []) if t is not None]
    if not local_strs:
        return [], []
    if pendulum is None:
        return local_strs, local_strs
    if not tz_name or tz_name == "auto":
        return local_strs, local_strs

    out: List[str] = []
    tz = pendulum.timezone(tz_name)
    for s in local_strs:
        try:
            dt = pendulum.parse(s, tz=tz)
            out.append(dt.to_iso8601_string())
        except Exception:
            out.append(s)
    return out, local_strs


def _ensure_aliases(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    Add backward‑compatible key aliases without breaking original payload.
    """
    if not isinstance(payload, dict):
        return payload

    hourly = payload.get("hourly")
    if isinstance(hourly, dict):
        if "weather_code" in hourly and "weathercode" not in hourly:
            hourly["weathercode"] = hourly.get("weather_code")
        if "wind_speed_10m" in hourly and "windspeed_10m" not in hourly:
            hourly["windspeed_10m"] = hourly.get("wind_speed_10m")
        if "wind_direction_10m" in hourly and "winddirection_10m" not in hourly:
            hourly["winddirection_10m"] = hourly.get("wind_direction_10m")
        if "wind_gusts_10m" in hourly and "windgusts_10m" not in hourly:
            hourly["windgusts_10m"] = hourly.get("wind_gusts_10m")

    daily = payload.get("daily")
    if isinstance(daily, dict):
        if "weather_code" in daily and "weathercode" not in daily:
            daily["weathercode"] = daily.get("weather_code")

    cur = payload.get("current")
    curw = payload.get("current_weather")
    if cur is None and isinstance(curw, dict):
        payload["current"] = dict(curw)
    elif isinstance(cur, dict) and curw is None:
        payload["current_weather"] = dict(cur)

    return payload


def _normalize_times(payload: Dict[str, Any], tz_name: str) -> Dict[str, Any]:
    """
    Convert hourly.time to ISO strings with timezone offset (if possible),
    preserving original local time in hourly.time_local.
    """
    if not isinstance(payload, dict):
        return payload

    hourly = payload.get("hourly")
    if isinstance(hourly, dict):
        t = hourly.get("time") or []
        if isinstance(t, list) and t:
            t_off, t_local = _localize_time_list(t, tz_name)
            if t_off:
                hourly["time_local"] = t_local
                hourly["time"] = t_off
    return payload


def _is_error_payload(obj: Any) -> bool:
    if not isinstance(obj, dict):
        return True
    if obj.get("error") is True:
        return True
    if isinstance(obj.get("reason"), str) and obj.get("reason"):
        return True
    if "hourly" not in obj and "daily" not in obj and "current_weather" not in obj and "current" not in obj:
        return True
    return False


# ---------- Public API ----------
def get_weather(
    lat: float,
    lon: float,
    tz_name: Optional[str] = None,
    cache_ttl_sec: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Fetch Open‑Meteo forecast payload for (lat, lon).

    Returns a dict that always tries to contain:
      - "hourly": {"time": [...], ...}
      - "daily":  {"time": [...], ...}
    plus "current"/"current_weather" when available.

    On network errors, returns cached payload if not too stale; otherwise {}.
    """
    tz_name_eff = (tz_name or TZ_DEFAULT or "auto").strip() or "auto"
    ttl = int(cache_ttl_sec) if isinstance(cache_ttl_sec, int) and cache_ttl_sec > 0 else CACHE_TTL_SEC

    cache_path = _cache_path(lat, lon, tz_name_eff)
    cached_obj = _read_cache(cache_path)
    cached_payload, cached_ts = _unwrap_cached(cached_obj) if cached_obj else (None, None)

    # Fast path: fresh cache
    if cached_payload is not None and isinstance(cached_ts, int):
        age = _now_ts() - cached_ts
        if age <= ttl:
            if DEBUG:
                LOG.info("weather: cache hit %s (age=%ss)", cache_path.name, age)
            out = dict(cached_payload)
            out = _ensure_aliases(_normalize_times(out, tz_name_eff))
            return out

    last_err: Optional[str] = None

    for attempt_idx, spec in enumerate(ATTEMPTS):
        url = _build_url(lat, lon, tz_name_eff, spec)
        tries = 1 + max(0, RETRIES)
        for t in range(tries):
            try:
                if DEBUG:
                    LOG.info("weather: fetch attempt=%s.%s", attempt_idx + 1, t + 1)
                obj = _http_get_json(url, timeout_sec=TIMEOUT_SEC)
                if _is_error_payload(obj):
                    last_err = f"api_error:{obj.get('reason') or 'unknown'}"
                    if DEBUG:
                        LOG.warning("weather: api error payload (%s)", last_err)
                    break  # move to next spec
                obj2 = _ensure_aliases(_normalize_times(obj, tz_name_eff))
                _write_cache(cache_path, {"fetched_at": _now_ts(), "data": obj2})
                return obj2
            except Exception as e:
                last_err = str(e)
                if t < tries - 1:
                    time.sleep(BACKOFF ** t)
                else:
                    if DEBUG:
                        LOG.warning("weather: fetch failed (spec=%s, err=%s)", attempt_idx + 1, e)

    # Emergency: stale cache up to 24h
    if cached_payload is not None:
        if cached_ts is None or (_now_ts() - int(cached_ts) <= 24 * 3600):
            if DEBUG:
                LOG.warning("weather: using stale cache (%s), last_err=%s", cache_path.name, last_err)
            out = dict(cached_payload)
            out = _ensure_aliases(_normalize_times(out, tz_name_eff))
            return out

    if DEBUG:
        LOG.error("weather: no data (last_err=%s)", last_err)
    return {}


__all__ = ["get_weather"]
