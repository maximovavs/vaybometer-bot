#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
astro.py  • возвращает для сегодняшнего дня два ключевых поля из lunar_calendar.json:
   1) фаза + первый совет
   2) next_event
"""

from __future__ import annotations
import pendulum
from typing import Any, Dict, List, Optional
from lunar import get_day_lunar_info

def astro_events() -> List[str]:
    """
    Для текущей даты в зоне Asia/Nicosia читает из lunar_calendar.json:
      {
        "phase":       "...",
        "advice":      [...],
        "next_event":  "...",
        ...
      }
    и возвращает:
      [
        "phase — advice[0]",
        "next_event"
      ]
    Если данных нет — возвращает пустой список.
    """
    tz = pendulum.timezone("Asia/Nicosia")
    today = pendulum.now(tz).date()
    info: Optional[Dict[str, Any]] = get_day_lunar_info(today)
    if not info:
        return []

    phase = info.get("phase", "").strip()
    advice_list = info.get("advice", [])
    next_event = info.get("next_event", "").strip()

    events: List[str] = []
    if phase and advice_list:
        # соединяем фазу и первый совет
        events.append(f"{phase} — {advice_list[0].strip()}")

    if next_event:
        events.append(next_event)

    return events

if __name__ == "__main__":
    from pprint import pprint
    pprint(astro_events())
