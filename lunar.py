# lunar.py
#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
lunar.py  • функция get_day_lunar_info для поста и для генерации месячного календаря.
Ожидает файл lunar_calendar.json в корне репозитория, 
куда ежемесячно записывается подробный календарь.
"""

from __future__ import annotations
import json
from pathlib import Path
import pendulum
from typing import Any, Dict, Optional

def get_day_lunar_info(d: pendulum.Date) -> Optional[Dict[str, Any]]:
    """
    Возвращает информацию по дате d из lunar_calendar.json:
      {
        'phase':         название фазы + знак + процент освещённости,
        'advice':        конкретный призыв к действию,
        'next_event':    краткая ссылка на ближайшее событие,
        'favorable':     [список благоприятных дней месяца],
        'unfavorable':   [список неблагоприятных дней месяца],
      }
    или None, если файла нет или для даты нет записи.
    """
    fn = Path(__file__).parent / "lunar_calendar.json"
    if not fn.exists():
        return None

    data = json.loads(fn.read_text(encoding="utf-8"))
    key = d.format("YYYY-MM-DD")
    info = data.get(key)
    if not info:
        return None

    return {
        "phase":       info.get("phase", ""),
        "advice":      info.get("advice", ""),
        "next_event":  info.get("next_event", ""),
        "favorable":   info.get("favorable_days", []),
        "unfavorable": info.get("unfavorable_days", []),
    }

# Простой тест
if __name__ == "__main__":
    today = pendulum.today()
    print(get_day_lunar_info(today))
