#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Генератор lunar_calendar.json • расширенные поля + long_desc для месячного поста
"""

import os, json, math, random, asyncio, html
from pathlib import Path
from typing import Dict, Any, List
import pendulum, swisseph as swe

TZ = pendulum.timezone("Asia/Nicosia")

# ── GPT (опционально) ───────────────────────────────
try:
    from openai import OpenAI
    GPT = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
except Exception:
    GPT = None
# ────────────────────────────────────────────────────

EMO = { "Новолуние":"🌑","Растущий серп":"🌒","Первая четверть":"🌓","Растущая Луна":"🌔",
        "Полнолуние":"🌕","Убывающая Луна":"🌖","Последняя четверть":"🌗","Убывающий серп":"🌘"}

FALLBACK_LONG = {
    "Новолуние"        :"Нулевая точка цикла: закладывайте намерения и мечты.",
    "Растущий серп"    :"Энергия прибавляется — хорошо стартовать новые задачи.",
    "Первая четверть"  :"Видны первые трудности; корректируйте курс и действуйте.",
    "Растущая Луна"    :"Ускорение: расширяйте проекты, укрепляйте связи.",
    "Полнолуние"       :"Кульминация месяца: максимум эмоций и результатов.",
    "Убывающая Луна"   :"Отпускаем лишнее, завершаем дела, наводим порядок.",
    "Последняя четверть":"Время аналитики и пересмотра стратегий.",
    "Убывающий серп"   :"Отдых, ретриты, подготовка к новому циклу."
}

CATS = {
    "general":{"favorable":[2,3,9,27],"unfavorable":[13,14,24]},
    "haircut":{"favorable":[2,3,9],"unfavorable":[]},
    "travel" :{"favorable":[4,5],"unfavorable":[]},
    "shopping":{"favorable":[1,2,7],"unfavorable":[]},
    "health" :{"favorable":[20,21,27],"unfavorable":[]},
}

# ───── helpers ──────────────────────────────────────
def jd2dt(jd: float)->pendulum.DateTime:
    return pendulum.from_timestamp((jd-2440587.5)*86400, tz="UTC")

def phase_name(angle: float)->str:
    idx=int(((angle+22.5)%360)//45)
    return ["Новолуние","Растущий серп","Первая четверть","Растущая Луна",
            "Полнолуние","Убывающая Луна","Последняя четверть","Убывающий серп"][idx]

def compute_phase(jd: float):
    lon_s = swe.calc_ut(jd, swe.SUN)[0][0]
    lon_m = swe.calc_ut(jd, swe.MOON)[0][0]
    ang   = (lon_m-lon_s)%360
    illum = int(round((1-math.cos(math.radians(ang)))/2*100))
    name  = phase_name(ang)
    sign  = ["Овен","Телец","Близнецы","Рак","Лев","Дева",
             "Весы","Скорпион","Стрелец","Козерог","Водолей","Рыбы"][int(lon_m//30)%12]
    return name, illum, sign

async def gpt_short(date:str, phase:str)->List[str]:
    prompt=(f"Дата {date}, фаза {phase}. Дай 3 лаконичных совета, каждый в одной строке, "
            "с emoji: 💼 (работа), ⛔ (отложить), 🪄 (ритуал).")
    try:
        r=GPT.chat.completions.create(model="gpt-4o-mini",
            messages=[{"role":"user","content":prompt}],temperature=0.65)
        return [l.strip() for l in r.choices[0].message.content.splitlines() if l.strip()][:3]
    except Exception:
        return ["💼 Сфокусируйся на главном.",
                "⛔ Отложи крупные решения.",
                "🪄 5-минутная медитация."]

async def gpt_long(name:str, month:str)->str:
    prompt=(f"Месяц {month}. Фаза {name}. 2 коротких предложения описывают энергетику периода. "
            "Тон экспертный, вдохновляющий.")
    try:
        r=GPT.chat.completions.create(model="gpt-4o-mini",
            messages=[{"role":"user","content":prompt}],temperature=0.7)
        return r.choices[0].message.content.strip()
    except Exception:
        return FALLBACK_LONG[name]

# ───── main generator ───────────────────────────────
async def generate(year:int, month:int)->Dict[str,Any]:
    swe.set_ephe_path(".")
    first = pendulum.date(year,month,1)
    last  = first.end_of('month')
    cal:Dict[str,Any] = {}
    long_tasks, short_tasks = {}, []

    d=first
    while d<=last:
        jd=swe.julday(d.year,d.month,d.day,0)
        name,illum,sign = compute_phase(jd)
        emoji = EMO[name]
        phase_time = jd2dt(jd).in_tz(TZ).to_iso8601_string()

        # GPT задачи
        short_tasks.append(asyncio.create_task(gpt_short(d.to_date_string(),name)))
        if name not in long_tasks:
            long_tasks[name]=asyncio.create_task(gpt_long(name,d.format('MMMM')))

        cal[d.to_date_string()] = {
            "phase_name": name,                # сохраняем чистое имя!
            "phase":      f"{emoji} {name} в {sign} ({illum}% освещ.)",
            "percent":    illum,
            "sign":       sign,
            "phase_time": phase_time,
            "advice":     [],                  # позже
            "long_desc":  "",                  # позже
            "void_of_course":{"start":None,"end":None},
            "favorable_days":CATS,
            "unfavorable_days":CATS,
        }
        d=d.add(days=1)

    # ждём GPT
    advice = await asyncio.gather(*short_tasks)
    for idx, day in enumerate(sorted(cal)):
        cal[day]["advice"]=advice[idx]

    for name, task in long_tasks.items():
        try: cal_long = await task
        except Exception: cal_long = FALLBACK_LONG[name]
        for rec in cal.values():
            if rec["phase_name"]==name:
                rec["long_desc"]=cal_long

    return cal

async def main():
    today=pendulum.today()
    data=await generate(today.year,today.month)
    Path("lunar_calendar.json").write_text(json.dumps(data,ensure_ascii=False,indent=2),'utf-8')
    print("✅ lunar_calendar.json готов")

if __name__=="__main__":
    asyncio.run(main())