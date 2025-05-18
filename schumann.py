#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
schumann.py
~~~~~~~~~~~

• Получает частоту / амплитуду резонанса Шумана из двух открытых API.
• Сохраняет историю измерений в «~/cache/sr1.json».
• get_schumann() → словарь:
      {"freq": 7.83, "amp": 48.2, "high": False}
      {"freq": 8.12, "amp":120.3, "high": True}
      {"msg": "..."}                – при недоступности обоих API
• get_schumann_trend(hours=24) → "↑" | "↓" | "→"
"""

from __future__ import annotations

import json, os, time, logging, random, datetime as dt
from pathlib import Path
from typing import Dict, Any, List

from utils import _get

# ── конфигурация ──────────────────────────────────────────────────
CACHE_DIR  = Path(os.path.expanduser("~/cache"))
CACHE_FILE = CACHE_DIR / "sr1.json"
CACHE_DIR.mkdir(parents=True, exist_ok=True)
MAX_AGE_H  = 48                     # храним не более 48 ч истории
SCH_QUOTES = [
    "датчики молчат — ретрит 🌱",
    "кошачий мяу-фактор заглушил сенсоры 😸",
    "волны ушли ловить чаек 🐦",
    "показания медитируют 🧘",
    "данные в отпуске 🏝️",
    "Шуман спит — не будим 🔕",
    "тишина в эфире… 🎧",
]

# ── вспомогательные функции работы с историей ────────────────────
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
    """Удаляет записи старше MAX_AGE_H часов."""
    cutoff = time.time() - MAX_AGE_H * 3600
    return [h for h in hist if h["ts"] >= cutoff]


# ── основное получение данных ────────────────────────────────────
def _fetch_once() -> Dict[str, Any] | None:
    """Пробует два публичных эндпойнта и возвращает dict либо None."""
    for url in (
        "https://api.glcoherence.org/v1/earth",
        "https://gci-api.ucsd.edu/data/latest",
    ):
        j = _get(url)
        if not j:
            continue
        try:
            # второй API оборачивает в "data" -> "sr1"
            if "data" in j:
                j = j["data"]["sr1"]

            freq = j.get("frequency_1") or j.get("frequency")
            amp  = j.get("amplitude_1") or j.get("amplitude")
            if freq is None or amp is None:
                raise KeyError("missing fields")

            freq_val = float(freq)
            amp_val  = float(amp)

            return {"freq": round(freq_val, 2),
                    "amp":  round(amp_val, 1)}
        except Exception as e:
            logging.warning("Schumann parse error (%s): %s", url, e)
    return None


def get_schumann() -> Dict[str, Any]:
    """
    • При успехе: записывает точку в историю и возвращает freq/amp/high.
    • При отказе обоих API: возвращает {"msg": "..."}.
    """
    data = _fetch_once()
    if not data:
        return {"msg": random.choice(SCH_QUOTES)}

    # критерий «⚡️high»: частота > 8 Гц *или* амплитуда > 100
    freq, amp = data["freq"], data["amp"]
    if freq > 8.0 or amp > 100:
        data["high"] = True

    # --- сохранение в историю ------------------------------------
    hist = _load_history()
    hist = _prune(hist)
    hist.append({"ts": time.time(), "freq": freq, "amp": amp})
    _save_history(hist)

    return data


# ── расчёт тренда ────────────────────────────────────────────────
def get_schumann_trend(hours: int = 24) -> str:
    """
    Возвращает:
        "↑" — частота выросла >0.05 Гц
        "↓" — частота упала   >0.05 Гц
        "→" — изменений нет / данных мало
    """
    hist = [h for h in _load_history() if h["ts"] >= time.time() - hours*3600]
    if len(hist) < 2:
        return "→"

    start, end = hist[0]["freq"], hist[-1]["freq"]
    diff = end - start
    if diff >= 0.05:
        return "↑"
    if diff <= -0.05:
        return "↓"
    return "→"


# ── CLI-тест:  python -m schumann ────────────────────────────────
if __name__ == "__main__":
    from pprint import pprint
    print("Current reading:")
    pprint(get_schumann())
    print("24-hour trend:", get_schumann_trend())
