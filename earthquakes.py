#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Cyprus local earthquake summary from regional and USGS catalogs."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
import logging
import math
import os
from typing import Any, Dict, Iterable, List, Optional

import requests


EMSC_EVENT_QUERY_URL = "https://www.seismicportal.eu/fdsnws/event/1/query"
USGS_EARTHQUAKE_QUERY_URL = "https://earthquake.usgs.gov/fdsnws/event/1/query"

CY_CENTER_LAT = 35.0
CY_CENTER_LON = 33.2
DEFAULT_CY_QUAKE_MIN_MAG = 0.9
DEFAULT_CY_QUAKE_RADIUS_KM = 350.0
DEFAULT_CY_QUAKE_HOURS = 24

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

CY_AREA_ALIASES = (
    ("Акротири", ("akrotiri", "акротири")),
    ("Лимассол", ("limassol", "лемесос", "лимассол")),
    ("Пафос", ("paphos", "pafos", "пафос")),
    ("Ларнака", ("larnaca", "ларнака")),
    ("Никосия", ("nicosia", "никосия")),
    ("Айя-Напа", ("ayia napa", "ayia-napa", "айя-напа", "айя напа")),
)

REQUEST_TIMEOUT = float(os.getenv("HTTP_TIMEOUT", "10"))
_USER_AGENT = "VayboMeterBot/1.0 (+https://github.com/maximovavs/vaybometer-bot)"
_NON_EARTHQUAKE_REJECT = (
    "quarry",
    "blast",
    "explosion",
    "chemical",
    "mine collapse",
    "sonic boom",
    "not existing",
    "not reported",
)

__all__ = (
    "DEFAULT_CY_QUAKE_HOURS",
    "DEFAULT_CY_QUAKE_MIN_MAG",
    "DEFAULT_CY_QUAKE_RADIUS_KM",
    "CyprusQuakeEvents",
    "build_cyprus_quake_line",
    "deduplicate_events",
    "fetch_regional_events",
    "fetch_usgs_events",
    "get_recent_earthquakes_cyprus",
)


class CyprusQuakeEvents(list):
    """List of normalized events with source-health metadata."""

    def __init__(
        self,
        events: Iterable[Dict[str, Any]] = (),
        *,
        min_mag: float = DEFAULT_CY_QUAKE_MIN_MAG,
        hours: int = DEFAULT_CY_QUAKE_HOURS,
        radius_km: float = DEFAULT_CY_QUAKE_RADIUS_KM,
        source_status: Optional[Dict[str, Dict[str, Any]]] = None,
    ) -> None:
        super().__init__(events)
        self.min_mag = float(min_mag)
        self.hours = int(hours)
        self.radius_km = float(radius_km)
        self.source_status = source_status or {}


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _iso_z(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _parse_time(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        timestamp = float(value)
        if timestamp > 10_000_000_000:
            timestamp /= 1000.0
        return datetime.fromtimestamp(timestamp, tz=timezone.utc)
    text = str(value).strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    radius = 6371.0
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * radius * math.asin(math.sqrt(a))


def _bbox_for_radius(radius_km: float) -> Dict[str, float]:
    lat_delta = radius_km / 111.0
    lon_delta = radius_km / (111.0 * max(0.2, math.cos(math.radians(CY_CENTER_LAT))))
    return {
        "minlatitude": CY_CENTER_LAT - lat_delta,
        "maxlatitude": CY_CENTER_LAT + lat_delta,
        "minlongitude": CY_CENTER_LON - lon_delta,
        "maxlongitude": CY_CENTER_LON + lon_delta,
    }


def _nearest_city(lat: float, lon: float) -> tuple[Optional[str], Optional[float]]:
    best_name: Optional[str] = None
    best_dist: Optional[float] = None
    for name, (city_lat, city_lon) in CY_CITY_COORDS.items():
        dist = _haversine_km(lat, lon, city_lat, city_lon)
        if best_dist is None or dist < best_dist:
            best_name = name
            best_dist = dist
    return best_name, best_dist


def _city_genitive(city: Any) -> str:
    name = str(city or "").strip()
    return CY_CITY_GENITIVE.get(name, name or "Кипра")


def _source_status(ok: bool, count: Optional[int] = None, error: str = "") -> Dict[str, Any]:
    return {"ok": ok, "count": count, "error": error}


def _event_type_text(props: Dict[str, Any]) -> str:
    parts = [
        props.get("type"),
        props.get("eventtype"),
        props.get("eventType"),
        props.get("evtype"),
        props.get("event_type"),
    ]
    return " ".join(str(part or "") for part in parts).lower()


def _is_earthquake_event(props: Dict[str, Any]) -> bool:
    text = _event_type_text(props)
    if any(token in text for token in _NON_EARTHQUAKE_REJECT):
        return False
    if not text:
        return True
    if "earthquake" in text or text in {"ke", "se"}:
        return True
    # EMSC uses compact event type codes; keep unknown seismic codes but reject
    # explicit non-earthquake wording above.
    return not any(token in text for token in ("qb", "ex", "expl"))


def _status_weight(event: Dict[str, Any]) -> int:
    status = str(event.get("status") or "").lower()
    if "review" in status or "manual" in status:
        return 3
    if "automatic" in status or "prelim" in status:
        return 1
    return 2


def _normalize_common(
    *,
    source: str,
    source_event_id: str,
    mag: Any,
    place: Any,
    time_value: Any,
    depth_km: Any,
    lat: Any,
    lon: Any,
    url: Any = "",
    status: Any = "",
    props: Optional[Dict[str, Any]] = None,
    tz: str = "Asia/Nicosia",
) -> Optional[Dict[str, Any]]:
    try:
        event_props = props or {}
        if not _is_earthquake_event(event_props):
            return None
        mag_value = float(mag)
        lat_value = float(lat)
        lon_value = float(lon)
        depth_value = float(depth_km) if depth_km is not None else None
        if depth_value is not None:
            depth_value = abs(depth_value)
        time_utc = _parse_time(time_value)
        if time_utc is None:
            return None
        distance = _haversine_km(CY_CENTER_LAT, CY_CENTER_LON, lat_value, lon_value)
        nearest_name, nearest_dist = _nearest_city(lat_value, lon_value)
        local = time_utc.astimezone(ZoneInfo(tz))
        return {
            "source": source,
            "sources": [source],
            "source_event_id": source_event_id,
            "mag": mag_value,
            "place": str(place or ""),
            "time_utc": _iso_z(time_utc),
            "time_local": local.isoformat(),
            "depth_km": depth_value,
            "lat": lat_value,
            "lon": lon_value,
            "distance_km": float(nearest_dist) if nearest_dist is not None else None,
            "distance_from_center_km": distance,
            "nearest_city": nearest_name,
            "url": str(url or ""),
            "status": str(status or event_props.get("status") or ""),
            "event_type": _event_type_text(event_props),
        }
    except Exception:
        return None


def _normalize_usgs_feature(feature: Dict[str, Any], tz: str = "Asia/Nicosia") -> Optional[Dict[str, Any]]:
    props = feature.get("properties") or {}
    geom = feature.get("geometry") or {}
    coords = geom.get("coordinates") or []
    if len(coords) < 2:
        return None
    return _normalize_common(
        source="USGS",
        source_event_id=str(feature.get("id") or props.get("ids") or ""),
        mag=props.get("mag"),
        place=props.get("place"),
        time_value=props.get("time"),
        depth_km=coords[2] if len(coords) > 2 else None,
        lat=coords[1],
        lon=coords[0],
        url=props.get("url"),
        status=props.get("status"),
        props=props,
        tz=tz,
    )


def _normalize_emsc_feature(feature: Dict[str, Any], tz: str = "Asia/Nicosia") -> Optional[Dict[str, Any]]:
    props = feature.get("properties") or {}
    geom = feature.get("geometry") or {}
    coords = geom.get("coordinates") or []
    lon = props.get("lon", coords[0] if len(coords) > 0 else None)
    lat = props.get("lat", coords[1] if len(coords) > 1 else None)
    depth = props.get("depth", coords[2] if len(coords) > 2 else None)
    return _normalize_common(
        source="EMSC",
        source_event_id=str(props.get("unid") or feature.get("id") or props.get("source_id") or ""),
        mag=props.get("mag"),
        place=props.get("flynn_region") or props.get("place") or props.get("region"),
        time_value=props.get("time"),
        depth_km=depth,
        lat=lat,
        lon=lon,
        url=f"https://www.emsc-csem.org/Earthquake/earthquake.php?id={props.get('source_id')}"
        if props.get("source_id")
        else "",
        status=props.get("status") or props.get("source_catalog"),
        props=props,
        tz=tz,
    )


def _filter_events(
    events: Iterable[Dict[str, Any]],
    *,
    min_mag: float,
    radius_km: float,
    hours: Optional[int] = None,
    now: Optional[datetime] = None,
) -> List[Dict[str, Any]]:
    result: List[Dict[str, Any]] = []
    current = now or _now_utc()
    start = current - timedelta(hours=int(hours)) if hours is not None else None
    for event in events:
        try:
            if float(event.get("mag") or 0) < float(min_mag):
                continue
            if float(event.get("distance_from_center_km") or 0) > float(radius_km):
                continue
            if start is not None:
                event_time = _parse_time(event.get("time_utc"))
                if event_time is None or event_time < start or event_time > current + timedelta(minutes=5):
                    continue
        except Exception:
            continue
        result.append(event)
    return result


def fetch_regional_events(
    *,
    hours: int = DEFAULT_CY_QUAKE_HOURS,
    radius_km: float = DEFAULT_CY_QUAKE_RADIUS_KM,
    min_mag: float = DEFAULT_CY_QUAKE_MIN_MAG,
    tz: str = "Asia/Nicosia",
) -> Optional[List[Dict[str, Any]]]:
    """Fetch regional EMSC/SeismicPortal events near Cyprus, or None on failure."""
    now = _now_utc()
    start = now - timedelta(hours=int(hours))
    params: Dict[str, Any] = {
        "format": "json",
        "starttime": _iso_z(start),
        "endtime": _iso_z(now),
        "minmagnitude": float(min_mag),
        "orderby": "time",
        "nodata": 204,
        **_bbox_for_radius(float(radius_km)),
    }
    try:
        resp = requests.get(
            EMSC_EVENT_QUERY_URL,
            params=params,
            timeout=REQUEST_TIMEOUT,
            headers={"User-Agent": _USER_AGENT},
        )
        if resp.status_code == 204:
            logging.info(
                "Cyprus seismic source=EMSC hours=%s radius=%s min_mag=%s status=204 count=0",
                hours,
                radius_km,
                min_mag,
            )
            return []
        resp.raise_for_status()
        payload = resp.json()
        features = payload.get("features")
        if not isinstance(features, list):
            logging.warning("Cyprus seismic source=EMSC invalid features payload")
            return None
        events = [
            normalized
            for feature in features
            if isinstance(feature, dict)
            for normalized in [_normalize_emsc_feature(feature, tz=tz)]
            if normalized is not None
        ]
        events = _filter_events(
            events,
            min_mag=float(min_mag),
            radius_km=float(radius_km),
            hours=int(hours),
            now=now,
        )
        logging.info(
            "Cyprus seismic source=EMSC hours=%s radius=%s min_mag=%s status=%s count=%s",
            hours,
            radius_km,
            min_mag,
            resp.status_code,
            len(events),
        )
        return events
    except Exception as exc:
        logging.warning(
            "Cyprus seismic source=EMSC failed hours=%s radius=%s min_mag=%s error=%s",
            hours,
            radius_km,
            min_mag,
            exc,
        )
        return None


def fetch_usgs_events(
    *,
    hours: int = DEFAULT_CY_QUAKE_HOURS,
    radius_km: float = DEFAULT_CY_QUAKE_RADIUS_KM,
    min_mag: float = DEFAULT_CY_QUAKE_MIN_MAG,
    tz: str = "Asia/Nicosia",
) -> Optional[List[Dict[str, Any]]]:
    """Fetch USGS events near Cyprus, or None on source failure."""
    now = _now_utc()
    start = now - timedelta(hours=int(hours))
    params = {
        "format": "geojson",
        "starttime": _iso_z(start),
        "endtime": _iso_z(now),
        "latitude": CY_CENTER_LAT,
        "longitude": CY_CENTER_LON,
        "maxradiuskm": float(radius_km),
        "minmagnitude": float(min_mag),
        "eventtype": "earthquake",
        "orderby": "time",
    }
    try:
        resp = requests.get(
            USGS_EARTHQUAKE_QUERY_URL,
            params=params,
            timeout=REQUEST_TIMEOUT,
            headers={"User-Agent": _USER_AGENT},
        )
        resp.raise_for_status()
        payload = resp.json()
        features = payload.get("features")
        if not isinstance(features, list):
            logging.warning("Cyprus seismic source=USGS invalid features payload")
            return None
        events = [
            normalized
            for feature in features
            if isinstance(feature, dict)
            for normalized in [_normalize_usgs_feature(feature, tz=tz)]
            if normalized is not None
        ]
        events = _filter_events(
            events,
            min_mag=float(min_mag),
            radius_km=float(radius_km),
            hours=int(hours),
            now=now,
        )
        logging.info(
            "Cyprus seismic source=USGS hours=%s radius=%s min_mag=%s status=%s count=%s",
            hours,
            radius_km,
            min_mag,
            resp.status_code,
            len(events),
        )
        return events
    except Exception as exc:
        logging.warning(
            "Cyprus seismic source=USGS failed hours=%s radius=%s min_mag=%s error=%s",
            hours,
            radius_km,
            min_mag,
            exc,
        )
        return None


def _event_time_seconds(event: Dict[str, Any]) -> float:
    parsed = _parse_time(event.get("time_utc"))
    return parsed.timestamp() if parsed else 0.0


def _events_duplicate(left: Dict[str, Any], right: Dict[str, Any]) -> bool:
    try:
        time_diff = abs(_event_time_seconds(left) - _event_time_seconds(right))
        distance = _haversine_km(
            float(left.get("lat")),
            float(left.get("lon")),
            float(right.get("lat")),
            float(right.get("lon")),
        )
        mag_diff = abs(float(left.get("mag")) - float(right.get("mag")))
        return time_diff <= 90 and distance <= 30 and mag_diff <= 0.5
    except Exception:
        return False


def _is_regional(event: Dict[str, Any]) -> bool:
    return str(event.get("source") or "").upper() in {"EMSC", "CYPRUS"}


def _merge_duplicate(base: Dict[str, Any], incoming: Dict[str, Any]) -> Dict[str, Any]:
    sources = list(dict.fromkeys([*base.get("sources", [base.get("source")]), *incoming.get("sources", [incoming.get("source")])]))
    prefer_incoming = False
    if _is_regional(incoming) and not _is_regional(base):
        prefer_incoming = True
    elif _status_weight(incoming) > _status_weight(base):
        prefer_incoming = True
    elif not base.get("depth_km") and incoming.get("depth_km") is not None:
        prefer_incoming = True
    elif not base.get("url") and incoming.get("url"):
        prefer_incoming = True

    merged = dict(incoming if prefer_incoming else base)
    other = base if prefer_incoming else incoming
    if not merged.get("depth_km") and other.get("depth_km") is not None:
        merged["depth_km"] = other.get("depth_km")
    if not merged.get("url") and other.get("url"):
        merged["url"] = other.get("url")
    merged["sources"] = [str(source) for source in sources if source]
    return merged


def deduplicate_events(events: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    deduped: List[Dict[str, Any]] = []
    for event in sorted(events, key=lambda item: _event_time_seconds(item), reverse=True):
        match_index = next(
            (index for index, existing in enumerate(deduped) if _events_duplicate(existing, event)),
            None,
        )
        if match_index is None:
            deduped.append(dict(event))
        else:
            deduped[match_index] = _merge_duplicate(deduped[match_index], event)
    return sorted(deduped, key=lambda item: float(item.get("mag") or 0), reverse=True)


def get_recent_earthquakes_cyprus(
    hours: int = DEFAULT_CY_QUAKE_HOURS,
    radius_km: float = DEFAULT_CY_QUAKE_RADIUS_KM,
    min_mag: float = DEFAULT_CY_QUAKE_MIN_MAG,
) -> Optional[CyprusQuakeEvents]:
    """Return normalized Cyprus-area events, or None when all sources fail."""
    regional = fetch_regional_events(hours=hours, radius_km=radius_km, min_mag=min_mag)
    usgs = fetch_usgs_events(hours=hours, radius_km=radius_km, min_mag=min_mag)
    source_status = {
        "regional": _source_status(regional is not None, len(regional) if regional is not None else None),
        "usgs": _source_status(usgs is not None, len(usgs) if usgs is not None else None),
    }
    if regional is None and usgs is None:
        return None
    merged = deduplicate_events([*(regional or []), *(usgs or [])])
    return CyprusQuakeEvents(
        merged,
        min_mag=min_mag,
        hours=hours,
        radius_km=radius_km,
        source_status=source_status,
    )


def _event_word(count: int) -> str:
    if count % 10 == 1 and count % 100 != 11:
        return "событие"
    if count % 10 in (2, 3, 4) and count % 100 not in (12, 13, 14):
        return "события"
    return "событий"


def _micro_word(count: int) -> str:
    if count % 10 == 1 and count % 100 != 11:
        return "микрособытие"
    if count % 10 in (2, 3, 4) and count % 100 not in (12, 13, 14):
        return "микрособытия"
    return "микрособытий"


def _weak_word(count: int) -> str:
    if count % 10 == 1 and count % 100 != 11:
        return "слабое событие"
    if count % 10 in (2, 3, 4) and count % 100 not in (12, 13, 14):
        return "слабых события"
    return "слабых событий"


def _format_mag(mag: Any) -> str:
    try:
        return f"M{float(mag):.1f}"
    except Exception:
        return "Mн/д"


def _threshold_text(value: float) -> str:
    return f"M{float(value):.1f}+"


def _local_time(event: Dict[str, Any], tz: str) -> str:
    parsed = _parse_time(event.get("time_local")) or _parse_time(event.get("time_utc"))
    if not parsed:
        return ""
    return parsed.astimezone(ZoneInfo(tz)).strftime("%H:%M")


def _area_from_place(place: str) -> Optional[str]:
    low = str(place or "").lower()
    for label, aliases in CY_AREA_ALIASES:
        if any(alias in low for alias in aliases):
            return label
    if "cyprus" in low or "кипр" in low:
        return "район Кипра"
    return None


def _area_label(event: Dict[str, Any]) -> Optional[str]:
    area = _area_from_place(str(event.get("place") or ""))
    if area:
        return area
    nearest = event.get("nearest_city")
    if nearest:
        return str(nearest)
    return None


def _weak_location_phrase(event: Dict[str, Any]) -> str:
    place = str(event.get("place") or "")
    area = _area_from_place(place)
    if area == "Акротири":
        return "в районе Акротири, рядом с Лимассолом"
    if area and area != "район Кипра":
        return f"в районе {area}"
    if area == "район Кипра":
        return "в районе Кипра"
    dist = event.get("distance_km")
    city = event.get("nearest_city")
    if isinstance(dist, (int, float)) and city:
        return f"{int(round(float(dist)))} км от {_city_genitive(city)}"
    if city:
        return f"рядом с {_city_genitive(city)}"
    return "рядом с Кипром"


def _strong_location_phrase(event: Dict[str, Any]) -> str:
    dist = event.get("distance_km")
    city = event.get("nearest_city")
    if isinstance(dist, (int, float)) and city:
        return f"{int(round(float(dist)))} км от {_city_genitive(city)}"
    area = _area_from_place(str(event.get("place") or ""))
    if area == "Акротири":
        return "в районе Акротири, рядом с Лимассолом"
    if area and area != "район Кипра":
        return f"в районе {area}"
    return "рядом с Кипром"


def _warning_location(event: Dict[str, Any]) -> tuple[str, str]:
    dist = event.get("distance_km")
    city = event.get("nearest_city")
    city_txt = _city_genitive(city)
    dist_txt = f"{int(round(float(dist)))} км" if isinstance(dist, (int, float)) else "рядом"
    return city_txt, dist_txt


def _majority_area(events: List[Dict[str, Any]]) -> Optional[str]:
    counts: Dict[str, int] = {}
    for event in events:
        label = _area_label(event)
        if not label:
            continue
        counts[label] = counts.get(label, 0) + 1
    if not counts:
        return None
    label, count = max(counts.items(), key=lambda item: item[1])
    if count > len(events) / 2 and label != "район Кипра":
        return label
    return None


def _regional_failed_usgs_succeeded(events: Any) -> bool:
    status = getattr(events, "source_status", {}) or {}
    regional_ok = bool((status.get("regional") or {}).get("ok"))
    usgs_ok = bool((status.get("usgs") or {}).get("ok"))
    return not regional_ok and usgs_ok


def build_cyprus_quake_line(
    events: Optional[List[Dict[str, Any]]],
    tz: str = "Asia/Nicosia",
    *,
    min_mag: float = DEFAULT_CY_QUAKE_MIN_MAG,
) -> str:
    """Build a compact factual Telegram line for last-24h Cyprus seismicity."""
    if events is None:
        return "🌍 Сейсмика: данные временно не обновились."

    threshold = float(getattr(events, "min_mag", min_mag))
    if _regional_failed_usgs_succeeded(events):
        if events:
            strongest = max(events, key=lambda item: float(item.get("mag") or 0))
            usgs_part = f" По USGS: сильнейшее {_format_mag(strongest.get('mag'))}, {_strong_location_phrase(strongest)}."
        else:
            usgs_part = " По каталогу USGS событий M2.5+ за 24 часа не найдено."
        return "🌍 Сейсмика: региональные данные по слабым событиям временно не обновились." + usgs_part

    if not events:
        return (
            "🌍 Сейсмика 24ч: по доступным региональным каталогам событий "
            f"{_threshold_text(threshold)} рядом с Кипром не найдено."
        )

    clean_events = [event for event in events if float(event.get("mag") or 0) >= threshold]
    if not clean_events:
        return (
            "🌍 Сейсмика 24ч: по доступным региональным каталогам событий "
            f"{_threshold_text(threshold)} рядом с Кипром не найдено."
        )

    micro = [event for event in clean_events if 0.9 <= float(event.get("mag") or 0) < 2.0]
    weak = [event for event in clean_events if 2.0 <= float(event.get("mag") or 0) < 3.0]
    m3 = [event for event in clean_events if 3.0 <= float(event.get("mag") or 0) < 4.0]
    m4 = [event for event in clean_events if float(event.get("mag") or 0) >= 4.0]
    strongest = max(clean_events, key=lambda item: float(item.get("mag") or 0))
    strongest_mag = float(strongest.get("mag") or 0)

    if strongest_mag >= 4.0:
        city, dist_txt = _warning_location(strongest)
        depth = strongest.get("depth_km")
        depth_part = f", глубина {int(round(float(depth)))} км" if isinstance(depth, (int, float)) else ""
        return f"🌍 Сейсмика 24ч: ⚠️ {_format_mag(strongest_mag)} у {city}, {dist_txt}{depth_part}."

    if strongest_mag >= 3.0:
        return (
            f"🌍 Сейсмика 24ч: сильнейшее событие {_format_mag(strongest_mag)}, "
            f"{_strong_location_phrase(strongest)}."
        )

    if weak:
        parts: List[str] = []
        if micro:
            parts.append(f"{len(micro)} {_micro_word(len(micro))}")
        parts.append(f"{len(weak)} {_weak_word(len(weak))}")
        return (
            "🌍 Сейсмика 24ч: "
            + " и ".join(parts)
            + f"; сильнейшее {_format_mag(strongest_mag)} {_weak_location_phrase(strongest)}."
        )

    micro_count = len(micro)
    line = (
        f"🌍 Сейсмика 24ч: {micro_count} {_micro_word(micro_count)} "
        "M0.9–1.9; заметных событий M2.0+ не найдено."
    )
    area = _majority_area(micro)
    if area:
        line += f" Большинство — в районе {area}."
    return line
