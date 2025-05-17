#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
weather.py  • унифицирует 3 источника прогноза

1. OpenWeather One Call (v3 → v2)                 – при наличии OWM_KEY
2. Open-Meteo (start_date / end_date = сегодня+завтра)
3. Фоллбэк — Open-Meteо «current_weather»          – всегда работает

‣ возвращаемая структура совместима с прошлым кодом post.py
"""

from __future__ import annotations
from typing import Any, Dict, Optional
import os, pendulum

from utils import _get

OWM_KEY = os.getenv("OWM_KEY")          # ваш ключ OpenWeather (может быть None)


# ────────────────────────────────────────────────────────────────────
def _openweather(lat: float, lon: float) -> Optional[Dict[str, Any]]:
    if not OWM_KEY:
        return None

    for ver in ("3.0", "2.5"):
        ow = _get(
            f"https://api.openweathermap.org/data/{ver}/onecall",
            lat=lat, lon=lon,       appid=OWM_KEY, units="metric",
            exclude="minutely,hourly,alerts",
        )
        if not ow or "current" not in ow:
            continue

        cur = ow["current"]

        # унифицируем поле current под «open-meteo стиль»
        ow["current"] = {
            "temperature":   cur.get("temp"),
            "pressure":      cur.get("pressure"),
            "clouds":        cur.get("clouds"),
            "windspeed":     cur.get("wind_speed") * 3.6,    # м/с → км/ч
            "winddirection": cur.get("wind_deg"),
            "weathercode":   cur.get("weather", [{}])[0].get("id", 0),
        }

        # подделываем минимальный hourly-массив
        ow["hourly"] = {
            "surface_pressure": [cur.get("pressure")],
            "cloud_cover":      [cur.get("clouds")],
            "weathercode":      [ow["current"]["weathercode"]],
            "wind_speed_10m":   [ow["current"]["windspeed"]],
            "wind_direction_10m":[ow["current"]["winddirection"]],
        }

        # daily привязан к сегодняшнему дню (нам нужно только max/min)
        ow["daily"] = {
            "time":                [],
            "temperature_2m_max":  [cur.get("temp")],
            "temperature_2m_min":  [cur.get("temp")],
            "weathercode":         [ow["current"]["weathercode"]],
        }

        ow["strong_wind"] = ow["current"]["windspeed"] > 30
        ow["fog_alert"]   = False
        return ow

    return None


# ────────────────────────────────────────────────────────────────────
def _openmeteo(lat: float, lon: float) -> Optional[Dict[str, Any]]:
    today    = pendulum.today().to_date_string()          # YYYY-MM-DD
    tomorrow = pendulum.today().add(days=1).to_date_string()

    om = _get(
        "https://api.open-meteo.com/v1/forecast",
        latitude  = lat,
        longitude = lon,
        timezone  = "UTC",                    # «UTC» надёжнее, чем локальные зоны
        start_date= today,
        end_date  = tomorrow,
        current_weather = "true",
        daily  = "temperature_2m_max,temperature_2m_min,weathercode",
        hourly = "surface_pressure,cloud_cover,weathercode,"
                 "wind_speed_10m,wind_direction_10m",
    )
    if not om or "current_weather" not in om or "daily" not in om:
        return None

    cur = om["current_weather"]

    # добавляем недостающие поля в current
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

    # флаги
    om["strong_wind"] = cur["windspeed"] > 30
    code_day          = om["daily"]["weathercode"][0]
    om["fog_alert"]   = code_day in (45, 48)

    return om


# ────────────────────────────────────────────────────────────────────
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

    # эмулируем daily/hourly
    om["daily"] = {
        "temperature_2m_max": [cw["temperature"]],
        "temperature_2m_min": [cw["temperature"]],
        "weathercode":        [cw.get("weathercode", 0)],
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


# ────────────────────────────────────────────────────────────────────
def get_weather(lat: float, lon: float) -> Optional[Dict[str, Any]]:
    """
    Пытается вернуть прогноз из нескольких источников.
    При первом удачном ответе сразу возвращает данные.
    """
    for fn in (_openweather, _openmeteo, _openmeteo_current_only):
        data = fn(lat, lon)
        if data:
            return data
    return None
