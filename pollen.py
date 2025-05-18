
#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
pollen.py
~~~~~~~~~

• Единственный бесплатный источник — Open-Meteo *Air Quality / Pollen*  
  (https://air-quality-api.open-meteo.com)
• Функция **get_pollen()** всегда возвращает словарь

    {
        "tree":  <int | None>,   # 0–4  (0 — нет пыльцы)
        "grass": <int | None>,
        "weed":  <int | None>,
        "risk":  <str>,          # "нет", "низкий", "умеренный", …
    }

  Если сервис недоступен, значения — `None`, `risk="н/д"`.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional, Tuple

from utils import _get

# ────────────────────────────────────────────────────────────────────
# Координаты по-умолчанию (Limassol); можно менять при вызове
LAT, LON = 34.707, 33.022

# шкала риска по Open-Meteo (0‒4) → текст
RISK_TXT = ["нет", "низкий", "умеренный", "высокий", "оч. высокий"]


def _risk_level(val: Optional[int]) -> str:
    """Числовой уровень 0–4 → текст; None → 'н/д'."""
    if val is None:
        return "н/д"
    try:
        return RISK_TXT[int(round(val))]
    except Exception:            # вне диапазона / не число
        return "н/д"


# ────────────────────────────────────────────────────────────────────
def get_pollen(lat: float = LAT, lon: float = LON) -> Dict[str, Any]:
    """
    Достаёт сегодняшний прогноз пыльцы с Open-Meteo и
    возвращает словарь с трёх типами + агрегированным риском.
    """
    url = "https://air-quality-api.open-meteo.com/v1/air-quality"
    params = {
        "latitude":   lat,
        "longitude":  lon,
        "timezone":   "UTC",
        # берём ближайшие 24 ч – first элемент массива подойдёт
        "hourly": ",".join(
            [
                "pollen_level_tree",
                "pollen_level_grass",
                "pollen_level_weed",
            ]
        ),
    }

    j = _get(url, **params)
    if not j or "hourly" not in j:
        logging.warning("Pollen API unavailable")
        return {"tree": None, "grass": None, "weed": None, "risk": "н/д"}

    h: Dict[str, list] = j["hourly"]

    def first_val(key: str) -> Optional[int]:
        try:
            return int(round(float(h[key][0])))
        except Exception:
            return None

    tree  = first_val("pollen_level_tree")
    grass = first_val("pollen_level_grass")
    weed  = first_val("pollen_level_weed")

    # агрегированный риск — максимум из имеющихся
    numeric_levels = [v for v in (tree, grass, weed) if v is not None]
    agg_num = max(numeric_levels) if numeric_levels else None
    risk = _risk_level(agg_num)

    return {"tree": tree, "grass": grass, "weed": weed, "risk": risk}


# ────────────────────────────────────────────────────────────────────
if __name__ == "__main__":        # быстрая проверка:  python -m pollen
    import json

    data = get_pollen()
    print(json.dumps(data, ensure_ascii=False, indent=2))
