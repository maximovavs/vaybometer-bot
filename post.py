"""
post.py – VayboМетр v4.1  (шаблон «Если сегодня…» фикс)

Изменено vs v4.0:
• gpt_blurb теперь жёстко формирует вывод:
  «Если сегодня что-то пойдёт не так, вините <фактор>. <короткий позитив>.»
• Остальной функционал (пыльца, астрособытия, жирные заголовки) без изменений.
"""

from __future__ import annotations
import asyncio, json, math, os, sys
from datetime import datetime, timedelta
from typing import Dict, Any, Optional

import requests, swisseph as swe
from openai import OpenAI
from telegram import Bot, error as tg_err

LAT, LON = 34.707, 33.022   # Limassol

# ───── helpers ────────────────────────────────────────────────────────────
def _get(url: str, **params) -> Optional[dict]:
    try:
        r = requests.get(url, params=params, timeout=20)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        print(f"[warn] {url} -> {e}", file=sys.stderr); return None

deg2dir=lambda d:"N NE E SE S SW W NW".split()[int((d+22.5)%360//45)]
wind_phrase=lambda k:"штиль"if k<5 else"слабый"if k<15 else"умеренный"if k<30 else"сильный"
clouds_word=lambda pc:"ясно"if pc<25 else"переменная"if pc<70 else"облачно"

def aqi_to_pm25(aqi: float)->float:
    bp=[(0,50,0,12),(51,100,12.1,35.4),(101,150,35.5,55.4),
        (151,200,55.5,150.4),(201,300,150.5,250.4),
        (301,400,250.5,350.4),(401,500,350.5,500.4)]
    for Il,Ih,Cl,Ch in bp:
        if Il<=aqi<=Ih:return round((aqi-Il)*(Ch-Cl)/(Ih-Il)+Cl,1)
    return aqi

# ───── data sources (weather, air, pollen, sst, kp, schumann) ─────────────
def get_weather():
    if (k:=os.getenv("OWM_KEY")):
        for ver in("3.0","2.5"):
            d=_get(f"https://api.openweathermap.org/data/{ver}/onecall",
                   lat=LAT,lon=LON,appid=k,units="metric",exclude="minutely,hourly,alerts")
            if d and d.get("current"):return d
    return _get("https://api.open-meteo.com/v1/forecast",
                latitude=LAT,longitude=LON,current_weather=True,
                hourly="cloud_cover,precipitation_probability,weathercode,surface_pressure",
                daily="temperature_2m_max,temperature_2m_min,precipitation_probability_max",
                timezone="UTC")

def get_air():
    key=os.getenv("AIRVISUAL_KEY")
    return _get("https://api.airvisual.com/v2/nearest_city",
                lat=LAT,lon=LON,key=key) if key else None

def pm10_openmeteo():
    d=_get("https://air-quality-api.open-meteo.com/v1/air-quality",
           latitude=LAT,longitude=LON,hourly="pm10",timezone="UTC")
    try:return round(float(d["hourly"]["pm10"][0]),1)
    except Exception:return None

def get_pollen():
    key=os.getenv("TOMORROW_KEY")
    if not key:return None
    d=_get("https://api.tomorrow.io/v4/timelines",
           apikey=key,location=f"{LAT},{LON}",
           fields="treeIndex,grassIndex,weedIndex",timesteps="1d",units="metric")
    try:return d["data"]["timelines"][0]["intervals"][0]["values"]
    except Exception:return None

def get_sst():
    d=_get("https://marine-api.open-meteo.com/v1/marine",
           latitude=LAT,longitude=LON,
           hourly="sea_surface_temperature",timezone="UTC")
    try:return round(float(d["hourly"]["sea_surface_temperature"][0]),1)
    except Exception:return None

get_kp=lambda:(lambda arr:float(arr[-1][1])if arr else None)(
    _get("https://services.swpc.noaa.gov/products/noaa-planetary-k-index.json"))

def get_schumann():
    d=_get("https://api.glcoherence.org/v1/earth")
    if d:return{"freq":d["frequency_1"],"amp":d["amplitude_1"]}
    d=_get("https://gci-api.ucsd.edu/data/latest")
    if d:return{"freq":d["data"]["sr1"]["frequency"],
                "amp":d["data"]["sr1"]["amplitude"]}
    quiet=True
    for i in(1,2,3):
        if _get("https://api.glcoherence.org/v1/earth",
                date=(datetime.utcnow()-timedelta(days=i)
                ).strftime("%Y-%m-%d")):
            quiet=False
    return{"msg":"датчики молчат третий день — ушли в ретрит"}if quiet else{"prev":"7.8 Гц, спокойно"}

# ───── astrology helpers ────────────────────────────────────────────────
signs="Овне Тельце Близнецах Раке Льве Деве Весах Скорпионе Стрельце Козероге Водолее Рыбах".split()
lunar_effect=("придаёт смелости","заставляет чувствовать комфорт","повышает коммуникабельность",
              "усиливает заботу","разжигает творческий огонь","настраивает на порядок",
              "заставляет искать баланс","поднимает страсть","толкает к приключениям",
              "фокусирует на деле","дарит странные идеи","усиливает эмпатию")

def moon_phase(jd):
    sun=swe.calc_ut(jd,swe.SUN)[0][0]; moon=swe.calc_ut(jd,swe.MOON)[0][0]
    phase=((moon-sun+360)%360)/360
    percent=round(abs(math.cos(math.pi*phase))*100)
    name=("Новолуние"if percent<5 else"Растущая Луна"if phase<.5 else"Полнолуние"if percent>95 else"Убывающая Луна")
    sign=int(moon//30)
    return f"{name} в {signs[sign]} — {lunar_effect[sign]} ({percent}% )"

def planet_parade(jd):
    bodies=[swe.MERCURY,swe.VENUS,swe.MARS,swe.JUPITER,swe.SATURN]
    lons=sorted(swe.calc_ut(jd,b)[0][0] for b in bodies)
    best=min((lons[i+2]-lons[i])%360 for i in range(len(lons)-2))
    return"Мини-парад планет"if best<90 else None

def aspect(jd):
    lon1,lon2=swe.calc_ut(jd,swe.VENUS)[0][0],swe.calc_ut(jd,swe.JUPITER)[0][0]
    diff=abs((lon1-lon2+180)%360-180)
    return"Трин Венеры и Юпитера — волна удачи"if diff<4 else None

def meteor_shower():
    showers={"Eta Aquarids":((4,19),(5,28),(6,6),60)}
    today=datetime.utcnow().date()
    for name,(start,peak,end,max_zhr) in showers.items():
        if datetime(today.year,*start).date()<=today<=datetime(today.year,*end).date():
            if today==datetime(today.year,*peak).date():
                return f"Метеорный поток {name} — до {max_zhr} метеоров/ч сейчас"
            return f"{name} активен (пик {peak[1]} {datetime(today.year,*peak).strftime('%b')})"
    return None

def astro_events():
    jd=swe.julday(*datetime.utcnow().timetuple()[:3])
    parts=[moon_phase(jd)]
    if p:=planet_parade(jd): parts.append(p)
    if a:=aspect(jd): parts.append(a)
    if swe.calc_ut(jd,swe.MERCURY)[0][3]<0: parts.append("Меркурий ретрограден")
    if m:=meteor_shower(): parts.append(m)
    return"\n".join(parts)

# ───── GPT blurb (fixed template) ───────────────────────────────────────
def gpt_blurb(culprit:str)->tuple[str,str]:
    prompt=(
        "Сформируй вывод ровно в одну строку, начинай дословно: "
        "«Если сегодня что-то пойдёт не так, вините " + culprit + ".». "
        "Допиши ещё короткий позитив (≤12 слов). "
        "Пустая строка. Потом ровно 3 bullets, эмодзи приветствуются, ≤12 слов."
    )
    rsp=OpenAI(api_key=os.environ["OPENAI_API_KEY"]).chat.completions.create(
        model="gpt-4o-mini",temperature=0.6,
        messages=[{"role":"user","content":prompt}]
    ).choices[0].message.content.strip()
    lines=[l.strip() for l in rsp.splitlines() if l.strip()]
    summary=lines[0]
    tips=[l.lstrip("-• ").strip() for l in lines[1:4]]
    return summary,"\n".join(f"- {t}" for t in tips)

# ───── digest builder ───────────────────────────────────────────────────
def build_md(d:Dict[str,Any])->str:
    P=[]
    w=d["weather"]; culprit=""
    # WEATHER (both providers)
    if"current" in w:
        cur,day=w["current"],w["daily"][0]["temp"];cloud=clouds_word(cur.get("clouds",0));wind=cur["wind_speed"]*3.6;press=cur["pressure"]
        P+=["☀️ <b>Погода</b>",
            f"<b>Температура:</b> днём до {day['max']:.0f} °C, ночью около {day['min']:.0f} °C",
            f"<b>Облачность:</b> {cloud}",
            "<b>Осадки:</b> не ожидаются" if w["daily"][0].get("rain",0)==0 else "<b>Осадки:</b> возможен дождь",
            f"<b>Ветер:</b> {wind_phrase(wind)} ({wind:.1f} км/ч), {deg2dir(cur['wind_deg'])}",
            f"<b>Давление:</b
