#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
weather.py

Унификация источников прогноза погоды:
 - OpenWeather One Call API (версии 3.0 → 2.5)
 - Open-Meteo API (daily + hourly)
 - Фоллбэк: только current_weather

Возвращает структуру:
{
  "current": { … },         # как в Open-Meteo current_weather + pressure/clouds
  "daily":    […],          # список с одним элементом: { temperature_2m_max, temperature_2m_min, weathercode }
  "hourly":   { … },         # с ключами surface_pressure, cloud_cover, weathercode, wind_speed, wind_direction
  "strong_wind": bool,       # скорость ветра > 30 км/ч
  "fog_alert":   bool        # код погоды в (45, 48)
}
"""

import os
from typing import Optional, Dict, Any

from utils import _get

OWM_KEY = os.getenv("OWM_KEY")


def get_weather(lat: float, lon: float) -> Optional[Dict[str, Any]]:
    # ─── 1️⃣ Попытка OpenWeather One Call (3.0 → 2.5) ───────────────
    if OWM_KEY:
        for ver in ("3.0", "2.5"):
            ow = _get(
                f"https://api.openweathermap.org/data/{ver}/onecall",
                lat=lat, lon=lon, appid=OWM_KEY, units="metric",
                exclude="minutely,hourly,alerts",
            )
            if ow and "current" in ow:
                cur = ow["current"]
                # унификация под Open-Meteo
                ow["current"] = {
                    "temperature":   cur.get("temp"),
                    "pressure":      cur.get("pressure"),
                    "clouds":        cur.get("clouds"),
                    "windspeed":     cur.get("wind_speed") * 3.6,  # м/с → км/ч
                    "winddirection": cur.get("wind_deg"),
                    "weathercode":   cur.get("weather", [{}])[0].get("id", 0),
                }
                # эмуляция hourly
                ow["hourly"] = {
                    "surface_pressure": [cur.get("pressure")],
                    "cloud_cover":      [cur.get("clouds")],
                    "weathercode":      [cur.get("weather", [{}])[0].get("id", 0)],
                    "wind_speed":       [cur.get("wind_speed") * 3.6],
                    "wind_direction":   [cur.get("wind_deg")],
                }
                # флаги
                speed = ow["current"]["windspeed"]
                ow["strong_wind"] = speed > 30
                ow["fog_alert"]   = False  # OpenWeather не выдаёт коды тумана 45/48
                return ow

    # ─── 2️⃣ Попытка Open-Meteo (daily + hourly) ───────────────────
    om = _get(
        "https://api.open-meteo.com/v1/forecast",
        latitude=lat, longitude=lon, timezone="UTC", current_weather="true",
        forecast_days=2,
        daily="temperature_2m_max,temperature_2m_min,weathercode",
        hourly="surface_pressure,cloud_cover,weathercode,wind_speed,wind_direction",
    )
    if om and "current_weather" in om and "daily" in om:
        cur = om["current_weather"]
        # встраиваем давление и облачность в current
        cur["pressure"] = om["hourly"]["surface_pressure"][0]
        cur["clouds"]   = om["hourly"]["cloud_cover"][0]
        # флаги
        speed = cur.get("windspeed", 0)
        code_day = om["daily"]["weathercode"][0]
        om["strong_wind"] = speed > 30
        om["fog_alert"]   = code_day in (45, 48)
        return om

    # ─── 3️⃣ Фоллбэк Open-Meteo — только current_weather ─────────────
    om = _get(
        "https://api.open-meteo.com/v1/forecast",
        latitude=lat, longitude=lon, timezone="UTC", current_weather="true",
    )
    if not om or "current_weather" not in om:
        return None

    cw = om["current_weather"]
    # эмуляция daily
    om["daily"] = [{
        "temperature_2m_max": [cw["temperature"]],
        "temperature_2m_min": [cw["temperature"]],
        "weathercode":        [cw["weathercode"]],
    }]
    # эмуляция hourly
    om["hourly"] = {
        "surface_pressure": [cw.get("pressure", 1013)],
        "cloud_cover":      [cw.get("clouds",   0   )],
        "weathercode":      [cw["weathercode"]],
        "wind_speed":       [cw.get("windspeed", 0  )],
        "wind_direction":   [cw.get("winddirection", 0)],
    }
    # флаги
    speed = cw.get("windspeed", 0)
    om["strong_wind"] = speed > 30
    om["fog_alert"]   = cw.get("weathercode", 0) in (45, 48)
    return om
