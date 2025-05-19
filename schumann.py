#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
schumann.py
~~~~~~~~~~~
• Берёт freq/amp из двух открытых API и кеширует 7 дней истории.
• high = freq > 8.0 Гц или amp > 100 пТ.
• Возвращает сразу и тренд (↑/↓/→).
"""

from __future__ import annotations
import json
import logging
import random
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from utils import _get

# ────────── настройки кеша ─────────────────────────────────────────
CACHE_PATH = Path.home() / ".cache" / "vaybometer" / "sr1.json"
CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)

# ────────── источники данных ─────────────────────────────────────────
URLS = (
    "https://api.glcoherence.org/v1/earth",
    "https://gci-api.ucsd.edu/data/latest",
)

# Цитаты, если данные недоступны
SCH_QUOTES = [
    "датчики молчат — ретрит 🌱",
    "кошачий мяу-фактор заглушил сенсоры 😸",
    "волны ушли ловить чаек 🐦",
    "показания медитируют 🧘",
    "данные в отпуске 🏝️",
    "Шуман спит — не будим 🔕",
    "тишина в эфире… 🎧",
]

# ────────── функции работы с кешем ────────────────────────────────────
def _save_point(freq: float, amp: float) -> None:
    """Добавляет точку {ts, freq, amp} и хранит 7 дней истории"""
    point = {"ts": time.time(), "freq": round(freq, 3), "amp": round(amp, 1)}
    try:
        history: List[Dict[str, Any]] = json.loads(CACHE_PATH.read_text())
    except Exception:
        history = []
    history.append(point)
    week_ago = time.time() - 7 * 24 * 3600
    history = [p for p in history if p["ts"] >= week_ago]
    CACHE_PATH.write_text(json.dumps(history, ensure_ascii=False))


def _last_points(hours: int = 24) -> List[Dict[str, Any]]:
    """Возвращает список точек за последние `hours` часов из кеша"""
    try:
        history: List[Dict[str, Any]] = json.loads(CACHE_PATH.read_text())
    except Exception:
        return []
    cutoff = time.time() - hours * 3600
    return [p for p in history if p["ts"] >= cutoff]

# ────────── тренд частоты Шумана ─────────────────────────────────────
def get_schumann_trend(hours: int = 24) -> str:
    """
    ↑ если последняя freq > средней за `hours` на ≥0.1 Гц,
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

# ────────── основная функция ─────────────────────────────────────────
def get_schumann() -> Dict[str, Any]:
    """
    Возвращает словарь:
      {
        'freq': float,       # частота резонанса в Гц
        'amp': float,        # амплитуда в пТ
        'high': bool,        # True, если freq>8.0 или amp>100
        'trend': '↑'|'↓'|'→',# тренд частоты за 24ч
        'cached': bool       # True, если данные из кеша
      }
    или при полном отсутствии данных:
      {'msg': str}
    """
    for url in URLS:
        data = _get(url)
        if not data:
            continue
        try:
            # для API с ключом 'data'
            if isinstance(data, dict) and 'data' in data:
                data = data['data'].get('sr1', data['data'])
            freq_raw = data.get('frequency_1') or data.get('frequency')
            amp_raw  = data.get('amplitude_1')  or data.get('amplitude')
            if freq_raw is None or amp_raw is None:
                raise ValueError('freq/amp absent')
            freq = float(freq_raw)
            amp  = float(amp_raw)
            _save_point(freq, amp)
            return {
                'freq':  round(freq, 2),
                'amp':   round(amp,  1),
                'high':  (freq > 8.0) or (amp > 100.0),
                'trend': get_schumann_trend(),
            }
        except Exception as e:
            logging.warning('schumann parse %s: %s', url, e)
    # fallback: данные из кеша за последние 48ч
    pts = _last_points(48)
    if pts:
        last = pts[-1]
        return {
            'freq':  last['freq'],
            'amp':   last['amp'],
            'high':  (last['freq'] > 8.0) or (last['amp'] > 100.0),
            'trend': get_schumann_trend(48),
            'cached': True,
        }
    # совсем нет данных
    return {'msg': random.choice(SCH_QUOTES)}

# ────────── CLI-тестирование ────────────────────────────────────────
if __name__ == '__main__':
    from pprint import pprint
    print('Schumann:', end=' '); pprint(get_schumann())
    print('Trend 24h:', get_schumann_trend())
