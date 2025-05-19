#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
schumann.py
~~~~~~~~~~~
• берёт freq/amp из 2 open-API;
• пишет историю в  ~/cache/sr1.json  (не критично, если каталога нет);
• high = freq>8 Гц  ИЛИ  amp>100;
• get_schumann_trend(hours) → ↑/↓/→.
"""

from __future__ import annotations
import json, os, time, logging, random, pathlib
from typing import Dict, Any, List
from datetime import datetime, timedelta

from utils import _get

# -----------------------------------------------------------------
CACHE_PATH = pathlib.Path.home() / "cache" / "sr1.json"
CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)

URLS = (
    "https://api.glcoherence.org/v1/earth",
    "https://gci-api.ucsd.edu/data/latest",
)

SCH_QUOTES = [
    "датчики молчат — ретрит 🌱",
    "кошачий мяу-фактор заглушил сенсоры 😸",
    "волны ушли ловить чаек 🐦",
    "показания медитируют 🧘",
    "данные в отпуске 🏝️",
    "Шуман спит — не будим 🔕",
    "тишина в эфире… 🎧",
]

# -----------------------------------------------------------------
def _save_point(freq: float, amp: float) -> None:
    point = {"ts": time.time(), "freq": round(freq, 3), "amp": round(amp, 1)}
    history: List[dict] = []
    if CACHE_PATH.exists():
        try:
            history = json.loads(CACHE_PATH.read_text())
        except Exception:
            history = []
    history.append(point)
    # оставляем последние 7 дней
    week_ago = time.time() - 7 * 24 * 3600
    history = [p for p in history if p["ts"] >= week_ago]
    CACHE_PATH.write_text(json.dumps(history, ensure_ascii=False))

def _last_points(hours: int = 24) -> List[dict]:
    if not CACHE_PATH.exists():
        return []
    try:
        history = json.loads(CACHE_PATH.read_text())
    except Exception:
        return []
    border = time.time() - hours * 3600
    return [p for p in history if p["ts"] >= border]

# -----------------------------------------------------------------
def get_schumann() -> Dict[str, Any]:
    """
    dict:
        {'freq': 7.83, 'amp': 45.1, 'high': False}
        или {'msg': '...'}
    """
    for url in URLS:
        j = _get(url)
        if not j:
            continue
        try:
            if "data" in j:                 # второй эндпоинт
                j = j["data"]["sr1"]
            freq = float(j.get("frequency_1") or j.get("frequency"))
            amp  = float(j.get("amplitude_1") or j.get("amplitude"))
            _save_point(freq, amp)
            return {
                "freq": round(freq, 2),
                "amp":  round(amp, 1),
                "high": freq > 8.0 or amp > 100.0,
            }
        except Exception as e:
            logging.warning("schumann parse %s: %s", url, e)

    # оба источника недоступны — пробуем кэш
    pts = _last_points(48)
    if pts:
        last = pts[-1]
        return {
            "freq": last["freq"],
            "amp":  last["amp"],
            "high": last["freq"] > 8.0 or last["amp"] > 100.0,
            "cached": True,
        }
    return {"msg": random.choice(SCH_QUOTES)}

# -----------------------------------------------------------------
def get_schumann_trend(hours: int = 24) -> str:
    """
    ↑  если последняя freq > средней на |hours|;
    ↓  если < средней −0.1 Гц;
    →  иначе.
    """
    pts = _last_points(hours)
    if len(pts) < 3:
        return "→"
    avg = sum(p["freq"] for p in pts[:-1]) / (len(pts)-1)
    last = pts[-1]["freq"]
    if last - avg >= 0.10:
        return "↑"
    if last - avg <= -0.10:
        return "↓"
    return "→"

# -----------------------------------------------------------------
if __name__ == "__main__":
    from pprint import pprint
    pprint(get_schumann())
    print("trend 24 h:", get_schumann_trend())
