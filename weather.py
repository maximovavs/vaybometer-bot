#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
weather.py  ─ унифицирует прогноз из 3-х источников.

1) OpenWeather One Call (v3→v2) – если установлен OWM_KEY
2) Open-Meteo (start / end = сегодня + завтра)
3) Фоллбэк – Open-Meteo «current_weather»

• daily.temperature_2m_max / _min ВСЕГДА содержат
  два элемента [today, tomorrow]  – чтобы ночная t° не «терялась».

НОВОЕ --------------------------------------------------------------
get_pressure_series(lat, lon, hours=24)  →  list[float] | None
Возвращает до `hours` значений surface_pressure (UTC) для трендов.
-------------------------------------------------------------------
"""

from __future__ import annotations
from typing import Any, Dict, Optional, List
import os, pendulum, logging

from utils import _get

OWM_KEY = os.getenv("OWM_KEY")           # может быть None


# ───────────────────── 1) OpenWeather ────────────────────────────
def _openweather(lat: float, lon: float) -> Optional[Dict[str, Any]]:
    if not OWM_KEY:
        return None

    for ver in ("3.0", "2.5"):
        ow = _get(
            f"https://api.openweathermap.org/data/{ver}/onecall",
            lat=lat, lon=lon,
            appid=OWM_KEY, units="metric",
            exclude="minutely,hourly,alerts",
        )
        if not ow or "current" not in ow:
            continue

        cur = ow["current"]

        # current в «стиле open-meteo»
        ow["current"] = {
            "temperature":   cur.get("temp"),
            "pressure":      cur.get("pressure"),
            "clouds":        cur.get("clouds"),
            "windspeed":     cur.get("wind_speed") * 3.6,   # м/с → км/ч
            "winddirection": cur.get("wind_deg"),
            "weathercode":   cur.get("weather", [{}])[0].get("id", 0),
        }

        ow["hourly"] = {
            "surface_pressure":    [cur.get("pressure")],
            "cloud_cover":         [cur.get("clouds")],
            "weathercode":         [ow["current"]["weathercode"]],
            "wind_speed_10m":      [ow["current"]["windspeed"]],
            "wind_direction_10m":  [ow["current"]["winddirection"]],
        }

        # daily (дублируем t°C, чтобы было 2 элемента)
        t  = cur.get("temp")
        wc = ow["current"]["weathercode"]
        ow["daily"] = {
            "time":                [],
            "temperature_2m_max":  [t, t],
            "temperature_2m_min":  [t, t],
            "weathercode":         [wc, wc],
        }

        ow["strong_wind"] = ow["current"]["windspeed"] > 30
        ow["fog_alert"]   = False
        return ow
    return None


# ─────────────── 2) Open-Meteo — полный прогноз ──────────────────
def _openmeteo(lat: float, lon: float) -> Optional[Dict[str, Any]]:
    today    = pendulum.today().to_date_string()
    tomorrow = pendulum.tomorrow().to_date_string()

    om = _get(
        "https://api.open-meteo.com/v1/forecast",
        latitude=lat, longitude=lon,
        timezone="UTC",
        start_date=today, end_date=tomorrow,
        current_weather="true",
        daily="temperature_2m_max,temperature_2m_min,weathercode",
        hourly="surface_pressure,cloud_cover,weathercode,"
               "wind_speed_10m,wind_direction_10m",
    )
    if not om or "current_weather" not in om or "daily" not in om:
        return None

    cur = om["current_weather"]
    cur["pressure"] = om["hourly"]["surface_pressure"][0]
    cur["clouds"]   = om["hourly"]["cloud_cover"][0]

    om["current"] = {
        "temperature":   cur["temperature"],
        "pressure":      cur["pressure"],
        "clouds":        cur["clouds"],
        "windspeed":     cur["windspeed"],
        "winddirection": cur["winddirection"],
        "weathercode":   cur["weathercode"],
    }

    om["strong_wind"] = cur["windspeed"] > 30
    om["fog_alert"]   = om["daily"]["weathercode"][0] in (45, 48)
    # daily уже содержит два элемента → ничего не режем
    return om


# ─────── 3) Open-Meteo current-only (fallback) ───────────────────
def _openmeteo_current_only(lat: float, lon: float) -> Optional[Dict[str, Any]]:
    om = _get(
        "https://api.open-meteo.com/v1/forecast",
        latitude=lat, longitude=lon,
        current_weather="true", timezone="UTC",
    )
    if not om or "current_weather" not in om:
        return None

    cw = om["current_weather"]

    om["current"] = {
        "temperature":   cw["temperature"],
        "pressure":      cw.get("pressure", 1013),
        "clouds":        cw.get("clouds", 0),
        "windspeed":     cw.get("windspeed", 0),
        "winddirection": cw.get("winddirection", 0),
        "weathercode":   cw.get("weathercode", 0),
    }

    t  = cw["temperature"]
    wc = cw.get("weathercode", 0)
    om["daily"] = {
        "temperature_2m_max": [t, t],
        "temperature_2m_min": [t, t],
        "weathercode":        [wc, wc],
    }

    om["hourly"] = {
        "surface_pressure":    [om["current"]["pressure"]],
        "cloud_cover":         [om["current"]["clouds"]],
        "weathercode":         [om["current"]["weathercode"]],
        "wind_speed_10m":      [om["current"]["windspeed"]],
        "wind_direction_10m":  [om["current"]["winddirection"]],
    }

    om["strong_wind"] = om["current"]["windspeed"] > 30
    om["fog_alert"]   = om["current"]["weathercode"] in (45, 48)
    return om


# ──────────────── универсальный вход  ────────────────────────────
def get_weather(lat: float, lon: float) -> Optional[Dict[str, Any]]:
    """Возвращает первую удачную унифицированную структуру прогноза."""
    for fn in (_openweather, _openmeteo, _openmeteo_current_only):
        data = fn(lat, lon)
        if data:
            return data
    return None


# ─────────────────────  NEW: pressure series  ────────────────────
def get_pressure_series(lat: float, lon: float, hours: int = 24) -> Optional[List[float]]:
    """
    Возвращает список из `hours` значений surface_pressure (гПа, UTC)
    для ближайших часов.  Используем только Open-Meteo, так как
    OpenWeather требует платный тариф для hourly.

    • Если данных нет → None.
    • Если значений меньше, чем `hours`, вернётся столько, сколько есть.
    """
    try:
        j = _get(
            "https://api.open-meteo.com/v1/forecast",
            latitude=lat, longitude=lon,
            timezone="UTC",
            hourly="surface_pressure",
            forecast_days=2,          # гарантированно ≥ 24 часов
        )
        if not j or "hourly" not in j:
            return None
        series = j["hourly"]["surface_pressure"][:hours]
        # фильтруем None / неверные типы
        return [float(p) for p in series if isinstance(p, (int, float))]
    except Exception as e:
        logging.warning("get_pressure_series(%s,%s) error: %s", lat, lon, e)
        return None


# ─────────────────────────── CLI-тест ────────────────────────────
if __name__ == "__main__":
    # Пример быстрого теста: выводим 5 точек давления
    lim_lat, lim_lon = 34.707, 33.022
    print("First 5 hourly pressures for Limassol:")
    print(get_pressure_series(lim_lat, lim_lon, hours=5))
