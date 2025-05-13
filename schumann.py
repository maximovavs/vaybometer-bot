#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
schumann.py

• SCH_QUOTES расширен до 7 вариантов.
• get_schumann() возвращает словарь:
    - {"freq": float, "amp": float, "high": bool}
      когда данные получены и freq > 8 Гц → high=True
    - {"freq": float, "amp": float, "high": False}
      когда freq ≤ 8 Гц
    - {"msg": str}
      когда оба источника недоступны → юмористическая заглушка
"""

import logging
import random
from typing import Dict, Any, Optional

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

def get_schumann() -> Dict[str, Any]:
    """
    Пытается получить текущие показания резонанса Шумана
    из двух эндпоинтов. Возвращает:
      • {"freq": float, "amp": float, "high": bool}
      • или {"msg": str} при ошибках всех источников.
    """
    for url in (
        "https://api.glcoherence.org/v1/earth",
        "https://gci-api.ucsd.edu/data/latest",
    ):
        j = _get(url)
        if not j:
            continue

        try:
            # второй сервис оборачивает данные в j["data"]["sr1"]
            if "data" in j:
                j = j["data"]["sr1"]
            # ключи могут называться frequency_1 или frequency, amplitude_1 или amplitude
            freq = j.get("frequency_1") or j.get("frequency")
            amp  = j.get("amplitude_1")  or j.get("amplitude")

            if freq is None or amp is None:
                raise ValueError("missing fields")

            freq_val = float(freq)
            amp_val  = float(amp)
            return {
                "freq": freq_val,
                "amp":  amp_val,
                "high": freq_val > 8.0,   # ⚡️ повышенные вибрации
            }
        except Exception as e:
            logging.warning("get_schumann(%s) parse error: %s", url, e)
            continue

    # оба источника недоступны — шуточный заглушка
    return {"msg": random.choice(SCH_QUOTES)}
