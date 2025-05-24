#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
gen_lunar_calendar.py  
Генерация lunar_calendar.json для текущего месяца:
  - phase       — фаза Луны (иконка, название, знак, % освещ.) + эффект
  - advice      — GPT-совет с конкретным призывом к действию
  - next_event  — краткий анонс ближайшего события (<– через n дней)
  - favorable_days, unfavorable_days — списки дат (как раньше)
"""

import json
import math
import datetime as dt
from pathlib import Path
from typing import Any, Dict

import pendulum
import swisseph as swe

from gpt import gpt_blurb
from astro import upcoming_event

# ─────────── Константы ───────────────────────────────────────────────
SYNODIC_MONTH = 29.53058867

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


def compute_phase(d: pendulum.Date) -> str:
    """Вычисляем фазу Луны, знак, % освещ. и эффект для конкретной даты d."""
    # переводим в UTC datetime
    ref = dt.datetime(d.year, d.month, d.day)
    jd = swe.julday(ref.year, ref.month, ref.day)
    sun_lon = swe.calc_ut(jd, swe.SUN)[0][0]
    moon_lon = swe.calc_ut(jd, swe.MOON)[0][0]

    phase = ((moon_lon - sun_lon + 360) % 360) / 360  # 0…1
    illum = round(abs(math.cos(math.pi * phase)) * 100)
    icon = MOON_ICONS[int(phase * 8) % 8]

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


def generate_calendar(year: int, month: int) -> Dict[str, Dict[str, Any]]:
    result: Dict[str, Dict[str, Any]] = {}
    d = pendulum.date(year, month, 1)

    while d.month == month:
        key = d.to_date_string()  # "YYYY-MM-DD"

        # 1) Phase
        phase_str = compute_phase(d)

        # 2) GPT-совет (summary + список tips)
        summary, tips = gpt_blurb(phase_str)
        advice = " ".join(tips) if tips else summary

        # 3) Следующее событие
        nxt = upcoming_event() or ""

        # 4) Старые списки (можно оставить ваш алгоритм расчёта)
        # здесь просто заглушка: все дни первого квартала — благоприятны, всё остальное — нейтрально
        days_since = (d - pendulum.date(year, month, 1)).days
        favorable = []
        unfavorable = []
        # пример: первая четверть (day ~ SYNODIC_MONTH*0.25) как благоприятный
        if 0 <= days_since < SYNODIC_MONTH * 0.25:
            favorable.append(d.day)
        else:
            unfavorable.append(d.day)

        result[key] = {
            "phase":            phase_str,
            "advice":           advice,
            "next_event":       nxt,
            "favorable_days":   favorable,
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
