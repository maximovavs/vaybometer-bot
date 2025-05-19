#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
schumann.py
~~~~~~~~~~~
• Берёт freq/amp из двух открытых API.
• Кэширует точки в ~/.cache/vaybometer/sr1.json (7 дней истории).
• high = freq > 8 Гц  ИЛИ  amp > 100.
• get_schumann_trend(hours) → ↑ / ↓ / →.
"""

from __future__ import annotations

import json
import logging
import os
import random
import time
from pathlib import Path
from typing import Any, Dict, List

from utils import _get

# ── конфиг ────────────────────────────────────────────────────────
CACHE_PATH = (
    Path.home() / ".cache" / "vaybometer" / "sr1.json"
)  # ~/.cache/vaybometer/sr1.json
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

# ── работа с кэшем ────────────────────────────────────────────────
def _save_point(freq: float, amp: float) -> None:
    """Добавляем точку в историю, храним 7 дней."""
    point = {"ts": time.time(), "freq": round(freq, 3), "amp": round(amp, 1)}
    try:
        history: List[dict] = json.loads(CACHE_PATH.read_text())
    except Exception:
        history = []

    history.append(point)
    week_ago = time.time() - 7 * 24 * 3600
    history = [p for p in history if p["ts"] >= week_ago]

    CACHE_PATH.write_text(json.dumps(history, ensure_ascii=False))


def _last_points(hours: int = 24) -> List[dict]:
    """Возвращает точки за последние *hours* часов из кэша."""
    try:
        history: List[dict] = json.loads(CACHE_PATH.read_text())
    except Exception:
        return []

    border = time.time() - hours * 3600
    return [p for p in history if p["ts"] >= border]


# ── основные функции ─────────────────────────────────────────────
def get_schumann() -> Dict[str, Any]:
    """
    {'freq': 7.83, 'amp': 45.1, 'high': False}
    {'freq': 8.12, 'amp':120.3, 'high': True}
    {'freq': 7.9,  'amp': 40.0, 'high': False, 'cached': True}
    {'msg': '...'}  – если всё упало
    """
    for url in URLS:
        data = _get(url)
        if not data:
            continue
        try:
            if "data" in data:  # второй эндпоинт
                data = data["data"]["sr1"]

            freq_raw = data.get("frequency_1") or data.get("frequency")
            amp_raw  = data.get("amplitude_1") or data.get("amplitude")
            if freq_raw is None or amp_raw is None:
                raise ValueError("freq/amp absent")

            freq, amp = float(freq_raw), float(amp_raw)
            _save_point(freq, amp)

            return {
                "freq": round(freq, 2),
                "amp":  round(amp, 1),
                "high": (freq > 8.0) or (amp > 100.0),
            }

        except Exception as e:
            logging.warning("schumann parse %s: %s", url, e)

    # оба источника недоступны → пробуем кэш
    pts = _last_points(48)
    if pts:
        last = pts[-1]
        return {
            "freq": last["freq"],
            "amp":  last["amp"],
            "high": (last["freq"] > 8.0) or (last["amp"] > 100.0),
            "cached": True,
        }

    # совсем ничего
    return {"msg": random.choice(SCH_QUOTES)}


def get_schumann_trend(hours: int = 24) -> str:
    """
    ↑ если последняя freq > средней за *hours* на ≥0.1 Гц,
    ↓ если ниже на ≥0.1 Гц,
    → в остальных случаях.
    """
    pts = _last_points(hours)
    if len(pts) < 3:
        return "→"

    *prev, last = pts
    avg = sum(p["freq"] for p in prev) / len(prev)
    delta = last["freq"] - avg

    if delta >= 0.10:
        return "↑"
    if delta <= -0.10:
        return "↓"
    return "→"


# ── CLI-тест ──────────────────────────────────────────────────────
if __name__ == "__main__":
    from pprint import pprint

    pprint(get_schumann())
    print("trend 24 h:", get_schumann_trend())
