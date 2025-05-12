#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
from typing import Optional
from utils import _get, aqi_color
import logging

AIR_KEY  = os.getenv("AIRVISUAL_KEY")
AMBEE_KEY= os.getenv("TOMORROW_KEY")

AQI_BANDS = (
    (0,50), (51,100),(101,150),(151,200),(201,300),(301,500)
)

def get_air() -> Optional[dict]:
    if not AIR_KEY:
        return None
    j = _get("https://api.airvisual.com/v2/nearest_city",
             lat=LAT, lon=LON, key=AIR_KEY)
    if not j:
        return None
    pol = j["data"]["current"]["pollution"]
    aqi = pol.get("aqius")
    return {
        "aqi":   aqi,
        "lvl":   aqi_color(aqi),
        "pm25":  pol.get("p2"),
        "pm10":  pol.get("p1"),
    }

def get_pollen() -> Optional[dict]:
    if not AMBEE_KEY:
        return None
    d = _get("https://api.tomorrow.io/v4/timelines",
             apikey=AMBEE_KEY,
             location=f"{LAT},{LON}",
             fields="treeIndex,grassIndex,weedIndex",
             timesteps="1d", units="metric")
    try:
        return d["data"]["timelines"][0]["intervals"][0]["values"]
    except Exception as e:
        logging.warning("Pollen: %s", e)
        return None

def get_sst() -> Optional[float]:
    j = _get("https://marine-api.open-meteo.com/v1/marine",
             latitude=LAT, longitude=LON,
             hourly="sea_surface_temperature",
             timezone="UTC")
    try:
        return round(j["hourly"]["sea_surface_temperature"][0],1)
    except:
        return None

def get_kp() -> tuple[Optional[float],str]:
    j = _get("https://services.swpc.noaa.gov/products/noaa-planetary-k-index.json")
    try:
        kp = float(j[-1][1])
    except:
        return None, "н/д"
    state = "спокойный" if kp<4 else "повышенный" if kp<5 else "буря"
    return kp, state
