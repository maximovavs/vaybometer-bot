#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
astro.py  • возвращает для сегодняшнего дня фазу Луны и совет из lunar_calendar.json
"""

from __future__ import annotations
import pendulum
from typing import Any, Dict, List, Optional
from lunar import get_day_lunar_info

def astro_events() -> List[str]:
    """
    Для текущей даты читает из lunar_calendar.json запись:
      {
        'phase':       строка с названием и знаком фазы Луны,
        'advice':      общий совет на этот день,
        'favorable':   [список благоприятных дней месяца],
        'unfavorable': [список неблагоприятных дней месяца],
      }
    и возвращает список строк [phase, advice].
    Если данных нет — возвращает пустой список.
    """
    today = pendulum.now().date()
    info: Optional[Dict[str, Any]] = get_day_lunar_info(today)
    if not info:
        return []
    events: List[str] = []
    # первая строка — фаза Луны
    phase = info.get("phase", "").strip()
    if phase:
        events.append(phase)
    # вторая строка — совет на сегодня
    advice = info.get("advice", "").strip()
    if advice:
        events.append(advice)
    return events

if __name__ == "__main__":
    from pprint import pprint
    pprint(astro_events())
