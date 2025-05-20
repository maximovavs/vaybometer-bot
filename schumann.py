#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
schumann.py
~~~~~~~~~~~
• Собирает freq/amp резонанса Шумана из нескольких API с retry и backoff.
• Кэширует историю (7 дней) в ~/.cache/vaybometer/sr1.json.
• Возвращает частоту, амплитуду, тренд и состояние high. Использует зеркала + fallback на кеш.
"""

from __future__ import annotations
import json, logging, random, time
from pathlib import Path
from typing import Any, Dict, List, Optional
from utils import _get

# ─── кеш и история ────────────────────────────────────────────────
CACHE_PATH = Path.home() / ".cache" / "vaybometer" / "sr1.json"
CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)

def _save_point(freq: float, amp: float) -> None:
    pt = {"ts": time.time(), "freq": round(freq,3), "amp": round(amp,1)}
    try:
        hist = json.loads(CACHE_PATH.read_text())
    except:
        hist = []
    hist.append(pt)
    cutoff = time.time() - 7*24*3600
    hist = [p for p in hist if p["ts"] >= cutoff]
    CACHE_PATH.write_text(json.dumps(hist, ensure_ascii=False))

def _last_points(hours: int=24) -> List[Dict[str,Any]]:
    try:
        hist = json.loads(CACHE_PATH.read_text())
    except:
        return []
    cutoff = time.time() - hours*3600
    return [p for p in hist if p["ts"] >= cutoff]

# ─── список URL-ов с разными прокси ─────────────────────────────────
URLS = [
    "https://api.codetabs.com/v1/proxy?quest=https://api.glcoherence.org/v1/earth",
    "https://thingproxy.freeboard.io/fetch/https://api.glcoherence.org/v1/earth",
    "https://api.allorigins.win/raw?url=https://api.glcoherence.org/v1/earth",
    "https://api.glcoherence.org/v1/earth",
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

# ─── retry + backoff ───────────────────────────────────────────────
def _fetch_schumann_data(url: str, attempts: int=7, backoff: float=2.0) -> Optional[Any]:
    logging.info("Schumann fetch %s (attempts=%d)", url, attempts)
    for i in range(attempts):
        data = _get(url)
        if data:
            logging.info("Schumann: got data from %s", url)
            return data
        wait = backoff ** i
        logging.warning("Schumann retry %d/%d after %.1fs", i+1, attempts, wait)
        time.sleep(wait)
    logging.error("Schumann: all attempts failed for %s", url)
    return None

# ─── вычисление тренда ────────────────────────────────────────────
def _compute_trend(pts: List[Dict[str,Any]], hours: int=24) -> str:
    if len(pts) < 3:
        return "→"
    *prev, last = pts
    avg = sum(p["freq"] for p in prev) / len(prev)
    delta = last["freq"] - avg
    if delta >= 0.1:  return "↑"
    if delta <= -0.1: return "↓"
    return "→"

# ─── основная функция ─────────────────────────────────────────────
logging.info("Schumann: start retrieval")
def get_schumann() -> Dict[str,Any]:
    for url in URLS:
        raw = _fetch_schumann_data(url)
        if not raw:
            continue
        try:
            if isinstance(raw, dict) and "data" in raw:
                data = raw["data"].get("sr1", raw["data"])
            else:
                data = raw
            fv = data.get("frequency_1") or data.get("frequency")
            av = data.get("amplitude_1")  or data.get("amplitude")
            if fv is None or av is None:
                raise ValueError("freq/amp absent")
            freq, amp = float(fv), float(av)
            _save_point(freq, amp)
            pts = _last_points(24)
            return {
                "freq":  round(freq,2),
                "amp":   round(amp,1),
                "high":  freq>8.0 or amp>100.0,
                "trend": _compute_trend(pts),
            }
        except Exception as e:
            logging.warning("Schumann parse error %s: %s", url, e)

    # fallback на кеш
    pts48 = _last_points(48)
    if pts48:
        last = pts48[-1]
        return {
            "freq":  last["freq"],
            "amp":   last["amp"],
            "high":  last["freq"]>8.0 or last["amp"]>100.0,
            "trend": _compute_trend(pts48),
            "cached": True,
        }

    return {"msg": random.choice(SCH_QUOTES)}

# ─── CLI-тест ─────────────────────────────────────────────────────
if __name__ == "__main__":
    from pprint import pprint
    pprint(get_schumann())
    print("trend:", get_schumann().get("trend"))
