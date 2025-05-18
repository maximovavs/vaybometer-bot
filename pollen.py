#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
pollen.py
~~~~~~~~~
Бесплатный источник: Open-Meteo *Air-Quality / Pollen*
    https://air-quality-api.open-meteo.com/v1/air-quality

Функция
    get_pollen(lat=…, lon=…) → dict
возвращает ВСЕГДА словарь с ключами
{
  "tree": 0‒5 | "н/д",
  "grass": …,
  "weed": …,
  "risk":  "нет/низкий/…/экстрим/н/д",
  "color": "⚪🟢🟡🟠🔴🟣",
  "ts":    "YYYY-MM-DDTHH",     # ISO-час метка прогноза
  "msg":   "ok" | "н/д"
}
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from utils import _get

# ------------------------------------------------------------------
LAT, LON = 34.707, 33.022   # Limassol по-умолчанию

RISK_TXT      = ["нет", "низкий", "умеренный",
                 "высокий", "оч. высокий", "экстрим"]
POLLEN_EMOJI  = ["⚪",  "🟢",   "🟡",    "🟠",    "🔴",       "🟣"]

# ------------------------------------------------------------------
def _risk_level(val: Optional[float]) -> tuple[str, str]:
    """0‒5 (в т. ч. дробь) → (текст, эмодзи); None → ('н/д','⚪')."""
    if val is None:
        return "н/д", "⚪"
    try:
        idx = int(round(float(val)))
        idx = 0 if idx < 0 else 5 if idx > 5 else idx
        return RISK_TXT[idx], POLLEN_EMOJI[idx]
    except Exception:
        return "н/д", "⚪"

# ------------------------------------------------------------------
def get_pollen(lat: float = LAT, lon: float = LON) -> Dict[str, Any]:
    """
    Запрашивает ближайший ЧАСОВОЙ прогноз пыльцы у Open-Meteo.
    Гарантированно возвращает словарь из docstring.
    """
    url = "https://air-quality-api.open-meteo.com/v1/air-quality"
    j = _get(
        url,
        latitude=lat,
        longitude=lon,
        timezone="UTC",
        hourly="pollen_level_tree,pollen_level_grass,pollen_level_weed",
    )

    if not j or "hourly" not in j:
        logging.warning("Pollen API unavailable")
        return {
            "tree":  "н/д",
            "grass": "н/д",
            "weed":  "н/д",
            "risk":  "н/д",
            "color": "⚪",
            "ts":    None,
            "msg":   "н/д",
        }

    hourly: Dict[str, list] = j["hourly"]

    def first_val(key: str) -> Optional[int]:
        raw = hourly.get(key, [None])[0]
        if raw is None:
            return None
        try:
            return int(round(float(raw)))
        except Exception:
            return None

    tree  = first_val("pollen_level_tree")
    grass = first_val("pollen_level_grass")
    weed  = first_val("pollen_level_weed")

    numeric = [v for v in (tree, grass, weed) if v is not None]
    max_level = max(numeric) if numeric else None
    risk_txt, risk_color = _risk_level(max_level)

    # заменяем None → "н/д" для удобства форматирования
    tree  = tree  if tree  is not None else "н/д"
    grass = grass if grass is not None else "н/д"
    weed  = weed  if weed  is not None else "н/д"

    return {
        "tree":  tree,
        "grass": grass,
        "weed":  weed,
        "risk":  risk_txt,
        "color": risk_color,
        "ts":    j["hourly"]["time"][0] if "time" in j["hourly"] else None,
        "msg":   "ok",
    }

# ------------------------------------------------------------------
if __name__ == "__main__":          # ➜  python -m pollen [lat lon]
    import json, sys
    lat = float(sys.argv[1]) if len(sys.argv) > 1 else LAT
    lon = float(sys.argv[2]) if len(sys.argv) > 2 else LON
    print(json.dumps(get_pollen(lat, lon), ensure_ascii=False, indent=2))
