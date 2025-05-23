#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
gen_lunar_calendar.py
~~~~~~~~~~~~~~~~~~~~~~
Генерирует файл lunar_calendar.json с лунным календарём
на указанный (или текущий) месяц. Для каждого дня выдаёт:
  - date         (YYYY-MM-DD)
  - timestamp    (начало дня в UTC)
  - phase        (название фазы Луны)
  - illumination (процент освещённости)
  - zodiac       (знак Зодиака, в котором Луна)
"""

import sys
import json
import calendar
from pathlib import Path

import pendulum
from lunar import get_day_lunar_info  # Функция из вашего lunar.py

def main():
    # 1) Парсим аргументы: год и месяц или текущие
    if len(sys.argv) == 3:
        year = int(sys.argv[1])
        month = int(sys.argv[2])
    else:
        now = pendulum.now('UTC')
        year, month = now.year, now.month

    # 2) Сколько дней в месяце
    days_in_month = calendar.monthrange(year, month)[1]

    result = []
    for day in range(1, days_in_month + 1):
        # создаём pendulum.Date
        d = pendulum.date(year, month, day)
        # явное начало этого дня в UTC
        dt_utc = pendulum.datetime(year, month, day, 0, 0, 0, tz='UTC')
        ts = int(dt_utc.timestamp())

        # берём лунную информацию
        info = get_day_lunar_info(d)
        # ожидаем: {'phase': str, 'illumination': float, 'zodiac': str}

        result.append({
            'date':         d.to_date_string(),
            'timestamp':    ts,
            'phase':        info.get('phase', ''),
            'illumination': info.get('illumination'),
            'zodiac':       info.get('zodiac', ''),
        })

    # 3) Сохраняем в JSON
    out = Path(__file__).parent / 'lunar_calendar.json'
    with open(out, 'w', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print(f'✓ lunar_calendar.json сгенерирован для {year}-{month:02d}')

if __name__ == '__main__':
    main()
