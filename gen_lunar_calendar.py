#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
gen_lunar_calendar.py
~~~~~~~~~~~~~~~~~~~~~
Генерирует lunar_calendar.json c точными фазами Луны,
моментами фаз, Void-of-Course, аспектами и советами.
"""

import os, json, math, random, asyncio
from pathlib import Path
from typing import Dict, List, Any, Tuple

import pendulum                       # удобные даты/время
import swisseph as swe                # Swiss-Ephemeris

# ──────────────────────────── настройки ────────────────────────────
TZ = pendulum.timezone("UTC")         # всё храним в UTC

# fallback-советы (порезаны до 3-х на фазу — для примера)
FALLBACK: Dict[str, List[str]] = {
    "Новолуние": [
        "Работа/финансы: Ставь цели на цикл 📝",
        "Что отложить: крупные траты 💸🛑",
        "Ритуал дня: медитация на намерения 🧘",
    ],
    "Растущий серп": [
        "Работа/финансы: Делегируй и расширяй 🤝",
        "Что отложить: споры ⚔️",
        "Ритуал дня: дыхание и утренняя йога 🌬️",
    ],
    # … остальные фазы по аналогии …
}

# категории благоприятных дней (пример)
CATEGORIES = {
    "general":   {"favorable": [2,3,9,27],    "unfavorable":[13,14,24]},
    "haircut":   {"favorable": [2,3,9],       "unfavorable":[]},
    "travel":    {"favorable": [4,5],         "unfavorable":[]},
    "shopping":  {"favorable": [1,2,7],       "unfavorable":[]},
    "health":    {"favorable": [20,21,27],    "unfavorable":[]},
}

# аспекты и орбисы
ASPECT_ANG = {0:"☌",60:"⚹",90:"□",120:"△",180:"☍"}
ORBS       = {0:6, 60:4, 90:3, 120:4, 180:6}

PLANETS = {
    "Sun": swe.SUN,"Mercury": swe.MERCURY,"Venus": swe.VENUS,"Mars": swe.MARS,
    "Jupiter": swe.JUPITER,"Saturn": swe.SATURN,"Uranus": swe.URANUS,
    "Neptune": swe.NEPTUNE,"Pluto": swe.PLUTO,
}

# optional GPT
try:
    from  openai import OpenAI
    _client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
except Exception:
    _client = None

# ──────────────────────── утилитарные функции ─────────────────────
def jd(dt:pendulum.DateTime)->float:           # pendulum → JD UT
    return swe.julday(dt.year, dt.month, dt.day,
                      dt.hour + dt.minute/60 + dt.second/3600)

def dt_from_jd(j:float)->pendulum.DateTime:    # JD UT → pendulum
    ts = (j - 2440587.5)*86400
    return pendulum.from_timestamp(ts, tz="UTC")

def moon_lon(jd_ut:float)->float:
    return swe.calc_ut(jd_ut, swe.MOON)[0][0] % 360

def sun_lon(jd_ut:float)->float:
    return swe.calc_ut(jd_ut, swe.SUN )[0][0] % 360

def illum_pct(angle:float)->int:
    return int(round((1-math.cos(math.radians(angle)))/2*100))

def phase_name(angle:float)->str:
    if   angle < 22.5:   return "Новолуние"
    elif angle < 67.5:   return "Растущий серп"
    elif angle <112.5:   return "Первая четверть"
    elif angle <157.5:   return "Растущая Луна"
    elif angle <202.5:   return "Полнолуние"
    elif angle <247.5:   return "Убывающая Луна"
    elif angle <292.5:   return "Последняя четверть"
    else:                return "Убывающий серп"

SIGNS = ["Овен","Телец","Близнецы","Рак","Лев","Дева",
         "Весы","Скорпион","Стрелец","Козерог","Водолей","Рыбы"]

def sign_name(lon:float)->str:
    return SIGNS[int(lon//30)%12]

# ───────────── поиск точного JD момента фазы (0,90,180,270) ─────────
def find_phase_time(month_start:pendulum.DateTime)->Dict[str,float]:
    """Возвращает {JD: phase_name} для всех фаз внутри месяца."""
    jd_start  = jd(month_start.start_of('month').subtract(days=2))
    jd_end    = jd(month_start.end_of('month').add(days=2))
    phases={}
    step=0.25      # дней
    j  = jd_start
    prev = (moon_lon(j)-sun_lon(j))%360
    while j < jd_end:
        j_next = j+step
        cur = (moon_lon(j_next)-sun_lon(j_next))%360
        # проверяем пересечения 0/90/180/270
        for target in (0,90,180,270):
            if (prev-target)*(cur-target) < 0:
                # бинарный поиск по интервалу [j, j_next]
                a,b=j,j_next
                for _ in range(20):            # ~ ±1 мин
                    m=(a+b)/2
                    ang=(moon_lon(m)-sun_lon(m))%360
                    if (prev-target)*(ang-target)<0: b=m
                    else: a=m
                phases[a]=phase_name(target)
        j_next,prev=j_next,cur
        j=j_next
    return dict(sorted(phases.items()))

# ─────────────────────── вычисление аспектов ───────────────────────
def aspects(j:float)->List[str]:
    mlon=moon_lon(j)
    res=[]
    for name,pid in PLANETS.items():
        plon=swe.calc_ut(j,pid)[0][0]%360
        diff=abs((mlon-plon+180)%360-180)
        for ang,sym in ASPECT_ANG.items():
            if abs(diff-ang) <= ORBS[ang]:
                res.append(f"{sym}{name} ({diff-ang:+.1f}°)")
    return res

# ───────────────────────── советы GPT / fallback ───────────────────
def advice(date:pendulum.Date, phase:str)->List[str]:
    p_name=phase.split(" в ")[0]
    if not _client:
        return FALLBACK.get(p_name,["…","…","…"])
    prompt=(f"Ты — опытный астролог. Ты лучше всех знаешь как влияют звёзды и луна на нашу жизнь, любишь помогать людям, но ты очень краток. Дата {date}, фаза: {phase}. "
            "Каждое слово будто золото, ты перехишь сразу к делу и не используешь слова такие как конечно, вот мои совету и подбираешь эмодзи в тему, а сейчас Дай три коротких совета с emoji в категориях:\n"
            "• работа/финансы\n• что отложить\n• ритуал дня")
    try:
        rsp=_client.chat.completions.create(
            model="gpt-4o-mini",temperature=0.7,
            messages=[{"role":"user","content":prompt}]
        )
        out=[l.strip("• ").strip()
             for l in rsp.choices[0].message.content.splitlines() if l.strip()]
        return out[:3] if len(out)>=3 else out+["…"]*(3-len(out))
    except Exception:
        return FALLBACK.get(p_name,["…","…","…"])

# ───────────────────────  Void-of-Course  (approx) ──────────────────
def void_of_course(j:float)->Tuple[str,str]:
    """
    Swiss-Ephemeris не имеет прямой функции V/C, поэтому:
    V/C начинается после последнего мажор-аспекта (0/60/90/120/180)
    и заканчивается при входе Луны в следующий знак. Для ежедневного
    дайджеста хватит грубой оценки: с 26° знака до 0° нового знака.
    """
    mlon=moon_lon(j)
    start=None; end=None
    # если Луна > 26° текущего знака → начинается V/C
    if mlon%30 > 26:
        # JD до входа в след. знак
        sign_end = j + (30 - mlon%30)/13.2/24    # 13.2°/день ≈
        start=dt_from_jd(j).format("DD.MM HH:mm")
        end  =dt_from_jd(sign_end).format("DD.MM HH:mm")
    return start,end

# ─────────────────────────── генератор дня ─────────────────────────
def day_record(d:pendulum.Date, jd_ut:float,
               phase_time_map:Dict[str,float])->Dict[str,Any]:
    ang  =(moon_lon(jd_ut)-sun_lon(jd_ut))%360
    name = phase_name(ang)
    pct  = illum_pct(ang)
    phase_str=f"{name} в {sign_name(moon_lon(jd_ut))} ({pct}% освещ.)"
    # ближайшее точное JD этой фазы
    jd_phase = next((pjd for pjd,pname in phase_time_map.items()
                     if pname==name and abs(pjd-jd_ut)<2), jd_ut)
    vc_start,vc_end = void_of_course(jd_ut)
    rec={
        "phase":       phase_str,
        "percent":     pct,
        "sign":        sign_name(moon_lon(jd_ut)),
        "phase_time":  dt_from_jd(jd_phase).to_iso8601_string(),
        "aspects":     aspects(jd_ut),
        "void_of_course":{"start":vc_start,"end":vc_end},
        "advice":      advice(d, phase_str),
        "favorable_days":   {k:v["favorable"]   for k,v in CATEGORIES.items()},
        "unfavorable_days": {k:v["unfavorable"] for k,v in CATEGORIES.items()},
    }
    return rec

# ───────────────────────── генерация месяца ────────────────────────
def generate_calendar(year:int, month:int)->Dict[str,Any]:
    swe.set_ephe_path(".")
    first_day=pendulum.datetime(year,month,1,tz=TZ)
    phases=find_phase_time(first_day)
    cal={}
    d=first_day
    while d.month==month:
        cal[d.to_date_string()]=day_record(d,jd(d), phases)
        d=d.add(days=1)
    return cal

# ────────────────────────────── main  ──────────────────────────────
async def main():
    today=pendulum.today()
    data = generate_calendar(today.year,today.month)
    Path("lunar_calendar.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print("✅ lunar_calendar.json обновлён",
          f"({today.format('MMMM YYYY')})")

if __name__=="__main__":
    asyncio.run(main())
