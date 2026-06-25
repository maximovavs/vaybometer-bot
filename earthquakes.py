#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Cyprus 24h earthquake summary via USGS Earthquake Catalog API.

The module is deliberately factual and production-safe: network/source errors
return None so post generation can skip the line and continue publishing.
"""
from __future__ import annotations

import math
import os
from typing import Any, Dict, List, Optional

import pendulum
import requests

USGS_EARTHQUAKE_QUERY_URL = "https://earthquake.usgs.gov/fdsnws/event/1/query"

CY_CENTER_LAT = 35.0
CY_CENTER_LON = 33.2

CY_CITY_COORDS = {
    "Лимассол": (34.707, 33.022),
    "Ларнака": (34.916, 33.624),
    "Никосия": (35.170, 33.360),
    "Пафос": (34.776, 32.424),
    "Айя-Напа": (34.988, 34.012),
}

CY_CITY_GENITIVE = {
    "Лимассол": "Лимассола",
    "Ларнака": "Ларнаки",
    "Никосия": "Никосии",
    "Пафос": "Пафоса",
    "Айя-Напа": "Айя-Напы",
}

REQUEST_TIMEOUT = float(os.getenv("HTTP_TIMEOUT", "10"))

__all__ = (
    "get_recent_earthquakes_cyprus",
    "build_cyprus_quake_line",
)


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    radius = 6371.0
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * radius * math.asin(math.sqrt(a))


def _nearest_city(lat: float, lon: float) -> tuple[Optional[str], Optional[float]]:
    best_name: Optional[str] = None
    best_dist: Optional[float] = None
    for name, (city_lat, city_lon) in CY_CITY_COORDS.items():
        dist = _haversine_km(lat, lon, city_lat, city_lon)
        if best_dist is None or dist < best_dist:
            best_name = name
            best_dist = dist
    return best_name, best_dist


def _normalize_event(feature: Dict[str, Any], tz: str = "Asia/Nicosia") -> Optional[Dict[str, Any]]:
    try:
        props = feature.get("properties") or {}
        geom = feature.get("geometry") or {}
        coords = geom.get("coordinates") or []
        if len(coords) < 2:
            return None
        lon = float(coords[0])
        lat = float(coords[1])
        depth_km = float(coords[2]) if len(coords) > 2 and coords[2] is not None else None
        mag = float(props.get("mag"))
        ts_ms = int(props.get("time"))
        time_utc = pendulum.from_timestamp(ts_ms / 1000, tz="UTC")
        time_local = time_utc.in_timezone(tz)
        nearest_name, nearest_dist = _nearest_city(lat, lon)
        return {
            "mag": mag,
            "place": str(props.get("place") or ""),
            "time_utc": time_utc.to_iso8601_string(),
            "time_local": time_local.to_iso8601_string(),
            "depth_km": depth_km,
            "lat": lat,
            "lon": lon,
            "distance_km": float(nearest_dist) if nearest_dist is not None else None,
            "nearest_city": nearest_name,
            "url": str(props.get("url") or ""),
        }
    except Exception:
        return None


def get_recent_earthquakes_cyprus(
    hours: int = 24,
    radius_km: float = 350,
    min_mag: float = 2.5,
) -> Optional[List[Dict[str, Any]]]:
    """Return normalized USGS earthquake events near Cyprus, or None on source failure."""
    try:
        now = pendulum.now("UTC")
        start = now.subtract(hours=int(hours))
        params = {
            "format": "geojson",
            "starttime": start.to_iso8601_string(),
            "endtime": now.to_iso8601_string(),
            "latitude": CY_CENTER_LAT,
            "longitude": CY_CENTER_LON,
            "maxradiuskm": float(radius_km),
            "minmagnitude": float(min_mag),
            "eventtype": "earthquake",
            "orderby": "time",
        }
        resp = requests.get(USGS_EARTHQUAKE_QUERY_URL, params=params, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        payload = resp.json()
        features = payload.get("features")
        if not isinstance(features, list):
            return None
        events: List[Dict[str, Any]] = []
        for feature in features:
            if not isinstance(feature, dict):
                continue
            normalized = _normalize_event(feature)
            if normalized is not None:
                events.append(normalized)
        return sorted(events, key=lambda item: float(item.get("mag") or 0), reverse=True)
    except Exception:
        return None


def _event_word(count: int) -> str:
    if count % 10 == 1 and count % 100 != 11:
        return "событие"
    if count % 10 in (2, 3, 4) and count % 100 not in (12, 13, 14):
        return "события"
    return "событий"


def _format_mag(mag: Any) -> str:
    try:
        return f"M{float(mag):.1f}"
    except Exception:
        return "Mн/д"


def _format_local_time(event: Dict[str, Any], tz: str) -> str:
    try:
        return pendulum.parse(str(event.get("time_local"))).in_timezone(tz).format("HH:mm")
    except Exception:
        return ""


def _city_genitive(city: Any) -> str:
    name = str(city or "").strip()
    return CY_CITY_GENITIVE.get(name, name or "Кипра")


def build_cyprus_quake_line(events: Optional[List[Dict[str, Any]]], tz: str = "Asia/Nicosia") -> str:
    """Build a compact factual Telegram line for last-24h seismicity."""
    if not events:
        return "🌍 Сейсмика 24ч: спокойно — заметных землетрясений рядом с Кипром не было."

    strongest = max(events, key=lambda item: float(item.get("mag") or 0))
    mag = float(strongest.get("mag") or 0)
    city = _city_genitive(strongest.get("nearest_city"))
    dist = strongest.get("distance_km")
    dist_txt = f"{int(round(float(dist)))} км" if isinstance(dist, (int, float)) else "рядом"
    time_txt = _format_local_time(strongest, tz)
    time_part = f", {time_txt}" if time_txt else ""

    if mag >= 4.0:
        depth = strongest.get("depth_km")
        depth_part = f", глубина {int(round(float(depth)))} км" if isinstance(depth, (int, float)) else ""
        return f"🌍 Сейсмика 24ч: ⚠️ {_format_mag(mag)} у {city}, {dist_txt}{depth_part}."

    count = len(events)
    return (
        f"🌍 Сейсмика 24ч: {count} {_event_word(count)}; "
        f"сильнейшее {_format_mag(mag)}, {dist_txt} от {city}{time_part}."
    )
