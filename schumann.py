#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
schumann.py
~~~~~~~~~~~
• Получает частоту (Гц) и амплитуду (Р) основного резонанса Шумана (SR 1)
  из двух открытых API (GL Coherence / UCSD GCI).
• Сохраняет измерения в «~/.cache/vaybometer/sr1.json».
• high = True, если  freq > 8 Гц  ИЛИ  amp > 100 Р.
• get_schumann_trend(hours=24) → '↑' / '↓' / '→'  по сравнению
  с показанием N часов назад.
"""

from __future__ import annotations

import json, time, logging, random, pathlib
from typing import Dict, Any, List, Optional

from utils import _get

# ── файловый кэш ──────────────────────────────────────────────────
CACHE_DIR   = pathlib.Path.home() / ".cache" / "vaybometer"
CACHE_FILE  = CACHE_DIR / "sr1.json"
CACHE_DIR.mkdir(parents=True, exist_ok=True)      # гарантируем, что путь есть

# ── шутки-затычки ────────────────────────────────────────────────
SCH_QUOTES = [
    "датчики молчат — ретрит 🌱",
    "кошачий мяу-фактор заглушил сенсоры 😸",
    "волны ушли ловить чаек 🐦",
    "показания медитируют 🧘",
    "данные в отпуске 🏝️",
    "Шуман спит — не будим 🔕",
    "тишина в эфире… 🎧",
]

# ── внутренние утилиты ───────────────────────────────────────────
def _load_history() -> List[Dict[str, float]]:
    if CACHE_FILE.exists():
        try:
            return json.loads(CACHE_FILE.read_text())
        except Exception:
            logging.warning("Schumann cache corrupt – recreating")
    return []

def _save_history(hist: List[Dict[str, float]]) -> None:
    try:
        CACHE_FILE.write_text(json.dumps(hist, ensure_ascii=False))
    except Exception as e:
        logging.warning("Schumann cache save error: %s", e)

def _append_history(freq: float, amp: float) -> None:
    ts_now = int(time.time())
    hist   = _load_history()
    hist.append({"ts": ts_now, "freq": freq, "amp": amp})
    # оставляем записи лишь за последние 72 ч (избыточно для тренда)
    cutoff = ts_now - 72 * 3600
    hist   = [h for h in hist if h["ts"] >= cutoff]
    _save_history(hist)

# ── API запрос ───────────────────────────────────────────────────
_API_ENDPOINTS = (
    "https://api.glcoherence.org/v1/earth",       # JSON plain
    "https://gci-api.ucsd.edu/data/latest",       # JSON в  ["data"]["sr1"]
)

def _fetch_sr1() -> Optional[tuple[float, float]]:
    """Пробует оба эндпойнта и возвращает (freq, amp) или None."""
    for url in _API_ENDPOINTS:
        j = _get(url)
        if not j:
            continue
        try:
            # второй сервис оборачивает данные
            if "data" in j and "sr1" in j["data"]:
                j = j["data"]["sr1"]

            freq = j.get("frequency_1") or j.get("frequency")
            amp  = j.get("amplitude_1") or j.get("amplitude")
            if freq is None or amp is None:
                raise ValueError("missing fields")

            return float(freq), float(amp)
        except Exception as e:
            logging.warning("Schumann parse error (%s): %s", url, e)
    return None

# ── публичные функции ────────────────────────────────────────────
def get_schumann() -> Dict[str, Any]:
    """
    ▸ При успехе:
        {"freq": 7.83, "amp": 42.1, "high": False}
        {"freq": 8.11, "amp":123.4, "high": True}
    ▸ При недоступности источников:
        {"msg": "<случайная цитата>"}
    """
    sr = _fetch_sr1()
    if not sr:
        return {"msg": random.choice(SCH_QUOTES)}

    freq, amp = sr
    _append_history(freq, amp)                  # кешируем факт измерения

    return {
        "freq": round(freq, 2),
        "amp":  round(amp, 1),
        "high": (freq > 8.0) or (amp > 100.0),  # новое правило
    }

def get_schumann_trend(hours: int = 24) -> str:
    """
    Сравнивает текущую частоту с частотой `hours` назад.
    Возвращает стрелку:
       ↑  рост > 0.05 Гц
       ↓  падение < −0.05 Гц
       →  почти без изменений
    """
    hist = _load_history()
    if len(hist) < 2:
        return "→"

    ts_now = int(time.time())
    target = ts_now - hours * 3600

    past: Optional[float] = None
    for h in hist:
        if h["ts"] <= target:
            past = h["freq"]
            break
    if past is None:          # нет достаточной давности
        past = hist[0]["freq"]

    current = hist[-1]["freq"]
    diff    = current - past

    if diff > 0.05:
        return "↑"
    if diff < -0.05:
        return "↓"
    return "→"

# ── CLI-тест  :  python -m schumann ──────────────────────────────
if __name__ == "__main__":
    from pprint import pprint
    data = get_schumann()
    trend = get_schumann_trend()
    if "freq" in data:
        data["trend"] = trend
    pprint(data)
