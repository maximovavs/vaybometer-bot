import requests
import logging
from typing import Optional, Dict, Any

# сюда подтягиваем ваши константы из utils, но для самодостаточности продублируем HEADERS и OWM_KEY
HEADERS = {"User-Agent": "VayboMeter/5.4"}
OWM_KEY = None  # подставьте os.getenv("OWM_KEY") при необходимости

def _get(url: str, **params) -> Optional[dict]:
    try:
        r = requests.get(url, params=params, timeout=15, headers=HEADERS)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        host = url.split("/")[2]
        logging.warning("%s – %s", host, e)
        return None

def get_weather(lat: float, lon: float) -> Optional[Dict[str, Any]]:
    """
    Возвращает словарь с ключами:
      • current_weather  (pressure, clouds, windspeed, winddirection)
      • daily            (temperature_2m_max, temperature_2m_min, weathercode)
      • hourly           (surface_pressure, cloud_cover, wind_speed, wind_direction)
      • strong_wind      bool
      • fog_alert        bool
    """
    # 1) OpenWeather OneCall
    if OWM_KEY:
        for ver in ("3.0", "2.5"):
            ow = _get(
                f"https://api.openweathermap.org/data/{ver}/onecall",
                lat=lat, lon=lon, appid=OWM_KEY,
                units="metric", exclude="minutely,hourly,alerts",
            )
            if ow and "current" in ow:
                cur = ow["current"]
                ow["current_weather"] = {
                    "pressure": cur.get("pressure"),
                    "clouds": cur.get("clouds"),
                    "windspeed": cur.get("wind_speed"),
                    "winddirection": cur.get("wind_deg"),
                }
                ow["hourly"] = {
                    "surface_pressure": [cur.get("pressure")],
                    "cloud_cover":      [cur.get("clouds")],
                    "weathercode":      [cur.get("weather", [{}])[0].get("id", 0)],
                    "wind_speed":       [cur.get("wind_speed")],
                    "wind_direction":   [cur.get("wind_deg")],
                }
                speed_kmh = cur.get("wind_speed", 0) * 3.6
                ow["strong_wind"] = speed_kmh > 30
                ow["fog_alert"]   = False
                return ow

    # 2) Open-Meteo full
    om = _get(
        "https://api.open-meteo.com/v1/forecast",
        latitude=lat, longitude=lon, timezone="UTC",
        forecast_days=2, current_weather="true",
        daily="temperature_2m_max,temperature_2m_min,weathercode",
        hourly="surface_pressure,cloud_cover,weathercode,wind_speed,wind_direction",
    )
    if om and "current_weather" in om:
        cur = om["current_weather"]
        cur["pressure"] = om["hourly"]["surface_pressure"][0]
        cur["clouds"]   = om["hourly"]["cloud_cover"][0]
        speed_kmh       = cur.get("windspeed", 0)
        om["strong_wind"] = speed_kmh > 30
        day_code        = om["daily"]["weathercode"][0]
        om["fog_alert"]   = day_code in (45, 48)
        return om

    # 3) Open-Meteo fallback (только current_weather)
    om = _get(
        "https://api.open-meteo.com/v1/forecast",
        latitude=lat, longitude=lon, timezone="UTC",
        current_weather="true",
    )
    if not (om and "current_weather" in om):
        return None
    cw = om["current_weather"]
    om["daily"] = [{
        "temperature_2m_max": [cw["temperature"]],
        "temperature_2m_min": [cw["temperature"]],
        "weathercode":        [cw["weathercode"]],
    }]
    om["hourly"] = {
        "surface_pressure": [cw.get("pressure", 1013)],
        "cloud_cover":      [cw.get("clouds",   0   )],
        "weathercode":      [cw["weathercode"]],
        "wind_speed":       [cw.get("windspeed", 0)],
        "wind_direction":   [cw.get("winddirection", 0)],
    }
    speed_kmh        = cw.get("windspeed", 0)
    om["strong_wind"] = speed_kmh > 30
    om["fog_alert"]   = cw["weathercode"] in (45, 48)
    return om
