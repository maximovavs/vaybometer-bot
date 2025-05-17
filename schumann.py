#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
schumann.py

• SCH_QUOTES – юмористические фразы-заглушки.
• get_schumann() возвращает:
    ▸ {"freq": float, "amp": float, "high": True}   – если freq > 8 Гц
    ▸ {"freq": float, "amp": float}                 – если freq ≤ 8 Гц
    ▸ {"msg": str}                                 – если оба источника недоступны
"""

from __future__ import annotations
import logging, random
from typing import Dict, Any

from utils import _get

SCH_QUOTES = [
    "датчики молчат — ретрит 🌱",
    "кошачий мяу-фактор заглушил сенсоры 😸",
    "волны ушли ловить чаек 🐦",
    "показания медитируют 🧘",
    "данные в отпуске 🏝️",
    "Шуман спит — не будим 🔕",
    "тишина в эфире… 🎧",
]

# ────────────────────────────────────────────────────────────────────
def get_schumann() -> Dict[str, Any]:
    """
    Возвращает словарь c частотой/амплитудой резонанса Шумана
    либо шуточную «msg», если данные не получены.
    """
    for url in (
        "https://api.glcoherence.org/v1/earth",
        "https://gci-api.ucsd.edu/data/latest",
    ):
        data = _get(url)
        if not data:
            continue

        try:
            # у второго эндпоинта полезные данные в ["data"]["sr1"]
            if "data" in data:
                data = data["data"]["sr1"]

            freq = data.get("frequency_1") or data.get("frequency")
            amp  = data.get("amplitude_1") or data.get("amplitude")
            if freq is None or amp is None:
                raise ValueError("missing fields")

            freq_val = float(freq)
            amp_val  = float(amp)

            result: Dict[str, Any] = {
                "freq": round(freq_val, 2),
                "amp":  round(amp_val, 1),
            }
            if freq_val > 8.0:               # «⚡️ повышенные вибрации»
                result["high"] = True
            return result

        except Exception as e:
            logging.warning("schumann parse %s: %s", url, e)

    # оба источника недоступны
    return {"msg": random.choice(SCH_QUOTES)}


# ── простой тест:  python -m schumann ─────────────────────────────
if __name__ == "__main__":
    from pprint import pprint
    pprint(get_schumann())
