#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
weather.py  •  универсальный слой над двумя API

1. OpenWeather One Call (v3.0 → v2.5)  – если есть ключ OWM_KEY
2. Open-Meteo – запрос «сегодня + завтра» через forecast_days=2
3. fallback  – Open-Meteo только current_weather

На выходе структура:
{
  "current": {temperature, pressure, clouds,
              windspeed, winddirection, weathercode},
  "daily":   {...}   # всегда массив-dict с завтрашними max/min
  "hourly":  {...}   # минимальный набор (pressure, clouds, wind)
  "strong_wind": bool,
  "fog_alert":   bool
}
"""

from __future__ import annotations
from typing import Any, Dict, Optional
import os, pendulum

from utils import _get

OWM_KEY = os.getenv("OWM_KEY")


# ───────────────────────── OpenWeather helper ──────────────────────────
def _via_openweather(lat: float, lon: float) -> Optional[Dict[str, Any]]:
    if not OWM_KEY:
        return None

    for ver in ("3.0", "2.5"):
        j = _get(
            f"https://api.openweathermap.org/data/{ver}/onecall",
            lat=lat, lon=lon, appid=OWM_KEY, units="metric",
            exclude="minutely,hourly,alerts",
        )
        if not j or "current" not in j:
            continue

        cur = j["current"]                      # исходная «сырость»

        # --- current в стиле Open-Meteo ---------------------------------
        j["current"] = {
            "temperature":   cur.get("temp"),
            "pressure":      cur.get("pressure"),
            "clouds":        cur.get("clouds"),
            "windspeed":     cur.get("wind_speed", 0) * 3.6,   # м/с→км/ч
            "winddirection": cur.get("wind_deg"),
            "weathercode":   cur.get("weather", [{}])[0].get("id", 0),
        }

        # --- hourly (1-элементная «оболочка» для унификации) ------------
        j["hourly"] = {
            "surface_pressure": [cur.get("pressure")],
            "cloud_cover":      [cur.get("clouds")],
            "weathercode":      [j["current"]["weathercode"]],
            "wind_speed":       [j["current"]["windspeed"]],
            "wind_direction":   [j["current"]["winddirection"]],
        }

        # --- daily берём из OpenWeather-овского daily[1] (завтра) -------
        d_src = j.get("daily", [])
        if d_src:
            blk = d_src[1] if len(d_src) > 1 else d_src[0]
            j["daily"] = {
                "temperature_2m_max": [blk["temp"]["max"]],
                "temperature_2m_min": [blk["temp"]["min"]],
                "weathercode":        [blk["weather"][0]["id"]],
            }
        else:   # на всякий случай — дублируем текущую
            j["daily"] = {
                "temperature_2m_max": [cur.get("temp")],
                "temperature_2m_min": [cur.get("temp")],
                "weathercode":        [j["current"]["weathercode"]],
            }

        # --- флаги -------------------------------------------------------
        j["strong_wind"] = j["current"]["windspeed"] > 30
        j["fog_alert"]   = False                    # OWM коды ≠ 45/48
        return j

    return None


# ───────────────────────── Open-Meteo helper ───────────────────────────
def _via_openmeteo(lat: float, lon: float) -> Optional[Dict[str, Any]]:
    j = _get(
        "https://api.open-meteo.com/v1/forecast",
        latitude=lat,
        longitude=lon,
        timezone="UTC",           # UTC → потом конвертим сами
        current_weather="true",
        forecast_days=2,          # сегодня + завтра
        daily="temperature_2m_max,temperature_2m_min,weathercode",
        hourly="surface_pressure,cloud_cover,weathercode,"
               "wind_speed,wind_direction",
    )
    if not j or "current_weather" not in j or "daily" not in j:
        return None

    cur = j["current_weather"]

    # встраиваем давление / облачность из первого часа
    cur["pressure"] = j["hourly"]["surface_pressure"][0]
    cur["clouds"]   = j["hourly"]["cloud_cover"][0]

    # упорядочиваем daily в массив-dict (берём только завтра)
    d = j["daily"]
    day_idx = 1 if len(d["temperature_2m_max"]) > 1 else 0
    j["daily"] = {
        "temperature_2m_max": [d["temperature_2m_max"][day_idx]],
        "temperature_2m_min": [d["temperature_2m_min"][day_idx]],
        "weathercode":        [d["weathercode"][day_idx]],
    }

    # приводим названия под openweather-style
    j["current"] = {
        "temperature":   cur["temperature"],
        "pressure":      cur["pressure"],
        "clouds":        cur["clouds"],
        "windspeed":     cur["windspeed"],
        "winddirection": cur["winddirection"],
        "weathercode":   cur["weathercode"],
    }

    # флаги
    j["strong_wind"] = cur["windspeed"] > 30
    j["fog_alert"]   = j["daily"]["weathercode"][0] in (45, 48)

    return j


# ───────────────────────── публичная обёртка ───────────────────────────
def get_weather(lat: float, lon: float) -> Optional[Dict[str, Any]]:
    """Первая удачная попытка из двух источников."""
    return (
        _via_openweather(lat, lon) or
        _via_openmeteo(lat, lon)
    )
