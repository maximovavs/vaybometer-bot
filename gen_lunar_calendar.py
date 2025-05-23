#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
gen_lunar_calendar.py
~~~~~~~~~~~~~~~~~~~~~~
Генерирует файл lunar_calendar.json с лунным календарём
на указанный (или текущий) месяц. Для каждого дня выдаёт:
  - date        (YYYY-MM-DD)
  - timestamp   (начало дня в UTC)
  - phase       (название фазы Луны)
  - illumination (процент освещённости)
  - zodiac      (знак Зодиака, в котором Луна)
"""

import sys
import json
import calendar
from pathlib import Path

import pendulum
from lunar import get_day_lunar_info  # ваш модуль, который возвращает словарь с ключами 'phase', 'illumination', 'zodiac'

def main():
    # 1) парсим аргументы: если переданы, берём год и месяц, иначе – текущие
    if len(sys.argv) == 3:
        year = int(sys.argv[1])
        month = int(sys.argv[2])
    else:
        now = pendulum.now('UTC')
        year, month = now.year, now.month

    # 2) готовим список всех дней месяца
    days_in_month = calendar.monthrange(year, month)[1]
    calendar_data = []

    for day in range(1, days_in_month + 1):
        d = pendulum.date(year, month, day)

        # 3) получаем _любой_ момент этого дня в UTC (возьмём полночь)
        dt_utc = d.at(0, 0, 0).in_tz('UTC')
        timestamp = int(dt_utc.timestamp())

        # 4) берём лунную информацию из вашего модуля lunar.py
        info = get_day_lunar_info(d)
        # ожидаем, что info == {
        #    'phase': 'Новолуние' / 'Убывающая' / ...,
        #    'illumination': 42.3,         # float – проценты
        #    'zodiac': 'Водолея'           # строка
        # }

        # 5) собираем итоговую запись
        entry = {
            'date':          d.to_date_string(),
            'timestamp':     timestamp,
            'phase':         info.get('phase', ''),
            'illumination':  info.get('illumination', None),
            'zodiac':        info.get('zodiac', ''),
        }
        calendar_data.append(entry)

    # 6) сохраняем в JSON
    out_path = Path(__file__).parent / 'lunar_calendar.json'
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump(calendar_data, f, ensure_ascii=False, indent=2)

    print(f'✓ lunar_calendar.json сгенерирован ({year}-{month:02d})')

if __name__ == '__main__':
    main()
