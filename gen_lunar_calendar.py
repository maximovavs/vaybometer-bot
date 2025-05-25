#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
gen_lunar_calendar.py
~~~~~~~~~~~~~~~~~~~~~
Генерирует файл lunar_calendar.json для текущего месяца с точными астрономическими расчётами
и профессиональными рекомендациями.

Выдаёт для каждой даты:
  - phase         : "Полнолуние в Овне (100% освещ.)"
  - percent       : 100
  - sign          : "Овен"
  - aspects       : ["☌Saturn (+0.4°)", "☍Mars (−0.2°)", …]
  - void_of_course: {"start":None,"end":None}  # заглушка, можно доработать
  - next_event    : "→ Через 2 дн. Новолуние в Близнецах"
  - advice        : ["…","…","…"]  # три совета GPT или fallback
  - favorable_days: {"general":[…], "haircut":[…], …}
  - unfavorable_days: {"general":[…], …}
"""

import os
import json
import math
import random
from pathlib import Path
from typing import Dict, Any, List

import pendulum
import swisseph as swe

# ── Опциональный GPT-клиент ───────────────────────────────
try:
    from openai import OpenAI
    OPENAI_KEY = os.getenv("OPENAI_API_KEY")
    gpt = OpenAI(api_key=OPENAI_KEY) if OPENAI_KEY else None
except ImportError:
    gpt = None

# ── Категории дней ────────────────────────────────────────
CATEGORIES: Dict[str, Dict[str, List[int]]] = {
    "general":  {"favorable":[1,2,3,4,7,28,29],     "unfavorable":[13,20,23,24,27]},
    "haircut":  {"favorable":[1,2,4,7,9,10,18,19,24,25,31], "unfavorable":[]},
    "travel":   {"favorable":[5,7,14,15],            "unfavorable":[]},
    "shopping": {"favorable":[3,6,9,12,14,17,20,25], "unfavorable":[13,20,23,24,27]},
    "health":   {"favorable":[1,2,3,4,7,28,29],      "unfavorable":[]},
}

# ── Аспекты и орбисы ─────────────────────────────────────
ASPECTS = {0:"☌", 60:"⚹", 90:"□", 120:"△", 180:"☍"}
ORBIS   = {0:5.0, 60:4.0, 90:3.0, 120:4.0, 180:5.0}

PLANETS = {
    "Sun":     swe.SUN,
    "Mercury": swe.MERCURY,
    "Venus":   swe.VENUS,
    "Mars":    swe.MARS,
    "Jupiter": swe.JUPITER,
    "Saturn":  swe.SATURN,
    "Uranus":  swe.URANUS,
    "Neptune": swe.NEPTUNE,
    "Pluto":   swe.PLUTO,
}

# ── Фолбэк-списки советов по фазам ────────────────────────
FALLBACK_ADVICE: Dict[str, List[str]] = {
    "Новолуние": [
        "Работа/финансы: Запланируй цели месяца, вдохновляясь кипрским солнцем 📝☀️",
        "Здоровье: Начни день с воды и лимона из садов Лимассола 💧🍋",
        "Творчество: Создай мудборд мечты, сидя в кафе Пафоса 📌",
    ],
    "Растущий серп": [
        "Работа/финансы: Составь план действий, вдохновляясь энергией Кипра 🚀",
        "Здоровье: Утренняя йога на пляже Ларнаки для заряда 🧘‍♀️",
        "Ритуал: Дыхательная практика под оливами в Омодосе 🌬️🌳",
    ],
    "Первая четверть": [
        "Работа/финансы: Сфокусируйся на ключевых задачах, как кипрский винодел 🍇",
        "Творчество: Проведи креативную сессию в тени Троодоса 🎨",
        "Ритуал: Тайм-блокинг для дел, вдохновленный рынками Никосии ⏳",
    ],
    "Растущая Луна": [
        "Работа/финансы: Запусти проект, как регата в Ларнаке 🚀⛵",
        "Творчество: Рисуй или пиши, вдохновляясь закатами Кипра 🎨🌅",
        "Ритуал: Йога под оливами для синергии с лунной энергией 🧘‍♀️",
    ],
    "Полнолуние": [
        "Работа/финансы: Проверь бюджет, как торговцы Никосии 💰",
        "Ритуал: Медитация на отпускание у моря в Айя-Напе 🌬️",
        "Творчество: Пиши или рисуй под звёздами Пафоса 🌕🎨",
    ],
    "Убывающая Луна": [
        "Работа/финансы: Подведи итоги, как виноделы после урожая 🔄",
        "Ритуал: Детокс-день с овощами из садов Троодоса 🥣",
        "Отложи: Лишние траты – лучше купи оливковое масло 🛑🫒",
    ],
    "Последняя четверть": [
        "Работа/финансы: Завершай дела, как рыбаки сети в Пафосе ✔️",
        "Ритуал: Дыхательная практика под звёздами Троодоса 🌬️🌳",
        "Творчество: Составь манифест благодарности, как в Омодосе 🙌",
    ],
    "Убывающий серп": [
        "Работа/финансы: Сверь планы, как торговцы Лефкары 📋",
        "Отложи: Новые начинания – лучше чай с травами Троодоса 🌿",
        "Ритуал: Медитация на отпускание под звёздами Пафоса 🌌",
    ],
}


def jd_to_datetime(jd: float) -> pendulum.DateTime:
    """Конвертирует юлианское время UT в pendulum DateTime (UTC)."""
    ts = (jd - 2440587.5) * 86400.0
    return pendulum.from_timestamp(ts, tz="UTC")


def compute_phase_and_sign(jd_ut: float):
    """Вычисляет фазу, % освещённости и знак Луны."""
    sun_lon  = swe.calc_ut(jd_ut, swe.SUN)[0][0]
    moon_lon = swe.calc_ut(jd_ut, swe.MOON)[0][0]
    angle    = (moon_lon - sun_lon) % 360.0
    illum    = int(round((1 - math.cos(math.radians(angle))) / 2 * 100))

    # Название фазы по углу
    if   angle < 22.5:     name = "Новолуние"
    elif angle < 67.5:     name = "Растущий серп"
    elif angle < 112.5:    name = "Первая четверть"
    elif angle < 157.5:    name = "Растущая Луна"
    elif angle < 202.5:    name = "Полнолуние"
    elif angle < 247.5:    name = "Убывающая Луна"
    elif angle < 292.5:    name = "Последняя четверть"
    else:                  name = "Убывающий серп"

    # Знак зодиака
    idx   = int(moon_lon // 30) % 12
    signs = ["Овен","Телец","Близнецы","Рак","Лев","Дева",
             "Весы","Скорпион","Стрелец","Козерог","Водолей","Рыбы"]
    sign  = signs[idx]

    phase_str = f"{name} в {sign} ({illum}% освещ.)"
    return phase_str, illum, sign


def compute_aspects(jd_ut: float) -> List[str]:
    """Ищет основные аспекты Луны к планетам."""
    moon_lon = swe.calc_ut(jd_ut, swe.MOON)[0][0]
    out: List[str] = []
    for pname, pid in PLANETS.items():
        pl_lon = swe.calc_ut(jd_ut, pid)[0][0]
        diff   = abs((moon_lon - pl_lon + 180) % 360 - 180)
        for ang, sym in ASPECTS.items():
            orb = ORBIS.get(ang, 3.0)
            if abs(diff - ang) <= orb:
                out.append(f"{sym}{pname} ({diff-ang:+.1f}°)")
    return out


def compute_advice_list(d: pendulum.Date, phase_str: str) -> List[str]:
    """Три совета от GPT или случайный fallback."""
    phase_name = phase_str.split(" в ")[0]
    if gpt:
        prompt = (
            f"Действуй как профессиональный астролог с чувством средиземноморского юмора. Но будь краток, как будто каждое слово стоит дорого."
            f"Дата {d.to_date_string()}, фаза: {phase_str}. "
            "Дай ровно три коротких практических совета с эмодзи в категориях:\n"
            "• работа/финансы\n• что отложить\n• ритуал дня"
        )
        resp = gpt.chat.completions.create(
            model="gpt-4o-mini", temperature=0.7,
            messages=[{"role":"user","content":prompt}]
        )
        lines = [ln.strip() for ln in resp.choices[0].message.content.splitlines() if ln.strip()]
        return lines[:3]
    else:
        pool = FALLBACK_ADVICE.get(phase_name, FALLBACK_ADVICE["Новолуние"])
        return random.sample(pool, k=min(3, len(pool)))


def generate_calendar(year: int, month: int) -> Dict[str, Any]:
    """Генерирует словарь с подробными данными на каждый день месяца."""
    swe.set_ephe_path('.')  # путь к эфемеридам (если нужно)
    start = pendulum.date(year, month, 1)
    end   = start.end_of('month')
    cal: Dict[str, Any] = {}

    # 1) Собираем базовые данные
    d = start
    while d <= end:
        jd_ut = swe.julday(d.year, d.month, d.day, 0.0)
        phase_str, illum, sign = compute_phase_and_sign(jd_ut)
        cal[d.to_date_string()] = {
            "phase":           phase_str,
            "percent":         illum,
            "sign":            sign,
            "aspects":         compute_aspects(jd_ut),
            "void_of_course":  {"start": None, "end": None},  # TODO: настоящий V/C
            "next_event":      "",  # заполним ниже
            "advice":          compute_advice_list(d, phase_str),
            "favorable_days":  {cat: CATEGORIES[cat]["favorable"]   for cat in CATEGORIES},
            "unfavorable_days":{cat: CATEGORIES[cat]["unfavorable"] for cat in CATEGORIES},
        }
        d = d.add(days=1)

    # 2) Пост-обработка next_event
    dates = sorted(cal.keys())
    for i, today_str in enumerate(dates):
        today_dt = pendulum.parse(today_str)
        nxt = None
        for future_str in dates[i+1:]:
            ph = cal[future_str]["phase"]
            if "Новолуние" in ph or "Полнолуние" in ph:
                nxt = future_str
                break
        if nxt:
            delta = (pendulum.parse(nxt) - today_dt).days
            cal[today_str]["next_event"] = f"→ Через {delta} дн. {cal[nxt]['phase']}"
        else:
            cal[today_str]["next_event"] = "→ Следующее событие скоро…"

    return cal


def main():
    today = pendulum.today()
    data  = generate_calendar(today.year, today.month)
    out   = Path(__file__).parent / "lunar_calendar.json"
    out.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"✅ lunar_calendar.json сгенерирован для {today.format('MMMM YYYY')}")


if __name__ == "__main__":
    main()
