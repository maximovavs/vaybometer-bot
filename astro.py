#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
astro.py  • возвращает для сегодняшнего дня основные рекомендации из lunar_calendar.json:
   1) фаза + первый совет
   2) остальные советы в виде списка
   3) next_event
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
    и возвращает список строк:
      [
        "phase — advice[0]",
        "• advice[1]",
        "• advice[2]",
        "next_event"
      ]
    Если данных нет — возвращает пустой список.
    """
    tz    = pendulum.timezone("Asia/Nicosia")
    today = pendulum.now(tz).date()
    info: Optional[Dict[str, Any]] = get_day_lunar_info(today)
    if not info:
        return []

    phase       = info.get("phase", "").strip()
    advice_list = info.get("advice", [])
    next_event  = info.get("next_event", "").strip()

    events: List[str] = []
    if phase and advice_list:
        # первая строка: фаза + первый совет
        events.append(f"{phase} — {advice_list[0].strip()}")
        # далее второй и третий совет как пункты списка
        for adv in advice_list[1:]:
            events.append(f"• {adv.strip()}")

    if next_event:
        events.append(next_event)

    return events

# Тестовый запуск
if __name__ == "__main__":
    from pprint import pprint
    pprint(astro_events())
