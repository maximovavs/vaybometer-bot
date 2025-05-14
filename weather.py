#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
from typing import Optional, Dict, Any

from utils import _get

OWM_KEY = os.getenv("OWM_KEY")


def get_weather(lat: float, lon: float) -> Optional[Dict[str, Any]]:
    """
    Возвращает прогноз для текущего и завтрашнего дня
    в единой структуре, независимо от источника.
    """

    # ─── 1. OpenWeather One Call v3.0 → v2.5 ────────────────────────
    if OWM_KEY:
        for ver in ("3.0", "2.5"):
            ow = _get(
                f"https://api.openweathermap.org/data/{ver}/onecall",
                lat=lat, lon=lon, appid=OWM_KEY,
                units="metric", exclude="minutely,hourly,alerts",
            )
            if ow and "current" in ow:
                cur = ow["current"]
                # unify current
                unified_current = {
                    "temperature":   cur.get("temp"),
                    "pressure":      cur.get("pressure"),
                    "clouds":        cur.get("clouds"),
                    "windspeed":     cur.get("wind_speed", 0) * 3.6,
                    "winddirection": cur.get("wind_deg"),
                    "weathercode":   cur.get("weather", [{}])[0].get("id", 0),
                }
                # unify daily → список dict с массивами [today, tomorrow]
                daily_list = []
                for day in ow.get("daily", [])[:2]:
                    temps = day.get("temp", {})
                    daily_list.append({
                        "temperature_2m_max": [temps.get("max")],
                        "temperature_2m_min": [temps.get("min")],
                        "weathercode":        [day.get("weather", [{}])[0].get("id", 0)],
                    })
                # hourly-emulation (только для current)
                hourly = {
                    "time":             [],  # нет точных меток
                    "temperature_2m":   [cur.get("temp")],
                    "surface_pressure": [cur.get("pressure")],
                    "cloud_cover":      [cur.get("clouds")],
                    "weathercode":      [cur.get("weather", [{}])[0].get("id", 0)],
                    "wind_speed":       [cur.get("wind_speed", 0) * 3.6],
                    "wind_direction":   [cur.get("wind_deg")],
                }
                # флаги для завтра (тот же элемент индекса 1, если есть)
                speed = unified_current["windspeed"]
                strong = speed > 30
                # OpenWeather не отдаёт коды 45/48, тумана не ловим
                return {
                    "current":     unified_current,
                    "daily":       daily_list,
                    "hourly":      hourly,
                    "strong_wind": strong,
                    "fog_alert":   False,
                }

    # ─── 2. Open-Meteo full (daily + hourly) ───────────────────────
    om = _get(
        "https://api.open-meteo.com/v1/forecast",
        latitude=lat, longitude=lon, timezone="UTC",
        current_weather="true", forecast_days=2,
        daily="temperature_2m_max,temperature_2m_min,weathercode",
        hourly="time,surface_pressure,cloud_cover,"
               "weathercode,wind_speed,wind_direction,temperature_2m",
    )
    if om and "current_weather" in om and "daily" in om and "hourly" in om:
        cur = om["current_weather"]
        unified_current = {
            "temperature":   cur.get("temperature"),
            "pressure":      om["hourly"]["surface_pressure"][0],
            "clouds":        om["hourly"]["cloud_cover"][0],
            "windspeed":     cur.get("windspeed", 0),
            "winddirection": cur.get("winddirection", 0),
            "weathercode":   cur.get("weathercode", 0),
        }
        # daily из dict → забираем массивы [today, tomorrow]
        daily = om["daily"]
        daily_list = [{
            "temperature_2m_max": daily["temperature_2m_max"],
            "temperature_2m_min": daily["temperature_2m_min"],
            "weathercode":        daily["weathercode"],
        }]
        # hourly уже содержит списки на каждый час
        hourly = om["hourly"]
        speed = unified_current["windspeed"]
        code_day = (
            daily["weathercode"][1]
            if len(daily["weathercode"]) > 1
            else daily["weathercode"][0]
        )
        return {
            "current":     unified_current,
            "daily":       daily_list,
            "hourly":      hourly,
            "strong_wind": speed > 30,
            "fog_alert":   code_day in (45, 48),
        }

    # ─── 3. Fallback Open-Meteo (только current_weather) ──────────
    om = _get(
        "https://api.open-meteo.com/v1/forecast",
        latitude=lat, longitude=lon, timezone="UTC",
        current_weather="true",
    )
    if not om or "current_weather" not in om:
        return None
    cw = om["current_weather"]
    unified_current = {
        "temperature":   cw.get("temperature"),
        "pressure":      cw.get("pressure", 1013),
        "clouds":        cw.get("clouds", 0),
        "windspeed":     cw.get("windspeed", 0),
        "winddirection": cw.get("winddirection", 0),
        "weathercode":   cw.get("weathercode", 0),
    }
    # эмуляция daily + hourly из одного значения
    daily_list = [{
        "temperature_2m_max": [cw.get("temperature")],
        "temperature_2m_min": [cw.get("temperature")],
        "weathercode":        [cw.get("weathercode")],
    }]
    hourly = {
        "time":             [],
        "temperature_2m":   [cw.get("temperature")],
        "surface_pressure": [cw.get("pressure", 1013)],
        "cloud_cover":      [cw.get("clouds", 0)],
        "weathercode":      [cw.get("weathercode", 0)],
        "wind_speed":       [cw.get("windspeed", 0)],
        "wind_direction":   [cw.get("winddirection", 0)],
    }
    speed = unified_current["windspeed"]
    return {
        "current":     unified_current,
        "daily":       daily_list,
        "hourly":      hourly,
        "strong_wind": speed > 30,
        "fog_alert":   cw.get("weathercode", 0) in (45, 48),
    }
