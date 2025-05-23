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
    Для текущей даты (Asia/Nicosia) читает из lunar_calendar.json запись:
      {
        'phase':       строка с названием и знаком фазы Луны,
        'advice':      общий совет на этот день,
        'favorable':   [список благоприятных дней месяца],
        'unfavorable': [список неблагоприятных дней месяца],
      }
    и возвращает список:
      [ "<название фазы и знак>", "<рекомендация на сегодня>" ].
    Если данных нет — возвращает пустой список.
    """
    tz = pendulum.timezone("Asia/Nicosia")
    today = pendulum.now(tz).date()
    info: Optional[Dict[str, Any]] = get_day_lunar_info(today)
    if not info:
        return []

    events: List[str] = []
    phase = info.get("phase", "").strip()
    if phase:
        events.append(phase)

    advice = info.get("advice", "").strip()
    if advice:
        events.append(advice)

    return events

if __name__ == "__main__":
    from pprint import pprint
    pprint(astro_events())
