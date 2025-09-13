#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
pollen.py (Cyprus)
~~~~~~~~~~~~~~~~~~
Пыльца из Open-Meteo Air Quality API с улучшениями из KLD:

• Безопасный HTTP с таймаутом (HTTP_TIMEOUT, сек).
• Берём значения по БЛИЖАЙШЕМУ ПРОШЕДШЕМУ часу (а не первый элемент).
• Округление до 0.1; единый риск по максимуму из трёх показателей.
• Всегда возвращаем словарь (без None), чтобы не ломать вызовы.

Возвращаемый формат:
{
  "tree":  float|None,   # birch_pollen
  "grass": float|None,   # grass_pollen
  "weed":  float|None,   # ragweed_pollen
  "risk":  "низкий"|"умеренный"|"высокий"|"экстремальный"|"н/д"
}
"""

from __future__ import annotations
import os
import math
import logging
from time import gmtime, strftime
from typing import Dict, Any, Optional, List, Union

from utils import _get  # ожидание: _get(url, **query) -> parsed JSON (dict)

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
REQUEST_TIMEOUT = float(os.getenv("HTTP_TIMEOUT", "10"))

# дефолт — Лимассол
DEFAULT_LAT = 34.707
DEFAULT_LON = 33.022


def _safe_http_get(url: str, **params) -> Optional[Dict[str, Any]]:
    """Пробуем _get с timeout, при несовместимости — без него; ошибки → None."""
    try:
        try:
            return _get(url, timeout=REQUEST_TIMEOUT, **params)  # type: ignore[call-arg]
        except TypeError:
            return _get(url, **params)
    except Exception as e:
        logging.warning("pollen: HTTP error: %s", e)
        return None


def _risk_level(val: Optional[float]) -> str:
    if val is None:
        return "н/д"
    if val < 10:
        return "низкий"
    if val < 30:
        return "умеренный"
    if val < 70:
        return "высокий"
    return "экстремальный"


def _pick_nearest_past_hour(times: List[str], values: List[Any]) -> Optional[float]:
    """Берём значение по ближайшему прошедшему часу (UTC)."""
    if not times or not values or len(times) != len(values):
        return None
    now_iso = strftime("%Y-%m-%dT%H:00", gmtime())
    idxs = [i for i, t in enumerate(times) if isinstance(t, str) and t <= now_iso]
    if not idxs:
        return None
    v = values[max(idxs)]
    if isinstance(v, (int, float)) and math.isfinite(v) and v >= 0:
        return float(v)
    return None


def get_pollen(lat: float = DEFAULT_LAT, lon: float = DEFAULT_LON) -> Dict[str, Any]:
    """
    Публичный API: возвращает словарь с концентрациями и риском.
    Ошибки/нет данных → значения None и risk="н/д".
    """
    empty = {"tree": None, "grass": None, "weed": None, "risk": "н/д"}

    j = _safe_http_get(
        "https://air-quality-api.open-meteo.com/v1/air-quality",
        latitude=lat,
        longitude=lon,
        hourly="birch_pollen,grass_pollen,ragweed_pollen",
        timezone="UTC",
    )
    if not j or "hourly" not in j:
        logging.debug("pollen: нет hourly в ответе")
        return empty

    try:
        h = j["hourly"]
        times: List[str] = h.get("time", []) or []
        birch: List[Union[float, None]]   = h.get("birch_pollen", [])   or []
        grass: List[Union[float, None]]   = h.get("grass_pollen", [])   or []
        ragweed: List[Union[float, None]] = h.get("ragweed_pollen", []) or []

        tree_val  = _pick_nearest_past_hour(times, birch)
        grass_val = _pick_nearest_past_hour(times, grass)
        weed_val  = _pick_nearest_past_hour(times, ragweed)

        def _rnd(x: Optional[float]) -> Optional[float]:
            return round(float(x), 1) if isinstance(x, (int, float)) and math.isfinite(x) else None

        tree_r, grass_r, weed_r = _rnd(tree_val), _rnd(grass_val), _rnd(weed_val)
        candidates = [v for v in (tree_r, grass_r, weed_r) if isinstance(v, (int, float))]
        max_val = max(candidates) if candidates else None

        return {
            "tree":  tree_r,
            "grass": grass_r,
            "weed":  weed_r,
            "risk":  _risk_level(max_val),
        }
    except Exception as e:
        logging.warning("pollen: parse error: %s", e)
        return empty


if __name__ == "__main__":
    from pprint import pprint
    pprint(get_pollen())
