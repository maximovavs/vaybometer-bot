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
    Возвращает информацию по дате d из lunar_calendar.json.

    JSON-запись для каждой даты должна содержать по крайней мере ключи:
      - "phase":            str
      - "percent":          int
      - "sign":             str
      - "aspects":          List[str]
      - "void_of_course":   Dict[str, str]
      - "next_event":       str
      - "advice":           List[str]
      - "favorable_days":   Dict[str, Dict[str, List[int]]] 
          где внутри каждой категории (например, "general", "shopping", и т. д.) 
          есть под-ключи "favorable" и "unfavorable"

    Функция возвращает саму запись (словарь) из JSON либо None, если:
      • файл lunar_calendar.json не найден или невалиден,
      • для указанной даты d нет записи.
    """
    fn = Path(__file__).parent / "lunar_calendar.json"
    if not fn.exists():
        return None

    try:
        data = json.loads(fn.read_text(encoding="utf-8"))
    except Exception:
        return None

    # Строковый ключ в формате "YYYY-MM-DD"
    key = d.format("YYYY-MM-DD")
    rec = data.get(key)
    if not rec:
        return None

    # Возвращаем всю структуру записи «как есть»
    return rec


# Тестовый запуск
if __name__ == "__main__":
    today = pendulum.now().date()
    from pprint import pprint
    pprint(get_day_lunar_info(today))