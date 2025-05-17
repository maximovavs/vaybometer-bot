#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Единый доступ к прогнозу погоды:
 1) OpenWeather One Call (v3.0 → v2.5) — если есть ключ OWM_KEY
 2) Open-Meteo c start_date / end_date (рекомендовано)
 3) Фоллбэк — только current_weather

Возвращается словарь формата «Open-Meteo»:
{
  "current": {...},
  "daily":   {...},
  "hourly":  {...},
  "strong_wind": bool,
  "fog_alert":   bool
}
"""
from __future__ import annotations
import os, pendulum
from typing import Any, Dict, Optional
from utils import _get

OWM_KEY = os.getenv("OWM_KEY")

# ────────────────────────────────────────────────────────────────────
def _today_dates(tz: str="Europe/Nicosia") -> tuple[str,str]:
    now = pendulum.now(tz).date()
    return now.to_date_string(), (now + pendulum.duration(days=1)).to_date_string()

# ────────────────────────────────────────────────────────────────────
def get_weather(lat: float, lon: float) -> Optional[Dict[str, Any]]:
    # 1️⃣ OpenWeather (если есть ключ)
    if OWM_KEY:
        for ver in ("3.0", "2.5"):
            ow = _get(
                f"https://api.openweathermap.org/data/{ver}/onecall",
                lat=lat, lon=lon, appid=OWM_KEY, units="metric",
                exclude="minutely,hourly,alerts",
            )
            if ow and "current" in ow:
                c = ow["current"]
                ow["current"] = {
                    "temperature":   c["temp"],
                    "pressure":      c["pressure"],
                    "clouds":        c["clouds"],
                    "windspeed":     c["wind_speed"] * 3.6,
                    "winddirection": c["wind_deg"],
                    "weathercode":   c["weather"][0]["id"],
                }
                # эмулируем структуры
                ow["hourly"] = {
                    "surface_pressure": [c["pressure"]],
                    "cloud_cover":      [c["clouds"]],
                    "weathercode":      [c["weather"][0]["id"]],
                    "wind_speed":       [c["wind_speed"] * 3.6],
                    "wind_direction":   [c["wind_deg"]],
                }
                ow["daily"] = {
                    "time":               [],
                    "temperature_2m_max": [c["temp"]],
                    "temperature_2m_min": [c["temp"]],
                    "weathercode":        [c["weather"][0]["id"]],
                }
                ow["strong_wind"] = c["wind_speed"] * 3.6 > 30
                ow["fog_alert"]   = False
                return ow

    # 2️⃣ Open-Meteo (актуальный формат)
    start, end = _today_dates()
    om = _get(
        "https://api.open-meteo.com/v1/forecast",
        latitude=lat, longitude=lon,
        timezone="auto",
        current_weather="true",
        start_date=start, end_date=end,
        hourly="surface_pressure,cloud_cover,weathercode,wind_speed,wind_direction",
        daily="temperature_2m_max,temperature_2m_min,weathercode",
    )
    if om and "current_weather" in om and "daily" in om:
        cw = om["current_weather"]
        # добавляем давление/облачность в current
        cw["pressure"] = om["hourly"]["surface_pressure"][0]
        cw["clouds"]   = om["hourly"]["cloud_cover"][0]
        om["current"]  = {
            "temperature":   cw["temperature"],
            "pressure":      cw["pressure"],
            "clouds":        cw["clouds"],
            "windspeed":     cw["windspeed"],
            "winddirection": cw["winddirection"],
            "weathercode":   cw["weathercode"],
        }
        speed   = cw["windspeed"]
        code_td = om["daily"]["weathercode"][-1]   # код завтрашнего дня
        om["strong_wind"] = speed > 30
        om["fog_alert"]   = code_td in (45, 48)
        return om

    # 3️⃣ Фоллбэк: только current_weather
    om = _get(
        "https://api.open-meteo.com/v1/forecast",
        latitude=lat, longitude=lon,
        timezone="auto",
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
        "cloud_cover":      [cw.get("clouds", 0)],
        "weathercode":      [cw["weathercode"]],
        "wind_speed":       [cw.get("windspeed", 0)],
        "wind_direction":   [cw.get("winddirection", 0)],
    }
    om["current"] = cw
    om["strong_wind"] = cw.get("windspeed", 0) > 30
    om["fog_alert"]   = cw["weathercode"] in (45, 48)
    return om
