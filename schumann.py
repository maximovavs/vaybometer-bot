#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
schumann.py
~~~~~~~~~~~

• Берёт данные резонанса Шумана из двух публичных API
  (glcoherence и gci-api UCSD).
• Хранит историю чтений в  ~/cache/sr1.json  (макс. 48 ч).
• get_schumann()           →  {"freq":7.83,"amp":48.2,"high":False}
                              или {"msg":"..."} когда истории нет.
• get_schumann_trend(24)   →  "↑" | "↓" | "→"  – динамика частоты.
"""

from __future__ import annotations
import os, json, time, random, logging, datetime as dt
from pathlib import Path
from typing import Dict, Any, List

from utils import _get

# ── настройки кеша ────────────────────────────────────────────────
CACHE_DIR  = Path(os.path.expanduser("~/cache"))
CACHE_FILE = CACHE_DIR / "sr1.json"
CACHE_DIR.mkdir(parents=True, exist_ok=True)
MAX_AGE_H  = 48                       # храним не более 48 ч в истории

SCH_QUOTES = [
    "датчики молчат — ретрит 🌱",
    "кошачий мяу-фактор заглушил сенсоры 😸",
    "волны ушли ловить чаек 🐦",
    "показания медитируют 🧘",
    "данные в отпуске 🏝️",
    "Шуман спит — не будим 🔕",
    "тишина в эфире… 🎧",
]

# ── работа с историей ────────────────────────────────────────────
def _load_history() -> List[Dict[str, float]]:
    if CACHE_FILE.exists():
        try:
            with CACHE_FILE.open() as f:
                return json.load(f)
        except Exception:
            logging.warning("Schumann history corrupted – reset")
    return []

def _save_history(hist: List[Dict[str, float]]) -> None:
    try:
        with CACHE_FILE.open("w") as f:
            json.dump(hist, f)
    except Exception as e:
        logging.warning("Schumann history save error: %s", e)

def _prune(hist: List[Dict[str, float]]) -> List[Dict[str, float]]:
    cutoff = time.time() - MAX_AGE_H * 3600
    return [h for h in hist if h["ts"] >= cutoff]

# ── единичное обращение к API ────────────────────────────────────
def _fetch_once() -> Dict[str, float] | None:
    for url in (
        "https://api.glcoherence.org/v1/earth",
        "https://gci-api.ucsd.edu/data/latest",
    ):
        j = _get(url)
        if not j:
            continue
        try:
            # второй сервис оборачивает полезное в ["data"]["sr1"]
            if "data" in j:
                j = j["data"]["sr1"]

            freq = j.get("frequency_1") or j.get("frequency")
            amp  = j.get("amplitude_1") or j.get("amplitude")
            if freq is None or amp is None:
                raise ValueError("missing fields")

            return {"freq": round(float(freq), 2),
                    "amp":  round(float(amp), 1)}
        except Exception as e:
            logging.warning("Schumann parse (%s): %s", url, e)
    return None

# ── публичные функции ────────────────────────────────────────────
def get_schumann() -> Dict[str, Any]:
    """
    Всегда пытается вернуть словарь с freq/amp/[high].
    • Если новые источники недоступны, берёт последний кэш-замер.
    • Если истории нет вовсе — возвращает {"msg":"..."}.
    """
    data = _fetch_once()
    hist = _prune(_load_history())

    # если API не ответили – fallback на последний кэш
    if data is None:
        if not hist:
            return {"msg": random.choice(SCH_QUOTES)}
        last = hist[-1]
        data = {"freq": last["freq"], "amp": last["amp"], "stale": True}

    # high-флаг
    if data["freq"] > 8.0 or data["amp"] > 100:
        data["high"] = True

    # запись в историю (только если это не устаревшая точка)
    if not data.get("stale"):
        hist.append({"ts": time.time(),
                     "freq": data["freq"],
                     "amp":  data["amp"]})
        _save_history(hist)

    return data


def get_schumann_trend(hours: int = 24) -> str:
    """
    Возвращает стрелку тренда за указанные часы:
        ↑  – выросло > 0.05 Гц
        ↓  – упало   > 0.05 Гц
        →  – без изменений / мало данных
    """
    span = time.time() - hours * 3600
    pts = [h for h in _load_history() if h["ts"] >= span]
    if len(pts) < 2:
        return "→"
    start, end = pts[0]["freq"], pts[-1]["freq"]
    diff = end - start
    if diff >= 0.05:
        return "↑"
    if diff <= -0.05:
        return "↓"
    return "→"

# ── CLI-тест ──────────────────────────────────────────────────────
if __name__ == "__main__":
    from pprint import pprint
    print("Current reading:")
    pprint(get_schumann())
    print("24-hour trend:", get_schumann_trend())
