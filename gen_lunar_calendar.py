#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
gen_lunar_calendar.py
~~~~~~~~~~~~~~~~~~~~~
Генерирует lunar_calendar.json для текущего месяца с точными расчётами:
  - phase         : "Полнолуние в Овне (100% освещ.)"
  - phase_time    : ISO-время UT момента фазы
  - percent       : 100
  - sign          : "Овен"
  - aspects       : ["☌Saturn (+0.4°)", …]
  - void_of_course: {"start":"…Z","end":"…Z"}
  - next_event    : "→ Через 2 дн. Новолуние в Близнецах"
  - advice        : ["…","…","…"]
  - favorable_days / unfavorable_days
"""

import os, json, math, random
from pathlib import Path
from typing import Dict, Any, List, Optional

import pendulum
import swisseph as swe

# GPT-клиент
try:
    from openai import OpenAI
    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
except ImportError:
    client = None

TZ = pendulum.timezone("UTC")

# Категории...
CATEGORIES = {
    "general":  {"favorable":[1,2,3,4,7,28,29], "unfavorable":[13,20,23,24,27]},
    "haircut":  {"favorable":[1,2,4,7,9,10,18,19,24,25,31],"unfavorable":[]},
    "travel":   {"favorable":[5,7,14,15],           "unfavorable":[]},
    "shopping": {"favorable":[3,6,9,12,14,17,20,25],"unfavorable":[13,20,23,24,27]},
    "health":   {"favorable":[1,2,3,4,7,28,29],      "unfavorable":[]},
}

ASPECTS = {0:"☌",60:"⚹",90:"□",120:"△",180:"☍"}
ORBIS   = {0:5.0,60:4.0,90:3.0,120:4.0,180:5.0}

PLANETS = {
    "Sun": swe.SUN, "Mercury": swe.MERCURY, "Venus": swe.VENUS,
    "Mars": swe.MARS, "Jupiter": swe.JUPITER, "Saturn": swe.SATURN,
    "Uranus": swe.URANUS, "Neptune": swe.NEPTUNE, "Pluto": swe.PLUTO,
}

FALLBACK = {
    "Новолуние": [
        "Работа/финансы: Запланируй цели месяца 📝☀️",
        "Здоровье: Пей воду с лимоном 💧🍋",
        "Ритуал: Медитация у моря 🧘🌊",
    ],
    # остальные фазы...
}

def jd_to_dt(jd: float) -> pendulum.DateTime:
    """JD → pendulum UTC"""
    return pendulum.from_timestamp((jd-2440587.5)*86400, tz=TZ)

def compute_phase_and_sign(jd: float):
    slon = swe.calc_ut(jd, swe.SUN)[0][0]
    mlon = swe.calc_ut(jd, swe.MOON)[0][0]
    angle = (mlon - slon) % 360
    pct = int(round((1 - math.cos(math.radians(angle)))/2*100))
    if angle < 22.5 or angle>=337.5:     name="Новолуние"
    elif angle<67.5:                     name="Растущий серп"
    elif angle<112.5:                    name="Первая четверть"
    elif angle<157.5:                    name="Растущая Луна"
    elif angle<202.5:                    name="Полнолуние"
    elif angle<247.5:                    name="Убывающая Луна"
    elif angle<292.5:                    name="Последняя четверть"
    else:                                name="Убывающий серп"
    sign = ["Овен","Телец","Близнецы","Рак","Лев","Дева",
            "Весы","Скорпион","Стрелец","Козерог","Водолей","Рыбы"][int(mlon//30)]
    return name, pct, sign

def next_phase_jd(jd: float, phase: str) -> float:
    """JD следующей указанной фазы."""
    if phase=="Новолуние":          return swe.next_new_moon(jd)
    if phase=="Полнолуние":         return swe.next_full_moon(jd)
    if phase=="Первая четверть":    return swe.next_first_quarter(jd)
    if phase=="Последняя четверть": return swe.next_last_quarter(jd)
    # для серпов можно брать ближайшее новолуние/полнолуние
    return swe.next_new_moon(jd)

def compute_next_event(jd: float) -> str:
    now = jd_to_dt(jd).date()
    # найдем ближайшее из новол. и полнол.
    jn = swe.next_new_moon(jd);   dn = jd_to_dt(jn).date()
    jf = swe.next_full_moon(jd);  df = jd_to_dt(jf).date()
    if (dn-now) <= (df-now):
        d,j = dn,jn
    else:
        d,j = df,jf
    days = (d-now).days
    name,_pct,sign = compute_phase_and_sign(j)
    return f"→ Через {days} дн. {name} в {sign}"

def compute_aspects(jd: float) -> List[str]:
    mlon = swe.calc_ut(jd, swe.MOON)[0][0]
    out=[]
    for n,p in PLANETS.items():
        pl = swe.calc_ut(jd,p)[0][0]
        diff=abs((mlon-pl+180)%360-180)
        for ang,s in ASPECTS.items():
            if abs(diff-ang)<=ORBIS[ang]:
                out.append(f"{s}{n} ({diff-ang:+.1f}°)")
    return out

def compute_void_of_course(jd: float) -> Dict[str,Optional[str]]:
    # ваш алгоритм без изменений
    ...

def compute_advice(date: pendulum.Date, phase: str) -> List[str]:
    cat,_,_ = phase.partition(" в ")
    if client:
        prompt = (
            f"Действуй как астролог, дата {date}, фаза {phase}. "
            "Дай 3 совета (работа/финансы; что отложить; ритуал дня)."
        )
        resp = client.chat.completions.create(
            model="gpt-4o-mini", temperature=0.7,
            messages=[{"role":"user","content":prompt}]
        )
        lines = [l.strip() for l in resp.choices[0].message.content.splitlines() if l.strip()]
        return lines[:3]
    return random.sample(FALLBACK.get(cat, []), 3)

def generate_calendar(year:int, month:int) -> Dict[str,Any]:
    swe.set_ephe_path('.')  # если нужно
    start=pendulum.date(year,month,1)
    end  =start.end_of('month')
    cal={}
    d=start
    while d<=end:
        jd=swe.julday(d.year,d.month,d.day,0.0)
        name,pct,sign = compute_phase_and_sign(jd)
        # точный момент этой фазы
        jt = next_phase_jd(jd,name)
        cal[d.to_date_string()]={
            "phase":         f"{name} в {sign} ({pct}% освещ.)",
            "phase_time":    jd_to_dt(jt).to_iso8601_string(),
            "percent":       pct,
            "sign":          sign,
            "aspects":       compute_aspects(jd),
            "void_of_course": compute_void_of_course(jd),
            "next_event":    compute_next_event(jd),
            "advice":        compute_advice(d, name),
            "favorable_days":   {k:v["favorable"]   for k,v in CATEGORIES.items()},
            "unfavorable_days": {k:v["unfavorable"] for k,v in CATEGORIES.items()},
        }
        d=d.add(days=1)
    return cal

def main():
    today=pendulum.today()
    data=generate_calendar(today.year,today.month)
    p=Path(__file__).parent/"lunar_calendar.json"
    p.write_text(json.dumps(data,ensure_ascii=False,indent=2), encoding="utf-8")
    print(f"✅ lunar_calendar.json сгенерирован для {today.format('MMMM YYYY')}")

if __name__=="__main__":
    main()
