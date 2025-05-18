#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
schumann.py
~~~~~~~~~~~

• Два бесплат-API c резонансом Шумана  
    1) https://api.glcoherence.org/v1/earth         (JSON с полями frequency_1, amplitude_1)  
    2) https://gci-api.ucsd.edu/data/latest         (тот же набор, но вложен в ["data"]["sr1"])

• При успешном чтении частота/амплитуда пишутся в кэш
      ~/.cache/vaybometer/sr1.json
  (храним список записей вида {"ts": "...", "freq": 7.83, "amp": 48.2})

• get_schumann() → {'freq', 'amp', 'high'}  либо {'msg'}  
    high = freq > 8 Гц **или** amp > 100

• get_schumann_trend(hours=24) → '↑' / '↓' / '→'
    сравнивает последнюю freq с freq часовой давности (порог ±0.05 Гц)

Пример использования
--------------------
>>> from schumann import get_schumann, get_schumann_trend
>>> d = get_schumann();  print(d)
{'freq': 7.79, 'amp': 54.1, 'high': False}
>>> print(get_schumann_trend(24))
'↑'
"""

from __future__ import annotations

import json, os, time, random, logging
from pathlib import Path
from typing import Any, Dict, List

from utils import _get

# ── постоянные ----------------------------------------------------
CACHE_DIR  = Path.home() / ".cache" / "vaybometer"
CACHE_FILE = CACHE_DIR / "sr1.json"
CACHE_DIR.mkdir(parents=True, exist_ok=True)

SCH_QUOTES = [
    "датчики молчат — ретрит 🌱",
    "кошачий мяу-фактор заглушил сенсоры 😸",
    "волны ушли ловить чаек 🐦",
    "показания медитируют 🧘",
    "данные в отпуске 🏝️",
    "Шуман спит — не будим 🔕",
    "тишина в эфире… 🎧",
]

# ──────────────────────────────────────────────────────────────────
def _load_history() -> List[Dict[str, float]]:
    if CACHE_FILE.exists():
        try:
            return json.loads(CACHE_FILE.read_text())
        except Exception:
            pass
    return []

def _save_history(hist: List[Dict[str, float]]) -> None:
    # храним не более 1000 записей
    hist = hist[-1000:]
    CACHE_FILE.write_text(json.dumps(hist))

def _append_history(freq: float, amp: float) -> None:
    hist = _load_history()
    hist.append({"ts": time.time(), "freq": freq, "amp": amp})
    _save_history(hist)

# ──────────────────────────────────────────────────────────────────
def get_schumann() -> Dict[str, Any]:
    """
    Возвращает:
        {'freq': 7.83, 'amp': 48.2, 'high': False}
        {'freq': 8.14, 'amp':120.3, 'high': True}
        {'msg': '…'}                       – когда оба источника упали
    high → freq > 8 Гц  **или** amp > 100
    """
    for url in (
        "https://api.glcoherence.org/v1/earth",
        "https://gci-api.ucsd.edu/data/latest",
    ):
        j = _get(url)
        if not j:
            continue

        try:
            # во втором эндпоинте данные лежат в ["data"]["sr1"]
            if "data" in j:
                j = j["data"]["sr1"]

            freq = float(j.get("frequency_1") or j.get("frequency"))
            amp  = float(j.get("amplitude_1")  or j.get("amplitude"))
        except Exception as e:
            logging.warning("schumann parse %s: %s", url, e)
            continue

        # сохраним в историю
        _append_history(freq, amp)

        return {
            "freq": round(freq, 2),
            "amp":  round(amp, 1),
            "high": (freq > 8.0) or (amp > 100),
        }

    # оба запроса не дали результата
    return {"msg": random.choice(SCH_QUOTES)}

# ──────────────────────────────────────────────────────────────────
def get_schumann_trend(hours: int = 24) -> str:
    """
    Анализирует историю и возвращает:
        '↑' – если freq выросла > 0.05 Гц
        '↓' – если freq упала   <-0.05 Гц
        '→' – изменений нет / данных мало
    """
    hist = _load_history()
    if len(hist) < 2:
        return "→"

    latest = hist[-1]
    t_cut  = latest["ts"] - hours * 3600

    # ищем запись не моложе t_cut
    earlier = next((x for x in reversed(hist[:-1]) if x["ts"] <= t_cut), None)
    if not earlier:
        return "→"

    diff = latest["freq"] - earlier["freq"]
    if diff >= 0.05:
        return "↑"
    if diff <= -0.05:
        return "↓"
    return "→"

# ── CLI-тест ------------------------------------------------------
if __name__ == "__main__":
    from pprint import pprint
    pprint(get_schumann())
    print("24h trend:", get_schumann_trend())
