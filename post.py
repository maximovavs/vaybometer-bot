#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
schumann.py
~~~~~~~~~~~
Резонанс Шумана — частота/амплитуда первой гармоники (SR-1).

Функции
--------
get_schumann() -> dict
    Возвращает последние показания и флаг «high»
get_schumann_trend(hours=24) -> str
    Стрелка ↑ / ↓ / → в зависимости от тренда за *hours*

История
--------
• Каждый успешный замер пишется в «~/.cache/sr1.json»
  [{ts: ISO8601, freq:…, amp:…}, …]
• Файл ограничивается 72 часами данных (≈ 72 записи по одному в час)
"""

from __future__ import annotations

import json
import logging
import os
import random
from pathlib import Path
from typing import Any, Dict, List, Optional

import pendulum

from utils import _get  # HTTP-обёртка с retry

# ────────── константы ──────────────────────────────────────────
CACHE_DIR = Path.home() / ".cache"
CACHE_DIR.mkdir(exist_ok=True)
HISTORY_FILE = CACHE_DIR / "sr1.json"

SR_URLS = (
    "https://api.glcoherence.org/v1/earth",        # JSON {frequency_1, amplitude_1}
    "https://gci-api.ucsd.edu/data/latest",        # JSON {data: {sr1:{frequency,…}}}
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


# ────────── helpers ────────────────────────────────────────────
def _load_history() -> List[Dict[str, Any]]:
    if HISTORY_FILE.exists():
        try:
            return json.loads(HISTORY_FILE.read_text())
        except Exception:
            logging.warning("SR1 history corrupt - recreating")
    return []


def _save_history(hist: List[Dict[str, Any]]) -> None:
    # Оставляем только записи за последние 72 ч.
    cutoff = pendulum.now("UTC").subtract(hours=72)
    hist = [h for h in hist if pendulum.parse(h["ts"]) >= cutoff]
    try:
        HISTORY_FILE.write_text(json.dumps(hist, ensure_ascii=False))
    except Exception as e:
        logging.warning("SR1 history save error: %s", e)


def _append_history(freq: float, amp: float) -> None:
    hist = _load_history()
    hist.append({"ts": pendulum.now("UTC").to_iso8601_string(),
                 "freq": round(freq, 2),
                 "amp": round(amp, 1)})
    _save_history(hist)


# ────────── public API ─────────────────────────────────────────
def get_schumann() -> Dict[str, Any]:
    """
    Сначала пытается получить данные по очереди из SR_URLS.
    • high = True, если freq > 8 Гц **или** amp > 100.
    • При успехе кеширует значение в историю.
    • При двух ошибках возвращает {"msg": <случайная фраза> }.
    """
    for url in SR_URLS:
        j = _get(url)
        if not j:
            continue
        try:
            if "data" in j:                 # формат GCI-API
                j = j["data"]["sr1"]
            freq = float(j.get("frequency_1") or j.get("frequency"))
            amp  = float(j.get("amplitude_1") or j.get("amplitude"))
        except Exception as e:
            logging.warning("Schumann parse %s: %s", url, e)
            continue

        _append_history(freq, amp)          # пишем историю
        return {
            "freq": round(freq, 2),
            "amp":  round(amp, 1),
            "high": (freq > 8.0) or (amp > 100.0),
        }

    # обе попытки не удались
    return {"msg": random.choice(SCH_QUOTES)}


def get_schumann_trend(hours: int = 24) -> str:
    """
    Возвращает стрелку тренда частоты (↑ / ↓ / →) за последние *hours*.
    Если данных недостаточно — '→'.
    """
    hist = _load_history()
    if len(hist) < 2:
        return "→"

    now      = pendulum.now("UTC")
    earlier: Optional[Dict[str, Any]] = None
    latest   = hist[-1]

    target_ts = now.subtract(hours=hours)
    # ищем запись, ближайшую, но <= целевого времени
    for rec in reversed(hist):
        if pendulum.parse(rec["ts"]) <= target_ts:
            earlier = rec
            break
    if not earlier:
        return "→"

    diff = latest["freq"] - earlier["freq"]
    if diff >= 0.05:     # +0.05 Гц и более — рост
        return "↑"
    if diff <= -0.05:    # −0.05 Гц и менее — падение
        return "↓"
    return "→"


# ────────── CLI тест  `python -m schumann` ──────────────────────
if __name__ == "__main__":           # demo
    from pprint import pprint

    info = get_schumann()
    pprint(info)

    if "freq" in info:
        arrow = get_schumann_trend()
        print(f"Тренд 24 ч: {arrow}")
