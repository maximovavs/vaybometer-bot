"""
post.py – VayboМетр v3.8
• Жирные заголовки
• «Облачность» → ясно / переменная / облачно
• Автокомментарий погоды (темп.-ветер-давление)
• + трин Венера-Юпитер; ретроградный Меркурий
• Шуточный вывод всегда с конкретным «виновником»
• Резонанс Шумана: если нет fresh-API, пишем «вчера было спокойно»
"""

from __future__ import annotations
import asyncio, json, os, sys, math
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

import requests, swisseph as swe
from openai import OpenAI
from telegram import Bot, error as tg_err

LAT, LON = 34.707, 33.022          # Limassol

# ── helpers ────────────────────────────────────────────────────────────────
def _get(url: str, **params) -> Optional[dict]:
    try:
        r = requests.get(url, params=params, timeout=20); r.raise_for_status()
        return r.json()
    except Exception as e:
        print(f"[warn] {url} -> {e}", file=sys.stderr); return None

deg2dir = lambda d: "N NE E SE S SW W NW".split()[int((d+22.5)%360//45)]
wind_phrase = lambda k: "штиль" if k<5 else "слабый" if k<15 else "умеренный" if k<30 else "сильный"

def clouds_word(val: int) -> str:
    return "ясно" if val < 25 else "переменная" if val < 70 else "облачно"

def aqi_to_pm25(aqi: float) -> float:
    bp=[(0,50,0,12),(51,100,12.1,35.4),(101,150,35.5,55.4),
        (151,200,55.5,150.4),(201,300,150.5,250.4),
        (301,400,250.5,350.4),(401,500,350.5,500.4)]
    for Il,Ih,Cl,Ch in bp:
        if Il <= aqi <= Ih:
            return round((aqi-Il)*(Ch-Cl)/(Ih-Il)+Cl,1)
    return aqi

# ── sources ────────────────────────────────────────────────────────────────
def get_weather():
    k=os.getenv("OWM_KEY")
    if k:
        for ver in ("3.0","2.5"):
            d=_get(f"https://api.openweathermap.org/data/{ver}/onecall",
                   lat=LAT,lon=LON,appid=k,units="metric",
                   exclude="minutely,hourly")
            if d and d.get("current"): return d
    return _get("https://api.open-meteo.com/v1/forecast",
                latitude=LAT,longitude=LON,current_weather=True,
                hourly="cloud_cover,precipitation_probability,weathercode,surface_pressure",
                daily="temperature_2m_max,temperature_2m_min,precipitation_probability_max",
                timezone="UTC")

get_air=lambda: _get("https://api.airvisual.com/v2/nearest_city",
                     lat=LAT,lon=LON,key=os.getenv("AIRVISUAL_KEY")) if os.getenv("AIRVISUAL_KEY") else None

def get_pm10_fallback()->Optional[float]:
    d=_get("https://air-quality-api.open-meteo.com/v1/air-quality",
           latitude=LAT,longitude=LON,hourly="pm10",timezone="UTC")
    try:return round(float(d["hourly"]["pm10"][0]),1)
    except Exception:return None

def get_sst():
    d=_get("https://marine-api.open-meteo.com/v1/marine",
           latitude=LAT,longitude=LON,hourly="sea_surface_temperature",timezone="UTC")
    try:return round(float(d["hourly"]["sea_surface_temperature"][0]),1)
    except Exception:return None

get_kp=lambda: (lambda a: float(a[-1][1]) if a else None)(
    _get("https://services.swpc.noaa.gov/products/noaa-planetary-k-index.json"))

def get_schumann():
    d=_get("https://api.glcoherence.org/v1/earth")
    if d: return {"freq":d["frequency_1"],"amp":d["amplitude_1"]}
    # fallback – вчера
    y=_get("https://api.glcoherence.org/v1/earth",
           date=(datetime.utcnow()-timedelta(days=1)).strftime("%Y-%m-%d"))
    return {"prev":"спокойно"} if y else None

# ─── астрология ────────────────────────────────────────────────────────────
def astro_events():
    jd=swe.julday(*datetime.utcnow().timetuple()[:3])
    lon=lambda body: swe.calc_ut(jd,body)[0][0]
    moon_sign=int(lon(swe.MOON)//30)
    sign_names="Овне Тельце Близнецах Раке Льве Деве Весах Скорпионе Стрельце Козероге Водолее Рыбах".split()
    out=[f"Луна в {sign_names[moon_sign]}"]
    # ретро Меркурий
    if swe.calc_ut(jd,swe.MERCURY)[0][3] < 0:
        out.append("Меркурий ретрограден")
    # трин Венера-Юпитер?
    diff=abs((lon(swe.VENUS)-lon(swe.JUPITER)+180)%360-180)
    if diff<4:
        out.append("Трин Венеры с Юпитером – бонус удачи")
    return " | ".join(out)

# ─── GPT ───────────────────────────────────────────────────────────────────
def gpt_comment(data: dict, culprit: str)->tuple[str,str]:
    prompt = (
        "Ты — дерзкий бот-астролог Gen-Z. Напиши:\n"
        "1) Один смешной вывод (1-2 предложения). "
        f"Если пойдёт не так — вини {culprit}.\n"
        "2) Три коротких, креативных совета, bullets, без занудства."
    )
    rsp=OpenAI(api_key=os.environ["OPENAI_API_KEY"]).chat.completions.create(
        model="gpt-4o-mini",temperature=0.5,
        messages=[{"role":"user","content":prompt}]
    ).choices[0].message.content.strip()
    summary,*tips=rsp.splitlines()
    tips=[t.lstrip("-• ").strip() for t in tips if t.strip()]
    return summary,"\n".join(f"- {t}" for t in tips[:3])

# ─── digest ───────────────────────────────────────────────────────────────
def build_md(d:Dict[str,Any])->str:
    P=[]; snip={}
    # weather
    w=d["weather"]
    if "current" in w:
        cur,day=w["current"],w["daily"][0]["temp"]
        cloud_txt=clouds_word(cur.get("clouds",0))
        wind_k=cur["wind_speed"]*3.6
        P+=["☀️ **Погода**",
            f"**Температура:** днём до {day['max']:.0f} °C, ночью около {day['min']:.0f} °C",
            f"**Облачность:** {cloud_txt}",
            f"**Осадки:** не ожидаются" if w['daily'][0].get("rain",0)==0 else "**Осадки:** возможен дождь",
            f"**Ветер:** {wind_phrase(wind_k)} ({wind_k:.1f} км/ч), {deg2dir(cur['wind_deg'])}",
            f"**Давление:** {cur['pressure']} гПа"]
        snip.update(temp=day['max'],pressure=cur['pressure'])
        comment=f"Тёплый {wind_phrase(wind_k)}, давление {cur['pressure']} гПа — слегка {'ниже' if cur['pressure']<1010 else 'выше'} нормы."
    else:
        cw=w["current_weather"]; dm=w["daily"]
        cloud_txt=clouds_word(w["hourly"]["cloud_cover"][0])
        pp=w["daily"]["precipitation_probability_max"][0]
        press=w["hourly"]["surface_pressure"][0]
        P+=["☀️ **Погода**",
            f"**Температура:** днём до {dm['temperature_2m_max'][0]:.0f} °C, "
            f"ночью около {dm['temperature_2m_min'][0]:.0f} °C",
            f"**Облачность:** {cloud_txt}",
            f"**Осадки:** не ожидаются" if pp<20 else "**Осадки:** возможен дождь",
            f"**Ветер:** {wind_phrase(cw['windspeed'])} ({cw['windspeed']:.1f} км/ч), {deg2dir(cw['winddirection'])}",
            f"**Давление:** {press:.0f} гПа"]
        snip.update(temp=dm['temperature_2m_max'][0],pressure=press)
        comment=f"Лайтовый бриз и давление {press:.0f} гПа — всё в пределах комфорта."
    P.append(comment)

    # air
    if air:=d["air"]:
        pol=air["data"]["current"]["pollution"]; pm25=pol.get("p2") or aqi_to_pm25(pol["aqius"])
        pm10=pol.get("p1") or d["pm10_fallback"] or "нет данных"
        level=("хороший" if pol['aqius']<=50 else "умеренный" if pol['aqius']<=100 else "вредный")
        P+=["","🌬️ **Качество воздуха**",
            f"**AQI:** {pol['aqius']}  |  **PM2.5:** {pm25} µg/m³  |  **PM10:** {pm10} µg/m³",
            f"Воздух {level}."]
        snip["aqi"]=pol['aqius']

    # geomagnetic
    kp=d["kp"]; state="буря" if kp and kp>=5 else "спокойный" if kp and kp<4 else "повышенный"
    P+=["","🌌 **Геомагнитная активность**",f"**Уровень:** {state} (Kp {kp:.1f})" if kp else "нет данных"]
    snip["kp"]=kp or 0

    # schumann
    if sch:=d["schumann"]:
        if "prev" in sch:
            P+=["","📈 **Резонанс Шумана**","нет данных, вчера было спокойно"]
        else:
            P+=["","📈 **Резонанс Шумана**",
                f"**Частота:** ≈{sch['freq']:.1f} Гц","амплитуда стабильна"]
    else:
        P+=["","📈 **Резонанс Шумана**","нет данных"]

    if d["sst"]:
        P+=["","🌊 **Температура воды в море**",f"**Сейчас:** {d['sst']} °C"]

    astro=astro_events()
    if astro:
        P+=["","🔮 **Астрологические события**",astro]

    P.append("---")

    # culprit for joke
    culprit = ("ретроградного Меркурия" if "ретрограден" in astro else
               "магнитных бурь" if kp and kp>=5 else
               "низкого давления" if abs(snip['pressure']-1013)>6 else
               "соседей по зодиаку")
    summary,tips=gpt_comment(snip,culprit)
    P+=["**📝 Вывод**",summary,"","---","**✅ Рекомендации**",tips]
    return "\n".join(P)

# ── Telegram ───────────────────────────────────────────────────────────────
async def send(md:str):
    await Bot(os.environ["TELEGRAM_TOKEN"]).send_message(
        chat_id=os.environ["CHANNEL_ID"], text=md[:4096],
        parse_mode="Markdown", disable_web_page_preview=True)

# ── main ───────────────────────────────────────────────────────────────────
async def main():
    data={"weather":get_weather(),"air":get_air(),
          "pm10_fallback":get_pm10_fallback(),"sst":get_sst(),
          "kp":get_kp(),"schumann":get_schumann()}
    md=build_md(data); print("MD preview:", md[:220].replace("\n"," | "))
    try: await send(md); print("✓ sent")
    except tg_err.TelegramError as e: print("Telegram error:", e,file=sys.stderr); raise

if __name__=="__main__":
    asyncio.run(main())
