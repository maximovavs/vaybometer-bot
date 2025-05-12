#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
from typing import Optional, Dict, Any
from utils import _get
import logging

OWM_KEY = os.getenv("OWM_KEY")

def get_weather(lat: float, lon: float) -> Optional[Dict[str,Any]]:
    # 1️⃣  OpenWeather One Call
    if OWM_KEY:
        for ver in ("3.0","2.5"):
            ow = _get(
                f"https://api.openweathermap.org/data/{ver}/onecall",
                lat=lat, lon=lon, appid=OWM_KEY,
                units="metric", exclude="minutely,hourly,alerts"
            )
            if ow and "current" in ow:
                cur = ow["current"]
                # унификация
                ow["hourly"] = {
                    "surface_pressure": [cur.get("pressure",1013)],
                    "cloud_cover":      [cur.get("clouds",0)],
                    "weathercode":      [cur.get("weather",[{}])[0].get("id",0)],
                    "wind_speed":       [cur.get("wind_speed",0)],
                    "wind_direction":   [cur.get("wind_deg",0)],
                }
                ow["strong_wind"] = cur.get("wind_speed",0)*3.6 > 30
                ow["fog_alert"]   = False
                return ow

    # 2️⃣  Open-Meteo полный
    om = _get(
        "https://api.open-meteo.com/v1/forecast",
        latitude=lat, longitude=lon, timezone="UTC",
        current_weather="true", forecast_days=2,
        daily="temperature_2m_max,temperature_2m_min,weathercode",
        hourly="surface_pressure,cloud_cover,weathercode,wind_speed,wind_direction"
    )
    if om and "current_weather" in om:
        cur = om["current_weather"]
        cur["pressure"] = om["hourly"]["surface_pressure"][0]
        cur["clouds"]   = om["hourly"]["cloud_cover"][0]
        speed = cur.get("windspeed",0)
        code  = om["daily"]["weathercode"][0]
        om["strong_wind"] = speed > 30
        om["fog_alert"]   = code in (45,48)
        return om

    # 3️⃣  Open-Meteo fallback
    om = _get(
        "https://api.open-meteo.com/v1/forecast",
        latitude=lat, longitude=lon, timezone="UTC",
        current_weather="true"
    )
    if not om or "current_weather" not in om:
        return None

    cw = om["current_weather"]
    # эмуляция daily/hourly
    om["daily"] = [{
        "temperature_2m_max": [cw["temperature"]],
        "temperature_2m_min": [cw["temperature"]],
        "weathercode":        [cw["weathercode"]],
    }]
    om["hourly"] = {
        "surface_pressure": [cw.get("pressure",1013)],
        "cloud_cover":      [cw.get("clouds",   0)],
        "weathercode":      [cw["weathercode"]],
        "wind_speed":       [cw.get("windspeed",0)],
        "wind_direction":   [cw.get("winddirection",0)],
    }
    speed = cw.get("windspeed",0)
    om["strong_wind"] = speed > 30
    om["fog_alert"]   = cw["weathercode"] in (45,48)
    return om
