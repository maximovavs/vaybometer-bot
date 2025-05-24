#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
gen_lunar_calendar.py
~~~~~~~~~~~~~~~~~~~~~
Генерирует файл lunar_calendar.json для текущего месяца.

Каждый день сохраняет:
  - phase:          строка с фазой Луны + знак + "(XX% освещ.)"
  - advice:         конкретный призыв к действию
  - next_event:     краткая ссылка на ближайшее астрособытие
  - favorable_days: список благоприятных дней месяца
  - unfavorable_days: список неблагоприятных дней месяца
"""

import os
import json
from pathlib import Path
import pendulum
from typing import Dict, Any, List, Tuple

# Если захотите использовать GPT для advice/next_event:
from openai import OpenAI

OPENAI_KEY = os.getenv("OPENAI_API_KEY")


def compute_lunar_phase(d: pendulum.Date) -> Tuple[str, int]:
    """
    Эмулируем фазу Луны и процент освещённости.
    Возвращает (название, percent).
    """
    SYNODIC = 29.530588853
    # Опорное новолуние
    ref = pendulum.date(2025, 5, 11)
    age = (d - ref).days % SYNODIC
    pct = int(round(abs((1 - abs((age / SYNODIC)*2-1))) * 100))
    if age < 1:
        name = "Новолуние"
    elif age < SYNODIC * 0.25:
        name = "Растущая Луна"
    elif age < SYNODIC * 0.5:
        name = "Первая четверть"
    elif age < SYNODIC * 0.75:
        name = "Полнолуние"
    elif age < SYNODIC * 0.875:
        name = "Убывающая Луна"
    else:
        name = "Последняя четверть"
    # Знак по дате (примерная широта)
    sign_idx = (d.day + d.month) % 12
    SIGNS = ["Овне","Тельце","Близнецах","Раке","Льве","Деве",
             "Весах","Скорпионе","Стрельце","Козероге","Водолее","Рыбах"]
    sign = SIGNS[sign_idx]
    return f"{name} в {sign} ({pct}% освещ.)", pct


def compute_next_event(d: pendulum.Date) -> str:
    """
    Здесь можно обратиться к GPT или вычислить реальное событие.
    Сейчас — заглушка: через 3 дня Полнолуние → совет.
    """
    # Пример GPT‐подхода:
    if OPENAI_KEY:
        client = OpenAI(api_key=OPENAI_KEY)
        prompt = (
            f"Для даты {d.to_date_string()}: какой ближайший заметный лунный переход "
            "и дай короткий совет (≤12 слов)? Только одна фраза."
        )
        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            temperature=0.6,
            messages=[{"role":"user","content":prompt}],
        )
        text = resp.choices[0].message.content.strip()
        return f"→ {text}"
    # fallback
    return "→ Через 3 дня Полнолуние в Рыбах — время для творчества 🎨"


def generate_calendar(year: int, month: int) -> Dict[str, Dict[str, Any]]:
    start = pendulum.date(year, month, 1)
    end = start.end_of('month')
    cal: Dict[str, Dict[str, Any]] = {}
    d = start
    # Заглушки благоприятных/неблагоприятных дней
    # (можно заменить на реальную логику или GPT-запрос)
    favorable = list(range(1, 6))
    unfavorable = list(range(20, 26))

    while d <= end:
        phase_str, pct = compute_lunar_phase(d)
        advice = (
            f"Начните утро с дыхательной практики 🧘 "
            f"— {phase_str.split()[0].lower()} Луны."
        )
        next_ev = compute_next_event(d)

        cal[d.to_date_string()] = {
            "phase":         phase_str,
            "advice":        advice,
            "next_event":    next_ev,
            "favorable_days": favorable,
            "unfavorable_days": unfavorable,
        }
        d = d.add(days=1)

    return cal


def main():
    today = pendulum.today()
    data = generate_calendar(today.year, today.month)
    out = Path(__file__).parent / "lunar_calendar.json"
    out.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"✅ Файл {out.name} сгенерирован для {today.format('MMMM YYYY')}")


if __name__ == "__main__":
    main()
