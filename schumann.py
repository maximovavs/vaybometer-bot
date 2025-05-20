#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
schumann.py
~~~~~~~~~~~
• Собирает freq/amp резонанса Шумана из нескольких API с retry и backoff.
• Кэширует историю (7 дней) в ~/.cache/vaybometer/sr1.json.
• Возвращает частоту, амплитуду, тренд и состояние high. Использует mirror и fallback на кеш.
"""

from __future__ import annotations
import json
import logging
import random
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from utils import _get

# ────────── Кеш и настройки ─────────────────────────────────────────
CACHE_PATH = Path.home() / ".cache" / "vaybometer" / "sr1.json"
CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)

# URL-источники: несколько зеркал + прямой + запасной минутный поток
URLS = [
    # 1) Codetabs proxy
    "https://api.codetabs.com/v1/proxy?quest=https://api.glcoherence.org/v1/earth",
    # 2) ThingProxy
    "https://thingproxy.freeboard.io/fetch/https://api.glcoherence.org/v1/earth",
    # 3) AllOrigins raw
    "https://api.allorigins.win/raw?url=https://api.glcoherence.org/v1/earth",
    # 4) Прямой
    "https://api.glcoherence.org/v1/earth",
    # 5) Запасной минутный поток
    "https://gci-api.ucsd.edu/data/latest",
]

SCH_QUOTES = [
    "датчики молчат — ретрит 🌱",
    "кошачий мяу-фактор заглушил сенсоры 😸",
    "волны ушли ловить чаек 🐦",
    "показания медитируют 🧘",
    "данные в отпуске 🏝️",
    "Шуман спит — не будим 🔕",
    "тишина в эфире… 🎧",
]

# ────────── Помощники кеша ───────────────────────────────────────────
def _save_point(freq: float, amp: float) -> None:
    point: Dict[str, Any] = {"ts": time.time(), "freq": round(freq, 3), "amp": round(amp, 1)}
    try:
        history: List[Dict[str, Any]] = json.loads(CACHE_PATH.read_text())
    except Exception:
        history = []
    history.append(point)
    cutoff = time.time() - 7 * 24 * 3600
    history = [p for p in history if p["ts"] >= cutoff]
    CACHE_PATH.write_text(json.dumps(history, ensure_ascii=False))

def _last_points(hours: int = 24) -> List[Dict[str, Any]]:
    try:
        history: List[Dict[str, Any]] = json.loads(CACHE_PATH.read_text())
    except Exception:
        return []
    cutoff = time.time() - hours * 3600
    return [p for p in history if p["ts"] >= cutoff]

# ────────── Retry + backoff ─────────────────────────────────────────
def _fetch_schumann_data(url: str, attempts: int = 7, backoff: float = 2.0) -> Optional[Any]:
    logging.info("Schumann: fetching from %s (attempts=%d)", url, attempts)
    for i in range(attempts):
        data = _get(url)
        if data:
            logging.info("Schumann: received data from %s", url)
            return data
        wait = backoff ** i
        logging.warning("Schumann: retry %d/%d after %.1fs", i+1, attempts, wait)
        time.sleep(wait)
    logging.error("Schumann: all attempts failed for %s", url)
    return None

# ────────── Тренд Шумана ─────────────────────────────────────────────
def _compute_trend(pts: List[Dict[str, Any]], hours: int = 24) -> str:
    if len(pts) < 3:
        return "→"
    *prev, last = pts
    avg = sum(p["freq"] for p in prev) / len(prev)
    delta = last["freq"] - avg
    if delta >= 0.1:
        return "↑"
    if delta <= -0.1:
        return "↓"
    return "→"

# ────────── Основная функция ─────────────────────────────────────────
logging.info("Schumann: starting retrieval")
def get_schumann() -> Dict[str, Any]:
    for url in URLS:
        raw = _fetch_schumann_data(url)
        if not raw:
            continue
        try:
            if isinstance(raw, dict) and "data" in raw:
                data = raw["data"].get("sr1", raw["data"])
            else:
                data = raw
            freq_val = data.get("frequency_1") or data.get("frequency")
            amp_val  = data.get("amplitude_1")  or data.get("amplitude")
            if freq_val is None or amp_val is None:
                raise ValueError("freq/amp absent")
            freq = float(freq_val)
            amp  = float(amp_val)
            _save_point(freq, amp)
            pts = _last_points(24)
            return {
                "freq":  round(freq, 2),
                "amp":   round(amp,   1),
                "high":  freq > 8.0 or amp > 100.0,
                "trend": _compute_trend(pts),
            }
        except Exception as e:
            logging.warning("Schumann parse error %s: %s", url, e)

    # Фоллбэк на кеш за 48 часов
    pts48 = _last_points(48)
    if pts48:
        last = pts48[-1]
        return {
            "freq":   last["freq"],
            "amp":    last["amp"],
            "high":   last["freq"] > 8.0 or last["amp"] > 100.0,
            "trend":  _compute_trend(pts48),
            "cached": True,
        }

    # Совсем нет данных — выдаём шутку
    return {"msg": random.choice(SCH_QUOTES)}

# ────────── CLI-тест ────────────────────────────────────────────────
if __name__ == "__main__":
    from pprint import pprint
    pprint(get_schumann())
    print("trend 24h:", get_schumann().get("trend"))
