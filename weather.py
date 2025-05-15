#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
weather.py

Унификация источников прогноза погоды:
 1. OpenWeather One Call (v3.0 → v2.5)
 2. Open-Meteo с указанием start_date/end_date
 3. Фоллбэк: только current_weather

Возвращает структуру:
{
  "current": {temperature, pressure, clouds, windspeed, winddirection, weathercode},
  "daily": {
      "time": [today, tomorrow],
      "temperature_2m_max": [...],
      "temperature_2m_min": [...],
      "weathercode": [...]
  },
  "hourly": {...},
  "strong_wind": bool,
  "fog_alert": bool
}
"""

import os
from typing import Any, Dict, Optional
from utils import _get
import pendulum

OWM_KEY = os.getenv("OWM_KEY")

def get_weather(lat: float, lon: float) -> Optional[Dict[str, Any]]:
    # 1️⃣ OpenWeather One Call
    if OWM_KEY:
        for ver in ("3.0", "2.5"):
            ow = _get(
                f"https://api.openweathermap.org/data/{ver}/onecall",
                lat=lat, lon=lon, appid=OWM_KEY, units="metric",
                exclude="minutely,hourly,alerts",
            )
            if ow and "current" in ow:
                cur = ow["current"]
                # унификация под Open-Meteo-like
                ow["current"] = {
                    "temperature":   cur.get("temp"),
                    "pressure":      cur.get("pressure"),
                    "clouds":        cur.get("clouds"),
                    "windspeed":     cur.get("wind_speed") * 3.6,
                    "winddirection": cur.get("wind_deg"),
                    "weathercode":   cur.get("weather", [{}])[0].get("id", 0),
                }
                ow["hourly"] = {
                    "surface_pressure": [cur.get("pressure")],
                    "cloud_cover":      [cur.get("clouds")],
                    "weathercode":      [cur.get("weather", [{}])[0].get("id", 0)],
                    "wind_speed":       [cur.get("wind_speed") * 3.6],
                    "wind_direction":   [cur.get("wind_deg")],
                }
                ow["daily"] = {
                    "time":                [],  # нет — не используем
                    "temperature_2m_max":  [cur.get("temp")],
                    "temperature_2m_min":  [cur.get("temp")],
                    "weathercode":         [cur.get("weather", [{}])[0].get("id", 0)],
                }
                speed = ow["current"]["windspeed"]
                ow["strong_wind"] = speed > 30
                ow["fog_alert"]   = False
                return ow

    # 2️⃣ Open-Meteo с start_date/end_date
    today    = pendulum.now().to_date_string()
    tomorrow = pendulum.now().add(days=1).to_date_string()
    om = _get(
        "https://api.open-meteo.com/v1/forecast",
        latitude=lat,
        longitude=lon,
        timezone="Europe/Nicosia",
        current_weather="true",
        daily="temperature_2m_max,temperature_2m_min,weathercode",
        start_date=today,
        end_date=tomorrow,
        hourly="surface_pressure,cloud_cover,weathercode,wind_speed,wind_direction",
    )
    if om and "daily" in om and "current_weather" in om:
        cur = om["current_weather"]
        cur["pressure"] = om["hourly"]["surface_pressure"][0]
        cur["clouds"]   = om["hourly"]["cloud_cover"][0]
        om["current"]   = {
            "temperature":   cur.get("temperature"),
            "pressure":      cur.get("pressure"),
            "clouds":        cur.get("clouds"),
            "windspeed":     cur.get("windspeed"),
            "winddirection": cur.get("winddirection"),
            "weathercode":   cur.get("weathercode"),
        }
        speed = cur.get("windspeed", 0)
        code_day = om["daily"]["weathercode"][1] if len(om["daily"]["weathercode"]) > 1 else om["daily"]["weathercode"][0]
        om["strong_wind"] = speed > 30
        om["fog_alert"]   = code_day in (45, 48)
        return om

    # 3️⃣ Фоллбэк: только current_weather
    om = _get(
        "https://api.open-meteo.com/v1/forecast",
        latitude=lat, longitude=lon,
        timezone="Europe/Nicosia",
        current_weather="true",
    )
    if not om or "current_weather" not in om:
        return None

    cw = om["current_weather"]
    om["daily"] = {
        "temperature_2m_max": [cw["temperature"], cw["temperature"]],
        "temperature_2m_min": [cw["temperature"], cw["temperature"]],
        "weathercode":        [cw["weathercode"], cw["weathercode"]],
    }
    om["hourly"] = {
        "surface_pressure": [cw.get("pressure", 1013)],
        "cloud_cover":      [cw.get("clouds",   0   )],
        "weathercode":      [cw["weathercode"]],
        "wind_speed":       [cw.get("windspeed", 0  )],
        "wind_direction":   [cw.get("winddirection", 0)],
    }
    speed = cw.get("windspeed", 0)
    om["strong_wind"] = speed > 30
    om["fog_alert"]   = cw.get("weathercode", 0) in (45, 48)
    return om
