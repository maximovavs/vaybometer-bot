#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
gen_lunar_calendar.py
~~~~~~~~~~~~~~~~~~~~~
Генерирует файл lunar_calendar.json для текущего месяца с продвинутыми советами:
  - phase:          строка с фазой Луны + знак + "(XX% освещ.)"
  - advice:         конкретный призыв к действию (GPT или fallback)
  - next_event:     краткая ссылка на ближайшее астрособытие (GPT или stub)
  - favorable_days: список благоприятных дней месяца (примеры)
  - unfavorable_days: список неблагоприятных дней месяца (примеры)
"""

import os
import json
import random
from pathlib import Path
import pendulum
from typing import Dict, Any, Tuple

# Попытаемся использовать GPT для советов и событий, если задан API-ключ
try:
    from openai import OpenAI
    OPENAI_KEY = os.getenv("OPENAI_API_KEY")
    client = OpenAI(api_key=OPENAI_KEY) if OPENAI_KEY else None
except ImportError:
    client = None

def compute_lunar_phase(d: pendulum.Date) -> Tuple[str, int]:
    """Упрощённо вычисляем фазу Луны и процент освещённости."""
    SYNODIC = 29.530588853
    ref = pendulum.date(2025, 5, 11)  # опорная дата Новолуния
    age = (d - ref).days % SYNODIC
    pct = int(round(abs((1 - abs((age / SYNODIC) * 2 - 1))) * 100))
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
    # Привяжем знак к дню для примера
    SIGNS = ["Овне","Тельце","Близнецах","Раке","Льве","Деве",
             "Весах","Скорпионе","Стрельце","Козероге","Водолее","Рыбах"]
    sign = SIGNS[(d.day + d.month) % 12]
    return f"{name} в {sign} ({pct}% освещ.)", pct

def compute_next_event(d: pendulum.Date) -> str:
    """Пытаемся через GPT получить ближайшее серьёзное лунное событие."""
    if client:
        prompt = (
            f"Дата {d.to_date_string()}, фаза Луны: {compute_lunar_phase(d)[0]}. "
            "Кратко (≤12 слов) анонсируй ближайшую заметную смену фазы или затмение и дай совет."
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

# Фолбэк-списки советов по фазам
FALLBACK_ADVICE = {
    "Новолуние": [
        "Начни новый проект с короткой медитации на цели 🧘",
        "Запиши свои намерения на бумаге и держи их на виду 📝"
    ],
    "Растущая Луна": [
        "Планируй и действуй: займись спортом на свежем воздухе 🏃",
        "Начни изучать что-то новое и записывай свой прогресс 📚"
    ],
    "Первая четверть": [
        "Используй импульс: устраивай встречи и обсуждай идеи 💬",
        "Сосредоточься на важных задачах — заверши половину планов ✅"
    ],
    "Полнолуние": [
        "Закрой незавершённые дела и отдохни под лунным светом 🌕",
        "Проведи творческий вечер: рисуй или пиши 🎨"
    ],
    "Убывающая Луна": [
        "Ритуал очищения: избавься от ненужных вещей 🕯️",
        "Подведи итоги и запланируй восстановление энергии 🔄"
    ],
    "Последняя четверть": [
        "Анализируй прошедшую неделю и расслабься 🛁",
        "Займись медитацией или дыхательной практикой 🌬️"
    ],
}

def compute_advice(d: pendulum.Date, phase_str: str) -> str:
    """Генерируем совет либо через GPT, либо из списка фолбэка."""
    phase_name = phase_str.split(" в ")[0]
    if client:
        prompt = (
            f"Дата {d.to_date_string()}, фаза Луны: {phase_str}. "
            "Дай конкретный практический совет к действию на этот день, 1–2 предложения."
        )
        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            temperature=0.7,
            messages=[{"role":"user","content":prompt}],
        )
        return resp.choices[0].message.content.strip()
    return random.choice(FALLBACK_ADVICE.get(phase_name, ["Насладись моментом под лунным светом 🌙"]))

def generate_calendar(year: int, month: int) -> Dict[str, Dict[str, Any]]:
    """Основной генератор календаря на заданный месяц."""
    start = pendulum.date(year, month, 1)
    end = start.end_of('month')
    cal: Dict[str, Dict[str, Any]] = {}
    # Примеры благоприятных/неблагоприятных дней (можно заменить на логику)
    favorable = list(range(1, 6))
    unfavorable = list(range(20, 26))
    d = start
    while d <= end:
        phase_str, _ = compute_lunar_phase(d)
        advice = compute_advice(d, phase_str)
        next_ev = compute_next_event(d)
        cal[d.to_date_string()] = {
            "phase":            phase_str,
            "advice":           advice,
            "next_event":       next_ev,
            "favorable_days":   favorable,
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
