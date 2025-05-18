#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
pollen.py
~~~~~~~~~

Бесплатный источник: Open-Meteo «Air-Quality / Pollen»
    https://air-quality-api.open-meteo.com/v1/air-quality

Функция get_pollen()
--------------------
Возвращает ВСЕГДА словарь
{
    "tree" : int|"н/д",   # 0–5
    "grass": int|"н/д",
    "weed" : int|"н/д",
    "risk" : str,         # текстовое описание суммарного риска
    "color": str,         # 🟢🟡🟠🔴🟣⚫
    "msg"  : "ok" | "н/д"
}
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from utils import _get

# ───────────────────────────────────────────────────────────────────
# Координаты по-умолчанию (Limassol)
LAT, LON = 34.707, 33.022

# 0–5 → текст + цвет
RISK_TXT   = ["нет", "низкий", "умеренный",
              "высокий", "оч. высокий", "экстрим"]      # ★
POLLEN_EMOJI = ["⚪", "🟢", "🟡", "🟠", "🔴", "🟣"]        # ★


def _risk_level(val: Optional[int]) -> tuple[str, str]:
    """Число 0–5 → (текст, эмодзи-цвет). None → ('н/д','⚪')."""
    if val is None:
        return "н/д", "⚪"
    try:
        idx = max(0, min(int(round(val)), 5))
        return RISK_TXT[idx], POLLEN_EMOJI[idx]
    except Exception:
        return "н/д", "⚪"


# ───────────────────────────────────────────────────────────────────
def get_pollen(lat: float = LAT, lon: float = LON) -> Dict[str, Any]:
    """
    Запрашивает ближайший часовой прогноз пыльцы у Open-Meteo.
    Всегда выдаёт словарь с ключами tree / grass / weed / risk / color / msg.
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
            "tree":  "н/д",
            "grass": "н/д",
            "weed":  "н/д",
            "risk":  "н/д",
            "color": "⚪",
            "msg":   "н/д",
        }

    h: Dict[str, list] = j["hourly"]

    def first_val(key: str) -> Optional[int]:
        try:
            raw = h[key][0]
            if raw is None:
                return None
            return int(round(float(raw)))
        except Exception:
            return None

    tree  = first_val("pollen_level_tree")
    grass = first_val("pollen_level_grass")
    weed  = first_val("pollen_level_weed")

    numeric = [v for v in (tree, grass, weed) if v is not None]
    max_level = max(numeric) if numeric else None
    risk_txt, risk_color = _risk_level(max_level)

    # заменяем None на «н/д» для удобства format/safe()
    tree  = tree  if tree  is not None else "н/д"
    grass = grass if grass is not None else "н/д"
    weed  = weed  if weed  is not None else "н/д"

    return {
        "tree":  tree,
        "grass": grass,
        "weed":  weed,
        "risk":  risk_txt,
        "color": risk_color,
        "msg":   "ok",
    }


# ───────────────────────────────────────────────────────────────────
if __name__ == "__main__":  # быстрая CLI-проверка:  python -m pollen [lat lon]
    import json, sys
    lat = float(sys.argv[1]) if len(sys.argv) > 1 else LAT
    lon = float(sys.argv[2]) if len(sys.argv) > 2 else LON
    print(json.dumps(get_pollen(lat, lon), ensure_ascii=False, indent=2))
