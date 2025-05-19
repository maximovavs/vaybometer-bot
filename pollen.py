#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
pollen.py
~~~~~~~~~
Получает данные о концентрации пыльцы из бесплатного энд-поинта
Open-Meteo Pollen и возвращает унифицированный словарь:

    {
        "tree" : <float>,   # ед./м³
        "grass": <float>,
        "weed" : <float>,
        "risk" : "нет" | "низкий" | "умеренный" | "высокий" | "экстремальный"
    }

Если сервис недоступен, возвращается:
    {"tree": None, "grass": None, "weed": None, "risk": "н/д"}
"""

from __future__ import annotations
import logging
from typing import Dict, Any, Optional

from utils import _get

# ── координаты по умолчанию (Лимассол) ───────────────────────────
LAT, LON = 34.707, 33.022

# ── градации уровня риска по EAN / Copernicus─────────────────────
def _risk_level(val: Optional[float]) -> str:
    if val is None:
        return "н/д"
    if val <  10:  return "нет"
    if val <  30:  return "низкий"
    if val <  70:  return "умеренный"
    if val < 120:  return "высокий"
    return "экстремальный"

# ─────────────────────────────────────────────────────────────────
def get_pollen(lat: float = LAT, lon: float = LON) -> Dict[str, Any]:
    """
    Возвращает словарь с ключами tree / grass / weed / risk.

    • risk определяется по максимальному из трёх индексов.
    • При ошибке или отсутствии значений возвращает 'None' и 'н/д'.
    """
    empty = {"tree": None, "grass": None, "weed": None, "risk": "н/д"}

    j: Optional[dict] = _get(
        "https://pollen-api.open-meteo.com/v1/forecast",
        latitude=lat,
        longitude=lon,
        timezone="UTC",
        daily="tree_pollen,grass_pollen,weed_pollen",
    )
    if not j or "daily" not in j:
        logging.warning("Pollen API: no data")
        return empty

    try:
        daily = j["daily"]
        tree  = float(daily["tree_pollen"][0])
        grass = float(daily["grass_pollen"][0])
        weed  = float(daily["weed_pollen"][0])

        highest = max(tree, grass, weed)
        return {
            "tree":  round(tree, 1),
            "grass": round(grass, 1),
            "weed":  round(weed, 1),
            "risk":  _risk_level(highest),
        }
    except Exception as e:
        logging.warning("Pollen parse error: %s", e)
        return empty


# ── тест standalone ──────────────────────────────────────────────
if __name__ == "__main__":
    from pprint import pprint
    pprint(get_pollen())
