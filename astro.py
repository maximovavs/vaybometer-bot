#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
astro.py

• MOON_ICONS — 8-символьная «луна-бар».
• moon_phase()      → строка c фазой Луны и эффектом знака.
• upcoming_event()  → анонс самого заметного события ≈ через 3 дня.
• astro_events()    → **ровно две строки**:
      1. сегодняшняя фаза Луны;
      2. «→ …» с предстоящим событием-пояснением.
"""

from __future__ import annotations
import math, datetime as dt
from typing import List, Optional
import swisseph as swe

# ── справочники ──────────────────────────────────────────────────
SIGNS = [
    "Овне", "Тельце", "Близнецах", "Раке", "Льве", "Деве",
    "Весах", "Скорпионе", "Стрельце", "Козероге", "Водолее", "Рыбах",
]
EFFECT = [
    "придаёт решимости", "настраивает на комфорт", "усиливает любознательность",
    "делает эмоциональнее", "поднимает самооценку", "стимулирует порядок",
    "прощает мелочи", "углубляет чувства", "толкает к открытиям",
    "фокусирует на целях", "будит идеи", "зовёт к приключениям",
]

MOON_ICONS = "🌑🌒🌓🌔🌕🌖🌗🌘"

# ── фаза Луны сегодня ───────────────────────────────────────────
def moon_phase() -> str:
    now = dt.datetime.utcnow()
    jd  = swe.julday(now.year, now.month, now.day)
    sun_lon,  *_ = swe.calc_ut(jd, swe.SUN)[0]
    moon_lon, *_ = swe.calc_ut(jd, swe.MOON)[0]

    phase  = ((moon_lon - sun_lon + 360) % 360) / 360   # 0…1
    illum  = round(abs(math.cos(math.pi * phase)) * 100)
    icon   = MOON_ICONS[int(phase * 8) % 8]

    if illum < 5:
        name = "Новолуние"
    elif illum > 95:
        name = "Полнолуние"
    elif phase < 0.5:
        name = "Растущая Луна"
    else:
        name = "Убывающая Луна"

    sign = SIGNS[int(moon_lon // 30) % 12]
    eff  = EFFECT[int(moon_lon // 30) % 12]

    return f"{icon} {name} в {sign} ({illum}% освещ.) — {eff}"

# ── ближайший «большой» астрособытие ────────────────────────────
def upcoming_event(days: int = 3) -> Optional[str]:
    """
    Возвращает краткий анонс события через `days` суток.
    По-умолчанию выводим пример затмения; при необходимости
    можно подключить реальное API / расчёты.
    """
    if days == 3:
        return "Через 3 дня частичное солнечное затмение — усиление интуиции"
    return None

# ── интерфейс для post.py ───────────────────────────────────────
def astro_events() -> List[str]:
    """
    ▸ [0] сегодняшняя фаза Луны
    ▸ [1] «→ …» предстоящий анонс (если есть)
    """
    events = [moon_phase()]
    if ann := upcoming_event():
        events.append(f"→ {ann}")
    return events


# ── тестовый запуск:  python -m astro ───────────────────────────
if __name__ == "__main__":
    from pprint import pprint
    pprint(astro_events())
