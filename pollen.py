#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
pollen.py
~~~~~~~~~

• Бесплатный источник: Open-Meteo ‘Air-Quality / Pollen’
  https://air-quality-api.open-meteo.com/v1/air-quality

Функция get_pollen()
--------------------
Возвращает ВСЕГДА словарь вида
{
    "tree" : <int|None>,   # 0‒4   (0 — нет пыльцы)
    "grass": <int|None>,
    "weed" : <int|None>,
    "risk" : <str>,        # "нет", "низкий", "умеренный", …
    "msg"  : <str>,        # "ok" | "н/д"  (для логов/UI)
}
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from utils import _get

# ───────────────────────────────────────────────────────────────────
# Координаты по-умолчанию (Limassol). При желании можно передавать свои.
LAT, LON = 34.707, 33.022

# Официальная шкала риска 0–4 → текст
RISK_TXT = ["нет", "низкий", "умеренный", "высокий", "оч. высокий"]


def _risk_level(val: Optional[int]) -> str:
    """Число 0–4 → текст, None → 'н/д'."""
    if val is None:
        return "н/д"
    try:
        return RISK_TXT[int(round(val))]
    except Exception:
        return "н/д"


# ───────────────────────────────────────────────────────────────────
def get_pollen(lat: float = LAT, lon: float = LON) -> Dict[str, Any]:
    """
    Запрашивает ближайший часовой прогноз пыльцы у Open-Meteo.
    Всегда выдаёт словарь с ключами: tree / grass / weed / risk / msg.
    """
    url = "https://air-quality-api.open-meteo.com/v1/air-quality"
    params = {
        "latitude":  lat,
        "longitude": lon,
        "timezone":  "UTC",
        "hourly":    "pollen_level_tree,pollen_level_grass,pollen_level_weed",
    }

    j = _get(url, **params)
    if not j or "hourly" not in j:
        logging.warning("Pollen API unavailable")
        return {
            "tree": None,
            "grass": None,
            "weed": None,
            "risk": "н/д",
            "msg":  "н/д",
        }

    h: Dict[str, list] = j["hourly"]

    def first_val(key: str) -> Optional[int]:
        try:
            return int(round(float(h[key][0])))
        except Exception:
            return None

    tree  = first_val("pollen_level_tree")
    grass = first_val("pollen_level_grass")
    weed  = first_val("pollen_level_weed")

    # суммарный риск как максимум из доступных числовых значений
    numeric = [v for v in (tree, grass, weed) if v is not None]
    risk = _risk_level(max(numeric) if numeric else None)

    return {
        "tree":  tree,
        "grass": grass,
        "weed":  weed,
        "risk":  risk,
        "msg":   "ok",
    }


# ───────────────────────────────────────────────────────────────────
if __name__ == "__main__":  # быстрая проверка:  python -m pollen
    import json, sys

    lat = float(sys.argv[1]) if len(sys.argv) > 2 else LAT
    lon = float(sys.argv[2]) if len(sys.argv) > 2 else LON
    print(json.dumps(get_pollen(lat, lon), ensure_ascii=False, indent=2))
