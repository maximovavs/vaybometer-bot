#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
gen_lunar_calendar.py
~~~~~~~~~~~~~~~~~~~~~
Генерирует файл lunar_calendar.json для текущего месяца.

Каждый день сохраняет:
  - phase:   строка с фазой Луны
  - advice:  общий совет на этот день
  - favorable_days:   список благоприятных дат (заглушка)
  - unfavorable_days: список неблагоприятных дат (заглушка)
"""

import json
from pathlib import Path
import pendulum

def compute_lunar_phase(d: pendulum.Date) -> str:
    """
    Простейшая эмульция фазы Луны:
      - вычисляем возраст луны от опорного новолуния
      - возвращаем одну из фаз
    """
    # Срок синодического месяца в днях
    SYNODIC_MONTH = 29.530588853
    # Опорная точка (пример) — новолуние 11 мая 2025
    ref = pendulum.date(2025, 5, 11)
    days_since = (d - ref).days % SYNODIC_MONTH
    if days_since < 1:
        return "Новолуние"
    if days_since < SYNODIC_MONTH * 0.25:
        return f"Растущая Луна ({int(days_since)} дн.)"
    if days_since < SYNODIC_MONTH * 0.50:
        return "Первая четверть"
    if days_since < SYNODIC_MONTH * 0.75:
        return f"Полнолуние"
    if days_since < SYNODIC_MONTH * 0.875:
        return f"Убывающая Луна ({int(days_since)} дн.)"
    return "Последняя четверть"

def generate_calendar(year: int, month: int) -> dict[str, dict]:
    """
    Перебирает все дни указанного месяца и собирает словарь:
      {
        "YYYY-MM-DD": {
           "phase": "...",
           "advice": "...",
           "favorable_days": [...],
           "unfavorable_days": [...]
        },
        ...
      }
    """
    start = pendulum.date(year, month, 1)
    end   = start.end_of('month')
    result: dict[str, dict] = {}
    d = start
    while d <= end:
        phase = compute_lunar_phase(d)
        # Здесь можно подставить свою логику советов и списков
        advice = f"Сегодня {phase.lower()}, отличное время для медитации и общения с природой."
        # Для примера: делаем каждый день благоприятным
        favorable = [d.day]
        unfavorable = []

        result[d.format("YYYY-MM-DD")] = {
            "phase": phase,
            "advice": advice,
            "favorable_days": favorable,
            "unfavorable_days": unfavorable,
        }
        d = d.add(days=1)
    return result

def main():
    today = pendulum.today()
    data = generate_calendar(today.year, today.month)
    out = Path(__file__).parent / "lunar_calendar.json"
    out.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    print(f"✅ Файл {out.name} сгенерирован для {today.format('MMMM YYYY')}")

if __name__ == "__main__":
    main()
