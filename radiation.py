#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
radiation.py — единый интерфейс для постера.

Контракт:
  get_radiation(lat, lon) -> {
      "value": <µSv/h float>,     # основное поле
      "cpm": <float|None>,        # если источник даёт CPM
      "source": "radmon|eurdep|safecast-cache|cache",
      "trend": "↑|→|↓",           # если есть история
      "cached": bool              # True для локальных fallback-данных
  } | None

Логика:
  1) Живые источники: radmon → EURDEP
  2) Fallback: локальные файлы safecast_* → radiation_hourly.json
"""

from __future__ import annotations
import json, time, math, logging, pathlib
from typing import Dict, Any, Optional, Tuple, List

import requests

# ───────────────────────── настройки ─────────────────────────
TIMEOUT = 15
CACHE_HOURLY = pathlib.Path(__file__).parent / "radiation_hourly.json"
SAFECAST_CANDIDATES = [
    pathlib.Path(__file__).parent / "safecast_radiation.json",
    pathlib.Path(__file__).parent / "data" / "safecast_kaliningrad.json",
    pathlib.Path(__file__).parent / "data" / "safecast_cyprus.json",
]

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

# ─────────────────────── утилиты ─────────────────────────────
def _haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R, dLat, dLon = 6371.0, math.radians(lat2-lat1), math.radians(lon2-lon1)
    a = math.sin(dLat/2)**2 + math.cos(math.radians(lat1))*math.cos(math.radians(lat2))*math.sin(dLon/2)**2
    return 2*R*math.asin(math.sqrt(a))

def _load_json(path: pathlib.Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None

# ─────────────────── живые источники ─────────────────────────
def _try_radmon(lat: float, lon: float) -> Optional[Tuple[float, Optional[float]]]:
    """
    Возвращает (µSv/h, cpm) или None.
    Конверсия CPM → µSv/h по простой константе ~0.0065.
    """
    try:
        r = requests.get("https://radmon.org/radmon.php?format=json", timeout=TIMEOUT)
        r.raise_for_status()
        j = r.json()
        best, dmin = None, 1e9
        now = time.time()
        for p in j.get("users", []):
            try:
                la, lo = float(p["lat"]), float(p["lon"])
                dx = _haversine(lat, lon, la, lo)
                # активный не старше 3ч и ближе 100 км
                if dx < 100 and (now - float(p.get("last_seen", 0)) < 3*3600):
                    if dx < dmin:
                        best, dmin = p, dx
            except Exception:
                continue
        if best:
            cpm = float(best.get("cpm_avg") or best.get("cpm") or 0.0)
            usvh = cpm * 0.0065
            return (usvh, cpm)
    except Exception as e:
        logging.info("radmon err: %s", e)
    return None

def _try_eurdep(lat: float, lon: float) -> Optional[float]:
    """
    Пытаемся найти ближайшее измерение не старше 6ч в радиусе 200 км.
    Ожидаем µSv/h.
    """
    try:
        r = requests.get("https://eurdep.jrc.ec.europa.eu/eurdep/json/", timeout=TIMEOUT)
        r.raise_for_status()
        j = r.json()
        best, dmin = None, 1e9
        now = time.time()
        for p in j.get("measurements", []):
            try:
                la, lo = float(p["lat"]), float(p["lon"])
                dx = _haversine(lat, lon, la, lo)
                if dx < 200 and (now - float(p.get("utctime", 0)) < 6*3600):
                    if dx < dmin:
                        best, dmin = p, dx
            except Exception:
                continue
        if best:
            return float(best["value"])
    except Exception as e:
        logging.info("eurdep err: %s", e)
    return None

# ─────────────────── локальные fallback-и ─────────────────────
def _try_safecast_cache(lat: float, lon: float) -> Optional[Dict[str, Any]]:
    """
    Ищем ближайшую запись в локальном safecast_* json (uSv/h).
    Поддерживаем разные поля: uSv_h | value | val.
    """
    for path in SAFECAST_CANDIDATES:
        if not path.exists():
            continue
        data = _load_json(path)
        if not isinstance(data, (list, dict)):
            continue
        # допускаем как список, так и словарь с историей
        items: List[Dict[str, Any]] = []
        if isinstance(data, list):
            items = data
        elif isinstance(data, dict):
            # возможные ключи-коллекции
            for k in ("history", "items", "data"):
                if isinstance(data.get(k), list):
                    items = data[k]
                    break
        if not items:
            continue
        # берём ближайшую по расстоянию из последних 48 часов
        now = time.time()
        best = None
        dmin = 1e9
        for it in items[::-1]:
            try:
                la = float(it.get("lat") or it.get("latitude"))
                lo = float(it.get("lon") or it.get("longitude"))
                ts = float(it.get("ts") or it.get("timestamp") or 0)
                if (now - ts) > 48*3600:
                    continue
                dx = _haversine(lat, lon, la, lo)
                if dx < dmin:
                    # нормализуем поле значения
                    v = it.get("uSv_h")
                    if v is None: v = it.get("value")
                    if v is None: v = it.get("val")
                    if v is None: v = it.get("dose")
                    v = float(v) if v is not None else None
                    if v is None:
                        continue
                    best, dmin = {"value": v, "ts": ts}, dx
            except Exception:
                continue
        if best:
            return {"value": round(best["value"], 3), "source": "safecast-cache", "cached": True}
    return None

def _try_hourly_cache(lat: float, lon: float) -> Optional[Dict[str, Any]]:
    """
    radiation_hourly.json — ожидается список точек с lat/lon и val|value|uSv_h.
    Рассчитываем тренд по двум последним наблюдениям поблизости.
    """
    if not CACHE_HOURLY.exists():
        return None
    arr = _load_json(CACHE_HOURLY)
    if not isinstance(arr, list):
        return None
    pts = []
    for p in arr:
        try:
            if _haversine(lat, lon, float(p["lat"]), float(p["lon"])) < 150:
                v = p.get("val")
                if v is None: v = p.get("value")
                if v is None: v = p.get("uSv_h")
                v = float(v) if v is not None else None
                if v is not None:
                    pts.append({"ts": float(p.get("ts") or 0), "value": v})
        except Exception:
            continue
    if len(pts) == 0:
        return None
    pts.sort(key=lambda x: x["ts"])
    last = pts[-1]["value"]
    prev = pts[-2]["value"] if len(pts) >= 2 else last
    diff = last - prev
    trend = "↑" if diff > 0.005 else ("↓" if diff < -0.005 else "→")
    return {"value": round(last, 3), "trend": trend, "source": "cache", "cached": True}

# ───────────────────────── API ───────────────────────────────
def get_radiation(lat: float, lon: float) -> Dict[str, Any] | None:
    """
    См. контракт в шапке. Возвращает None, если нет данных.
    """
    # 1) Радмон (CPM → µSv/h)
    live_radmon = _try_radmon(lat, lon)
    if live_radmon is not None:
        usvh, cpm = live_radmon
        return {"value": round(usvh, 3), "cpm": round(cpm, 1) if cpm is not None else None,
                "source": "radmon", "cached": False}

    # 2) EURDEP (µSv/h)
    live_eurdep = _try_eurdep(lat, lon)
    if live_eurdep is not None:
        return {"value": round(live_eurdep, 3), "cpm": None, "source": "eurdep", "cached": False}

    # 3) Safecast из локальной истории
    sc = _try_safecast_cache(lat, lon)
    if sc is not None:
        # совместимость: добавим trend как неизвестный
        sc.setdefault("trend", "→")
        return sc

    # 4) Общий кэш-часовки
    hc = _try_hourly_cache(lat, lon)
    if hc is not None:
        return hc

    return None
