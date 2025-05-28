#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Генерирует lunar_calendar.json на месяц вплоть до расширенных полей:
  • phase, percent, sign, phase_time
  • advice  – 3 коротких совета (💼 ⛔ 🪄)
  • long_desc – 1–2 предложения для «месячного» поста
  • favorable_days / unfavorable_days
"""

import os, json, math, random, asyncio
from pathlib import Path
from typing import Dict, Any, List
import pendulum, swisseph as swe

TZ = pendulum.timezone("Asia/Nicosia")

# ──────────────────────────────────────────────────
try:
    from openai import OpenAI            # GPT-описания фаз
    _client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
except Exception:
    _client = None
# ──────────────────────────────────────────────────

PH_EMOJI = {
    "Новолуние":"🌑","Растущий серп":"🌒","Первая четверть":"🌓","Растущая Луна":"🌔",
    "Полнолуние":"🌕","Убывающая Луна":"🌖","Последняя четверть":"🌗","Убывающий серп":"🌘"
}

FALLBACK_LONG: Dict[str,str] = {
    "Новолуние":         "Нулевая точка цикла: время закладывать намерения и мечты.",
    "Растущий серп":     "Энергия растёт — хорошо браться за новые начинания и обучение.",
    "Первая четверть":   "Критическая отметка: проявляются препятствия, требуются решения.",
    "Растущая Луна":     "Ускорение; расширяйте проекты, наращивайте связки и ресурсы.",
    "Полнолуние":        "Кульминация месяца; максимум эмоций и результатов, работайте с итогами.",
    "Убывающая Луна":    "Отпускаем лишнее, завершаем дела, очищаем пространство.",
    "Последняя четверть":"Пора пересмотра стратегий; фиксируем выводы и корректируем планы.",
    "Убывающий серп":    "Доверяем процессу, отдыхаем, готовим почву для нового цикла."
}

CATEGORIES = {
    "general":  {"favorable":[2,3,9,27], "unfavorable":[13,14,24]},
    "haircut":  {"favorable":[2,3,9],    "unfavorable":[]},
    "travel":   {"favorable":[4,5],      "unfavorable":[]},
    "shopping": {"favorable":[1,2,7],    "unfavorable":[]},
    "health":   {"favorable":[20,21,27], "unfavorable":[]},
}

def jd2dt(jd: float) -> pendulum.DateTime:
    return pendulum.from_timestamp((jd-2440587.5)*86400, tz="UTC")

def phase_name(angle: float)->str:
    i = int(((angle+22.5)%360)//45)
    return ["Новолуние","Растущий серп","Первая четверть","Растущая Луна",
            "Полнолуние","Убывающая Луна","Последняя четверть","Убывающий серп"][i]

def compute_phase(jd: float):
    lon_s = swe.calc_ut(jd, swe.SUN)[0][0]
    lon_m = swe.calc_ut(jd, swe.MOON)[0][0]
    ang   = (lon_m-lon_s)%360
    illum = int(round((1-math.cos(math.radians(ang)))/2*100))
    name  = phase_name(ang)
    sign  = ["Овен","Телец","Близнецы","Рак","Лев","Дева",
             "Весы","Скорпион","Стрелец","Козерог","Водолей","Рыбы"][int(lon_m//30)%12]
    return name, illum, sign

async def gpt_short(date: str, phase: str)->List[str]:
    prompt = (f"Дата {date}, фаза: {phase}. Дай по-русски ровно 3 лаконичных совета, "
              "каждый в отдельной строке, с соответствующим эмодзи в начале: "
              "💼 (работа/финансы), ⛔ (что отложить), 🪄 (ритуал дня).")
    try:
        r=_client.chat.completions.create(model="gpt-4o-mini",
                messages=[{"role":"user","content":prompt}], temperature=0.6)
        lines=[l.strip() for l in r.choices[0].message.content.splitlines() if l.strip()]
        return lines[:3]
    except Exception:                   # fallback
        base=["💼 Сфокусируйся на главном.",
              "⛔ Не принимай поспешных решений.",
              "🪄 Медитация 5 мин. перед сном."]
        return base

async def gpt_long(name:str,month:str)->str:
    prompt=(f"Месяц {month}. Фаза {name}. 2-3 предложения описывают энергетику периода. "
             "Стиль дружелюбный, но экспертный.")
    try:
        r=_client.chat.completions.create(model="gpt-4o-mini",
                messages=[{"role":"user","content":prompt}], temperature=0.7)
        return r.choices[0].message.content.strip()
    except Exception:
        return FALLBACK_LONG[name]

async def generate_calendar(year:int,month:int)->Dict[str,Any]:
    swe.set_ephe_path(".")
    first_day=pendulum.date(year,month,1)
    last_day=first_day.end_of('month')
    cal:Dict[str,Any]={}
    long_cache={}
    tasks=[]
    d=first_day
    while d<=last_day:
        jd=swe.julday(d.year,d.month,d.day,0)
        p_name,illum,sign=compute_phase(jd)
        # время ближайшей смены фазы (грубо – для нов/пол)
        phase_time_iso=jd2dt(jd).in_tz(TZ).to_iso8601_string()
        # краткие советы (асинх)
        tasks.append(asyncio.create_task(gpt_short(d.to_date_string(),p_name)))
        # длинный текст для фазы – кэшируем
        if p_name not in long_cache:
            tasks.append(asyncio.create_task(gpt_long(p_name,d.format('MMMM'))))
            long_cache[p_name]='__pending__'
        rec={
            "phase":f"{PH_EMOJI[p_name]} {p_name} в {sign} ({illum}% освещ.)",
            "percent":illum,
            "sign":sign,
            "phase_time":phase_time_iso,
            "void_of_course":{"start":None,"end":None},   # можно доработать
            "favorable_days":CATEGORIES,
            "unfavorable_days":CATEGORIES,   # для краткости – те же ссылки
        }
        cal[d.to_date_string()]=rec
        d=d.add(days=1)

    # дожидаемся GPT
    results=await asyncio.gather(*tasks)
    short_idx=0
    d=first_day
    while d<=last_day:
        cal[d.to_date_string()]["advice"]=results[short_idx]; short_idx+=1
        d=d.add(days=1)
    # заполняем long_desc
    for k,name in enumerate(long_cache):
        long_cache[name]=results[short_idx+k] if long_cache[name]=='__pending__' else long_cache[name]
    for rec in cal.values():
        rec["long_desc"]=long_cache[rec["phase"].split()[1]]   # берем имя без emoji

    return cal

# ──────────────────────────────────────────────────
async def main():
    today=pendulum.today()
    data=await generate_calendar(today.year,today.month)
    Path("lunar_calendar.json").write_text(json.dumps(data,ensure_ascii=False,indent=2),'utf-8')
    print("✅ lunar_calendar.json обновлён")

if __name__=="__main__":
    asyncio.run(main())