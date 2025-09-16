#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
weather.py — единый слой для погоды (Кипр), совместимый с рендером «как у KLD».

Экспорт:
- get_weather(lat, lon) -> dict с блоками hourly/daily
- fetch_tomorrow_temps(lat, lon, tz) -> (t_day_max, t_night_min)
- day_night_stats(lat, lon, tz) -> {t_day_max, t_night_min, rh_min, rh_max}

Нормализация hourly-полей (синонимы):
  time / time_local / timestamp (гарантируем наличие 'time')
  wind_speed_10m | windspeed_10m | windspeed             (км/ч)
  wind_direction_10m | winddirection_10m | winddirection (градусы)
  wind_gusts_10m | wind_gusts | windgusts_10m            (км/ч)
  surface_pressure | pressure                             (гПа)
  relative_humidity_2m | relativehumidity_2m | humidity   (%)
"""

from __future__ import annotations
from typing import Any, Dict, List, Optional, Tuple
import os, logging

import pendulum
from utils import _get

log = logging.getLogger(__name__)

# ───────────────────────── helpers ─────────────────────────

_UA = "vaybometer/1.0 (+github actions)"

def _safe_http_get(url: str, params: dict | None = None, timeout: int = 10) -> Optional[Dict[str, Any]]:
    try:
        r = _get(url, params=params, headers={"User-Agent": _UA}, timeout=timeout)
        return r.json()
    except Exception as e:
        log.warning("_safe_http_get — HTTP error: %s", e)
        return None

def _ensure_aliases_om_payload(w: Dict[str, Any]) -> Dict[str, Any]:
    """Приводим структуру к kld-совместимой схеме + гарантируем hourly.time."""
    if not isinstance(w, dict):
        return {}

    out = {"hourly": {}, "daily": {}}
    hourly = (w.get("hourly") or {}) if isinstance(w.get("hourly"), dict) else {}
    daily  = (w.get("daily")  or {}) if isinstance(w.get("daily"),  dict) else {}

    # time
    time_arr = (
        hourly.get("time")
        or hourly.get("time_local")
        or hourly.get("timestamp")
        or []
    )
    if not isinstance(time_arr, list):
        time_arr = []
    out["hourly"]["time"] = [str(t) for t in time_arr]

    # wind speed (км/ч)
    spd = hourly.get("wind_speed_10m") or hourly.get("windspeed_10m") or hourly.get("windspeed")
    if isinstance(spd, list):
        out["hourly"]["wind_speed_10m"] = spd
        out["hourly"]["windspeed_10m"]  = spd
        out["hourly"]["windspeed"]      = spd

    # wind dir
    wdir = hourly.get("wind_direction_10m") or hourly.get("winddirection_10m") or hourly.get("winddirection")
    if isinstance(wdir, list):
        out["hourly"]["wind_direction_10m"] = wdir
        out["hourly"]["winddirection_10m"]  = wdir
        out["hourly"]["winddirection"]      = wdir

    # gusts (км/ч)
    gust = hourly.get("wind_gusts_10m") or hourly.get("wind_gusts") or hourly.get("windgusts_10m")
    if isinstance(gust, list):
        out["hourly"]["wind_gusts_10m"] = gust
        out["hourly"]["wind_gusts"]     = gust
        out["hourly"]["windgusts_10m"]  = gust

    # pressure (гПа)
    pres = hourly.get("surface_pressure") or hourly.get("pressure")
    if isinstance(pres, list):
        out["hourly"]["surface_pressure"] = pres
        out["hourly"]["pressure"]         = pres

    # RH (%) — добавлен алиас relativehumidity_2m
    rh = hourly.get("relative_humidity_2m") or hourly.get("relativehumidity_2m") or hourly.get("humidity")
    if isinstance(rh, list):
        out["hourly"]["relative_humidity_2m"] = rh
        out["hourly"]["relativehumidity_2m"]  = rh
        out["hourly"]["humidity"]             = rh

    # Температура — пригодится для расчётов, если нет daily
    temp = hourly.get("temperature_2m")
    if isinstance(temp, list):
        out["hourly"]["temperature_2m"] = temp

    # daily.weathercode (иконка завтра)
    wc = daily.get("weathercode")
    if isinstance(wc, list):
        out["daily"]["weathercode"] = wc

    # daily temps — если есть
    tmax = daily.get("temperature_2m_max")
    tmin = daily.get("temperature_2m_min")
    if isinstance(tmax, list):
        out["daily"]["temperature_2m_max"] = tmax
    if isinstance(tmin, list):
        out["daily"]["temperature_2m_min"] = tmin

    # daily RH (для фоллбэка)
    rh_min = daily.get("relative_humidity_2m_min") or daily.get("relativehumidity_2m_min")
    rh_max = daily.get("relative_humidity_2m_max") or daily.get("relativehumidity_2m_max")
    if isinstance(rh_min, list):
        out["daily"]["relative_humidity_2m_min"] = rh_min
    if isinstance(rh_max, list):
        out["daily"]["relative_humidity_2m_max"] = rh_max

    return out

# ─────────────────────── источники погоды ───────────────────────

def _openmeteo(lat: float, lon: float) -> Optional[Dict[str, Any]]:
    """Полный forecast Open-Meteo с часовками и daily."""
    url = "https://api.open-meteo.com/v1/forecast"
    params = {
        "latitude": lat,
        "longitude": lon,
        "hourly": ",".join([
            "temperature_2m",
            "wind_speed_10m",
            "wind_direction_10m",
            "wind_gusts_10m",
            "surface_pressure",
            "relative_humidity_2m",
        ]),
        "daily": "weathercode,temperature_2m_max,temperature_2m_min,relative_humidity_2m_min,relative_humidity_2m_max",
        "forecast_days": 3,
        "timezone": "UTC",
    }
    j = _safe_http_get(url, params=params, timeout=15)
    if not isinstance(j, dict):
        return None

    h = j.get("hourly") or {}
    d = j.get("daily")  or {}

    time_iso = [str(t) for t in (h.get("time") or [])]

    out = {
        "hourly": {
            "time": time_iso,
            "temperature_2m": h.get("temperature_2m"),
            "wind_speed_10m": h.get("wind_speed_10m"),
            "wind_direction_10m": h.get("wind_direction_10m"),
            "wind_gusts_10m": h.get("wind_gusts_10m"),
            "surface_pressure": h.get("surface_pressure"),
            "relative_humidity_2m": h.get("relative_humidity_2m"),
        },
        "daily": {
            "weathercode": d.get("weathercode"),
            "temperature_2m_max": d.get("temperature_2m_max"),
            "temperature_2m_min": d.get("temperature_2m_min"),
            "relative_humidity_2m_min": d.get("relative_humidity_2m_min"),
            "relative_humidity_2m_max": d.get("relative_humidity_2m_max"),
        },
    }
    return _ensure_aliases_om_payload(out)

def _owm_map_to_wmo(code: int) -> int:
    """Грубая мапа кодов OWM -> WMO для иконки."""
    try:
        c = int(code)
    except Exception:
        return 0
    if c == 800: return 0
    if 801 <= c <= 803: return 1
    if c == 804: return 3
    if 200 <= c <= 232: return 95
    if 300 <= c <= 321: return 51
    if 500 <= c <= 531: return 61
    if 600 <= c <= 622: return 71
    if 700 <= c <= 781: return 45
    return 2

def _openweather(lat: float, lon: float) -> Optional[Dict[str, Any]]:
    """OWM OneCall 3.0. Нормализуем к km/h и добавляем hourly.time как ISO."""
    key = os.getenv("OWM_KEY") or os.getenv("OWM_API_KEY")
    if not key:
        return None
    url = "https://api.openweathermap.org/data/3.0/onecall"
    params = {
        "lat": lat, "lon": lon, "appid": key,
        "units": "metric", "exclude": "minutely,alerts",
    }
    j = _safe_http_get(url, params=params, timeout=15)
    if not isinstance(j, dict):
        return None

    hourly = j.get("hourly") or []
    daily  = j.get("daily")  or []

    times_iso: List[str] = []
    spd_kmh:  List[Optional[float]] = []
    dir_deg:  List[Optional[float]] = []
    gust_kmh: List[Optional[float]] = []
    pres_hpa: List[Optional[float]] = []
    rh_perc:  List[Optional[float]] = []
    temp:     List[Optional[float]] = []

    for it in hourly:
        try:
            ts = int(it.get("dt"))
            t  = pendulum.from_timestamp(ts, tz="UTC").to_iso8601_string()
            times_iso.append(t)
        except Exception:
            continue
        try: spd_kmh.append(float(it.get("wind_speed", 0.0)) * 3.6)
        except Exception: spd_kmh.append(None)
        try: dir_deg.append(float(it.get("wind_deg")))
        except Exception: dir_deg.append(None)
        try:
            g = it.get("wind_gust")
            gust_kmh.append(float(g) * 3.6 if g is not None else None)
        except Exception:
            gust_kmh.append(None)
        try: pres_hpa.append(float(it.get("pressure")))
        except Exception: pres_hpa.append(None)
        try: rh_perc.append(float(it.get("humidity")))
        except Exception: rh_perc.append(None)
        try: temp.append(float(it.get("temp")))
        except Exception: temp.append(None)

    d_wmo: List[int] = []
    d_tmax: List[Optional[float]] = []
    d_tmin: List[Optional[float]] = []
    for d in daily:
        w = (d.get("weather") or [{}])[0] or {}
        d_wmo.append(_owm_map_to_wmo(w.get("id", 800)))
        t = d.get("temp") or {}
        d_tmax.append(t.get("max"))
        d_tmin.append(t.get("min"))

    out = {
        "hourly": {
            "time": times_iso,
            "wind_speed_10m": spd_kmh,
            "wind_direction_10m": dir_deg,
            "wind_gusts_10m": gust_kmh,
            "surface_pressure": pres_hpa,
            "relative_humidity_2m": rh_perc,
            "temperature_2m": temp,
        },
        "daily": {
            "weathercode": d_wmo,
            "temperature_2m_max": d_tmax,
            "temperature_2m_min": d_tmin,
        },
    }
    return _ensure_aliases_om_payload(out)

def _openmeteo_current_only(lat: float, lon: float) -> Optional[Dict[str, Any]]:
    """Минимальный фоллбэк: current → склеиваем в 'hourly' из одного значения и добавляем time (включая RH)."""
    url = "https://api.open-meteo.com/v1/forecast"
    params = {
        "latitude": lat,
        "longitude": lon,
        "current": "wind_speed_10m,wind_direction_10m,wind_gusts_10m,surface_pressure,relative_humidity_2m",
        "timezone": "UTC",
    }
    j = _safe_http_get(url, params=params, timeout=10)
    if not isinstance(j, dict):
        return None

    cur = j.get("current") or {}
    def _f(name, default=None):
        v = cur.get(name)
        return [v] if v is not None else ([default] if default is not None else [])

    now_iso = pendulum.now("UTC").replace(minute=0, second=0, microsecond=0).to_iso8601_string()
    out = {
        "hourly": {
            "time": [now_iso],
            "wind_speed_10m": _f("wind_speed_10m"),
            "wind_direction_10m": _f("wind_direction_10m"),
            "wind_gusts_10m": _f("wind_gusts_10m"),
            "surface_pressure": _f("surface_pressure"),
            "relative_humidity_2m": _f("relative_humidity_2m"),
        },
        "daily": {"weathercode": [0, 0, 0]},
    }
    return _ensure_aliases_om_payload(out)

# ─────────────────────── публичные функции ───────────────────────

def get_weather(lat: float, lon: float) -> Optional[Dict[str, Any]]:
    """Пробуем OM → OWM → OM current-only, нормализуя поля и hourly.time."""
    for fn in (_openmeteo, _openweather, _openmeteo_current_only):
        try:
            data = fn(lat, lon)  # type: ignore
        except Exception as e:
            log.warning("%s failed: %s", getattr(fn, "__name__", "src"), e)
            data = None
        if data:
            return data
    return None

def fetch_tomorrow_temps(lat: float, lon: float, tz: str = "UTC") -> Tuple[Optional[float], Optional[float]]:
    """(t_day_max, t_night_min) через daily OM; если нет — по часовкам завтрашних суток."""
    tzobj = pendulum.timezone(tz)
    w = _openmeteo(lat, lon) or _openweather(lat, lon) or _openmeteo_current_only(lat, lon) or {}
    d = (w.get("daily") or {})
    tmax = d.get("temperature_2m_max")
    tmin = d.get("temperature_2m_min")
    if isinstance(tmax, list) and len(tmax) > 1 and isinstance(tmin, list) and len(tmin) > 1:
        return tmax[1], tmin[1]

    # Фоллбэк: из hourly temperature_2m на «завтра»
    h = w.get("hourly") or {}
    times = h.get("time") or []
    temps = h.get("temperature_2m") or []
    if not (isinstance(times, list) and isinstance(temps, list) and times and temps):
        return None, None

    tomorrow = pendulum.today(tzobj).add(days=1).date()
    vals: List[float] = []
    for t, v in zip(times, temps):
        try:
            dt = pendulum.parse(str(t)).in_tz(tzobj)
            if dt.date() == tomorrow and v is not None:
                vals.append(float(v))
        except Exception:
            continue
    if not vals:
        return None, None
    return max(vals), min(vals)

def day_night_stats(lat: float, lon: float, tz: str = "UTC") -> Dict[str, Optional[float]]:
    """
    Возвращает:
      { t_day_max, t_night_min, rh_min, rh_max, rh_avg }
    по часовкам «завтра» в заданной таймзоне. RH имеет фоллбэк из daily.
    """
    tzobj = pendulum.timezone(tz)
    w = _openmeteo(lat, lon) or _openweather(lat, lon) or _openmeteo_current_only(lat, lon) or {}
    h = w.get("hourly") or {}
    times = h.get("time") or []
    temps = h.get("temperature_2m") or []
    rh    = h.get("relative_humidity_2m") or h.get("relativehumidity_2m") or h.get("humidity") or []

    tomorrow = pendulum.today(tzobj).add(days=1).date()

    t_vals: List[float] = []
    rh_vals: List[float] = []
    for i, t in enumerate(times if isinstance(times, list) else []):
        try:
            dt = pendulum.parse(str(t)).in_tz(tzobj)
        except Exception:
            continue
        if dt.date() != tomorrow:
            continue
        # Температура
        if isinstance(temps, list) and i < len(temps) and temps[i] is not None:
            try: t_vals.append(float(temps[i]))
            except Exception: ...
        # Влажность
        if isinstance(rh, list) and i < len(rh) and rh[i] is not None:
            try: rh_vals.append(float(rh[i]))
            except Exception: ...

    out: Dict[str, Optional[float]] = {
        "t_day_max": max(t_vals) if t_vals else None,
        "t_night_min": min(t_vals) if t_vals else None,
        "rh_min": (min(rh_vals) if rh_vals else None),
        "rh_max": (max(rh_vals) if rh_vals else None),
        "rh_avg": (sum(rh_vals)/len(rh_vals) if rh_vals else None),
    }

    # Приоритезируем суточные t_max/t_min, если есть
    d = w.get("daily") or {}
    tmax = d.get("temperature_2m_max")
    tmin = d.get("temperature_2m_min")
    if isinstance(tmax, list) and len(tmax) > 1 and tmax[1] is not None:
        out["t_day_max"] = tmax[1]
    if isinstance(tmin, list) and len(tmin) > 1 and tmin[1] is not None:
        out["t_night_min"] = tmin[1]

    # Фоллбэк RH из daily, если по часовкам пусто
    if out["rh_min"] is None or out["rh_max"] is None:
        rh_min = d.get("relative_humidity_2m_min") or d.get("relativehumidity_2m_min")
        rh_max = d.get("relative_humidity_2m_max") or d.get("relativehumidity_2m_max")
        if isinstance(rh_min, list) and len(rh_min) > 1 and rh_min[1] is not None:
            out["rh_min"] = float(rh_min[1])
        if isinstance(rh_max, list) and len(rh_max) > 1 and rh_max[1] is not None:
            out["rh_max"] = float(rh_max[1])

    return out
