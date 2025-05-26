#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
gen_lunar_calendar.py  • строит lunar_calendar.json
–– добавлены:
   • phase_time  – ISO-время точного достижения фазы
   • реальные V/C (ещё черновик: swe.next_void не во всех сборках,
     но заглушку оставили ↓)
   • собственная find_next_phase вместо отсутствующих
     swe.next_new_moon / swe.next_full_moon / …
"""

import os, json, math, random
from pathlib import Path
from typing import Dict, Any, List, Tuple

import pendulum
import swisseph as swe

TZ = pendulum.timezone("Asia/Nicosia")

# ──────────────────────────────────────────────────────────
# ► UTILS
# ----------------------------------------------------------
def jd_to_dt(jd: float) -> pendulum.DateTime:
    """Юлианская дата ➜ pendulum UTC"""
    return pendulum.from_timestamp((jd - 2440587.5) * 86400, tz="UTC")

def lunar_angle(jd: float) -> float:
    """Разность долгот Луны и Солнца (0…360) на jd (UT)"""
    lon_moon = swe.calc_ut(jd, swe.MOON)[0][0]
    lon_sun  = swe.calc_ut(jd, swe.SUN )[0][0]
    return (lon_moon - lon_sun) % 360.0

PHASE_TARGET = {
    "Новолуние":          0,
    "Растущий серп":     45,
    "Первая четверть":   90,
    "Растущая Луна":    135,
    "Полнолуние":       180,
    "Убывающая Луна":   225,
    "Последняя четверть":270,
    "Убывающий серп":   315,
}

def find_next_phase(jd: float, target_deg: float) -> float:
    """
    Находит ближайший вперёд момент, когда угол «Луна – Солнце»
    ≈ target_deg ±0.1°. Сначала идём шагом 0.5 дня, затем
    бинарно уточняем до ≈1 мин.
    """
    step = 0.5        # суток
    jd1  = jd + step
    while step > 1/1440:          # пока грубее 1 минуты
        while (lunar_angle(jd1) - target_deg + 540) % 360 - 180 > 0:
            jd1 += step
        # «перепрыгнули» через цель – откатываемся назад и уменьшаем шаг
        jd1 -= step
        step /= 2
        jd1 += step
    return jd1

# ──────────────────────────────────────────────────────────
# ► MAIN CALCULATIONS  (сокращено до ключевых мест)
# ----------------------------------------------------------
SIGNS = ["Овен","Телец","Близнецы","Рак","Лев","Дева",
         "Весы","Скорпион","Стрелец","Козерог","Водолей","Рыбы"]

def phase_name(angle: float) -> str:
    if   angle < 22.5:   return "Новолуние"
    elif angle < 67.5:   return "Растущий серп"
    elif angle < 112.5:  return "Первая четверть"
    elif angle < 157.5:  return "Растущая Луна"
    elif angle < 202.5:  return "Полнолуние"
    elif angle < 247.5:  return "Убывающая Луна"
    elif angle < 292.5:  return "Последняя четверть"
    else:                return "Убывающий серп"

def compute_day_block(jd_midnight: float) -> Tuple[Dict[str,Any], str]:
    """
    Возвращает словарь с данными на день + ключ (YYYY-MM-DD)
    """
    angle = lunar_angle(jd_midnight)
    name  = phase_name(angle)
    illum = int(round((1 - math.cos(math.radians(angle))) / 2 * 100))

    # длинная история со знаком
    moon_lon = swe.calc_ut(jd_midnight, swe.MOON)[0][0]
    sign = SIGNS[int(moon_lon // 30) % 12]

    # точный момент данной фазы вперёд
    target = PHASE_TARGET[name]
    jd_phase = find_next_phase(jd_midnight - 1.0, target)  # ищем от вчера

    rec = {
        "phase":       f"{name} в {sign} ({illum}% освещ.)",
        "percent":     illum,
        "sign":        sign,
        "phase_time":  jd_to_dt(jd_phase).in_tz(TZ).to_iso8601_string(),
        # ----------- далее оставляем прежнюю логику -----------
        "aspects":     [],        # здесь ваши compute_aspects(...)
        "void_of_course": {"start": None, "end": None},  # TODO real VC
        "next_event":  "",        # заполним позже
        "advice":      ["…", "…", "…"],   # ваш GPT/fallback
        "favorable_days":   {},   # как было
        "unfavorable_days": {},
    }
    key = jd_to_dt(jd_midnight).format("YYYY-MM-DD")
    return rec, key

def generate_calendar(year:int, month:int) -> Dict[str,Any]:
    start = pendulum.date(year,month,1)
    end   = start.end_of('month')
    jd0   = swe.julday(start.year, start.month, start.day, 0.0)

    cal: Dict[str,Any] = {}
    cur = start
    while cur <= end:
        jd_mid = swe.julday(cur.year, cur.month, cur.day, 0.0)
        rec, key = compute_day_block(jd_mid)
        cal[key] = rec
        cur = cur.add(days=1)

    # — next_event (как было) —
    keys = sorted(cal.keys())
    for i,k in enumerate(keys):
        for f in keys[i+1:]:
            if "Новолуние" in cal[f]["phase"] or "Полнолуние" in cal[f]["phase"]:
                delta = (pendulum.parse(f) - pendulum.parse(k)).days
                cal[k]["next_event"] = f"→ Через {delta} дн. {cal[f]['phase']}"
                break
    return cal

def main():
    today = pendulum.today()
    data  = generate_calendar(today.year,today.month)
    Path("lunar_calendar.json").write_text(
        json.dumps(data,ensure_ascii=False,indent=2), encoding="utf-8"
    )
    print("✅ lunar_calendar.json updated")

if __name__ == "__main__":
    main()
