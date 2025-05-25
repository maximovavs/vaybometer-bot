#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
gen_lunar_calendar.py
~~~~~~~~~~~~~~~~~~~~~
Генерирует файл lunar_calendar.json для текущего месяца с точными астрономическими расчётами:
  - phase          "Полнолуние в Овне (100% освещ.)"
  - percent        100
  - sign           "Овен"
  - aspects        ["☌Saturn (+0.4°)", "☍Mars (-0.2°)", ...]
  - void_of_course {"start": ISO, "end": ISO}  # заглушка, можно заполнить позже
  - next_event     "→ Через 3 дн. Новолуние в Близнецах"
  - advice         ["Работа/финансы: …", "Что отложить: …", "Ритуал дня: …"]
  - favorable_days {"general":[…], "haircut":[…], ...}
  - unfavorable_days {"general":[…], ...}
"""

import os
import json
import math
import random
from pathlib import Path
from typing import Dict, Any, List, Tuple, Optional

import pendulum
import swisseph as swe

# ── Опциональный GPT-клиент ─────────────────────────────
try:
    from openai import OpenAI
    OPENAI_KEY = os.getenv("OPENAI_API_KEY")
    gpt = OpenAI(api_key=OPENAI_KEY) if OPENAI_KEY else None
except ImportError:
    gpt = None

# ── Категории дней ───────────────────────────────────────
CATEGORIES = {
    "general":   {"favorable":[1,2,3,4,7,28,29],     "unfavorable":[13,20,23,24,27]},
    "haircut":   {"favorable":[1,2,4,7,9,10,18,19,24,25,31], "unfavorable":[]},
    "travel":    {"favorable":[5,7,14,15],            "unfavorable":[]},
    "shopping":  {"favorable":[3,6,9,12,14,17,20,25], "unfavorable":[13,20,23,24,27]},
    "health":    {"favorable":[1,2,3,4,7,28,29],      "unfavorable":[]},
}

# ── Фолбэк-списки для советов по фазам ───────────────────
FALLBACK_ADVICE: Dict[str, List[str]] = {
    "Новолуние": [
        "Работа/финансы: Запланируй и зафиксируй цели месяца 📝",
        "Что отложить: Откажись от импульсивных покупок 💸",
        "Ритуал дня: Мини-медитация на очистку ума 🧘"
    ],
    "Растущая Луна": [
        "Работа/финансы: Начни новый проект 🚀",
        "Что отложить: Не ввязывайся в споры ⚔️",
        "Ритуал дня: Утренняя зарядка на свежем воздухе 🏃‍♀️"
    ],
    "Первая четверть": [
        "Работа/финансы: Сосредоточься на самых важных задачах 🎯",
        "Что отложить: Не начинай крупных покупок 🛑",
        "Ритуал дня: Креативная сессия (рисуй/пиши) 🎨"
    ],
    "Полнолуние": [
        "Работа/финансы: Проведи ревизию бюджета 💰",
        "Что отложить: Избегай важных переговоров 🗣️",
        "Ритуал дня: Лунная ванна или вечер под звёздами 🌕"
    ],
    "Последняя четверть": [
        "Работа/финансы: Подведи итоги и закрой дела ✔️",
        "Что отложить: Отложи крупные решения до завтра ⏳",
        "Ритуал дня: Ритуал прощения/отпускания 🔄"
    ],
}

# ── Аспектные углы и орбисы ──────────────────────────────
ASPECTS = {0:"☌",60:"⚹",90:"□",120:"△",180:"☍"}
ORBIS   = {0:5.0, 60:4.0, 90:3.0, 120:4.0, 180:5.0}

PLANETS = {
    "Sun":   swe.SUN,
    "Mercury": swe.MERCURY,
    "Venus": swe.VENUS,
    "Mars":  swe.MARS,
    "Jupiter": swe.JUPITER,
    "Saturn":  swe.SATURN,
    "Uranus":  swe.URANUS,
    "Neptune": swe.NEPTUNE,
    "Pluto":   swe.PLUTO,
}

def compute_phase_and_sign(jd_ut: float) -> Tuple[str,int,str]:
    """Возвращает phase_str, illum, sign."""
    sun_lon  = swe.calc_ut(jd_ut, swe.SUN)[0][0]
    moon_lon = swe.calc_ut(jd_ut, swe.MOON)[0][0]
    angle    = (moon_lon - sun_lon) % 360.0
    illum    = int(round((1 - math.cos(math.radians(angle))) / 2 * 100))
    # название фазы
    if illum < 5:
        name = "Новолуние"
    elif illum == 50:
        name = "Первая четверть"
    elif illum < 50:
        name = "Растущая Луна"
    elif illum < 95:
        name = "Полнолуние"
    else:
        name = "Последняя четверть"
    # знак
    idx   = int(moon_lon // 30) % 12
    signs = ["Овен","Телец","Близнецы","Рак","Лев","Дева",
             "Весы","Скорпион","Стрелец","Козерог","Водолей","Рыбы"]
    sign  = signs[idx]
    phase_str = f"{name} в {sign} ({illum}% освещ.)"
    return phase_str, illum, sign

def compute_aspects(jd_ut: float) -> List[str]:
    moon_lon = swe.calc_ut(jd_ut, swe.MOON)[0][0]
    out = []
    for name, pid in PLANETS.items():
        pl_lon = swe.calc_ut(jd_ut, pid)[0][0]
        diff   = abs((moon_lon - pl_lon + 180) % 360 - 180)
        for ang,sym in ASPECTS.items():
            orb = ORBIS.get(ang, 3.0)
            if abs(diff - ang) <= orb:
                out.append(f"{sym}{name} ({diff-ang:+.1f}°)")
    return out

def find_next_events(dates: List[pendulum.Date], phases: Dict[str,str]) -> Dict[str,str]:
    """Для каждой даты ищем следующее новолуние/полнолуние."""
    result = {}
    for d in dates:
        future = [x for x in dates if x > d]
        nxt = next((x for x in future
                    if "Новолуние" in phases[x.to_date_string()] 
                    or "Полнолуние" in phases[x.to_date_string()]), None)
        if nxt:
            delta = (nxt - d).days
            result[d.to_date_string()] = f"→ Через {delta} дн. {phases[nxt.to_date_string()]}"
        else:
            result[d.to_date_string()] = "→ Следующее событие скоро…"
    return result

def compute_advice(d: pendulum.Date, phase_str: str) -> List[str]:
    """Обращение к GPT или выбор из фолбэка — 3 совета."""
    phase_name = phase_str.split(" в ")[0]
    if gpt:
        prompt = (
            f"Дата: {d.to_date_string()}, фаза: {phase_str}. "
            "Дай 3 коротких практических совета, разделённых категориями:\n"
            "• работа/финансы\n• что отложить\n• ритуал дня."
        )
        resp = gpt.chat.completions.create(
            model="gpt-4o-mini", temperature=0.7,
            messages=[{"role":"user","content":prompt}]
        )
        # ожидаем ответ со строками, разбитыми по переносу
        lines = [l.strip() for l in resp.choices[0].message.content.split("\n") if l.strip()]
        return lines[:3]
    else:
        pool = FALLBACK_ADVICE.get(phase_name, ["Насладись лунным светом 🌙"])
        return random.sample(pool, k=min(3,len(pool)))

def generate_calendar(year: int, month: int) -> Dict[str,Any]:
    start = pendulum.date(year,month,1)
    end   = start.end_of('month')
    cal: Dict[str,Any] = {}
    dates, phases = [], {}
    # 1) Собираем базу
    d = start
    while d <= end:
        jd_ut = swe.julday(d.year,d.month,d.day,0.0)
        phase_str, illum, sign = compute_phase_and_sign(jd_ut)
        dates.append(d)
        phases[d.to_date_string()] = phase_str
        cal[d.to_date_string()] = {
            "phase":    phase_str,
            "percent":  illum,
            "sign":     sign,
            "aspects":  compute_aspects(jd_ut),
            "void_of_course": {"start": None, "end": None},
            "advice":   [],
            "next_event": "",
            "favorable_days":   {cat:CATEGORIES[cat]["favorable"] for cat in CATEGORIES},
            "unfavorable_days": {cat:CATEGORIES[cat]["unfavorable"] for cat in CATEGORIES},
        }
        d = d.add(days=1)
    # 2) next_event
    nxt = find_next_events(dates, phases)
    for k in cal: cal[k]["next_event"] = nxt[k]
    # 3) advice
    for d in dates:
        key = d.to_date_string()
        cal[key]["advice"] = compute_advice(d, cal[key]["phase"])
    return cal

def main():
    today = pendulum.today()
    data  = generate_calendar(today.year, today.month)
    out   = Path(__file__).parent / "lunar_calendar.json"
    out.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"✅ lunar_calendar.json сгенерирован для {today.format('MMMM YYYY')}")

if __name__ == "__main__":
    main()
