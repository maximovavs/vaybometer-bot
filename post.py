"""
post.py – VayboМетр v3.7
"""

from __future__ import annotations
import asyncio, json, os, sys
from datetime import datetime
from typing import Any, Dict, Optional

import requests, swisseph as swe
from openai import OpenAI
from telegram import Bot, error as tg_err

LAT, LON = 34.707, 33.022

# ───────── helpers ─────────
def _get(u: str, **p) -> Optional[dict]:
    try:
        r = requests.get(u, params=p, timeout=20); r.raise_for_status()
        return r.json()
    except Exception as e:
        print(f"[warn] {u} -> {e}", file=sys.stderr); return None

deg2dir = lambda d: "N NE E SE S SW W NW".split()[int((d+22.5)%360//45)]
wind_phrase = lambda k: "штиль" if k<5 else "слабый" if k<15 else "умеренный" if k<30 else "сильный"

def aqi_to_pm25(aqi: float) -> float:
    bp=[(0,50,0,12),(51,100,12.1,35.4),(101,150,35.5,55.4),
        (151,200,55.5,150.4),(201,300,150.5,250.4),(301,400,250.5,350.4),(401,500,350.5,500.4)]
    for Il,Ih,Cl,Ch in bp:
        if Il<=aqi<=Ih: return round((aqi-Il)*(Ch-Cl)/(Ih-Il)+Cl,1)
    return aqi

# ───────── data sources ─────────
def get_weather():
    k=os.getenv("OWM_KEY")
    if k:
        for ver in ("3.0","2.5"):
            d=_get(f"https://api.openweathermap.org/data/{ver}/onecall",
                   lat=LAT,lon=LON,appid=k,units="metric",exclude="minutely,hourly")
            if d and d.get("current") and d.get("daily"): return d
    return _get("https://api.open-meteo.com/v1/forecast",
                latitude=LAT,longitude=LON,current_weather=True,
                hourly="cloud_cover,precipitation_probability,weathercode,surface_pressure",
                daily="temperature_2m_max,temperature_2m_min,precipitation_probability_max",
                timezone="UTC")

get_air = lambda: _get("https://api.airvisual.com/v2/nearest_city",
                       lat=LAT,lon=LON,key=os.getenv("AIRVISUAL_KEY")) if os.getenv("AIRVISUAL_KEY") else None

def get_pm10_fallback()->Optional[float]:
    d=_get("https://air-quality-api.open-meteo.com/v1/air-quality",
           latitude=LAT,longitude=LON,hourly="pm10",timezone="UTC")
    try:return round(float(d["hourly"]["pm10"][0]),1)
    except Exception:return None

def get_pollen():
    k=os.getenv("TOMORROW_KEY")
    if not k:return None
    d=_get("https://api.tomorrow.io/v4/timelines",apikey=k,location=f"{LAT},{LON}",
           fields="treeIndex,grassIndex,weedIndex",timesteps="1d")
    try:v=d["data"]["timelines"][0]["intervals"][0]["values"];return{"tree":v["treeIndex"],"grass":v["grassIndex"],"weed":v["weedIndex"]}
    except Exception:return None

get_sst=lambda: (lambda d: round(float(d["hourly"]["sea_surface_temperature"][0]),1) if d else None)(
    _get("https://marine-api.open-meteo.com/v1/marine",
         latitude=LAT,longitude=LON,hourly="sea_surface_temperature",timezone="UTC"))

get_kp=lambda: (lambda a: float(a[-1][1]) if a else None)(
    _get("https://services.swpc.noaa.gov/products/noaa-planetary-k-index.json"))

def get_schumann():
    d=_get("https://api.glcoherence.org/v1/earth")
    return {"freq":d["frequency_1"],"amp":d["amplitude_1"]} if d else None

def astro_events():
    jd=swe.julday(*datetime.utcnow().timetuple()[:3])
    moon_lon=swe.calc_ut(jd,swe.MOON)[0][0]; moon_sign=int(moon_lon//30)
    sign_names="Овне Тельце Близнецах Раке Льве Деве Весах Скорпионе Стрельце Козероге Водолее Рыбах".split()
    moon=f"Луна в {sign_names[moon_sign]}"
    # ретроградный Меркурий?
    merc_spd=swe.calc_ut(jd,swe.MERCURY)[0][3]
    merc="Меркурий ретрограден – техника шалит" if merc_spd<0 else None
    return " | ".join(filter(None,[moon,merc]))

# ───────── GPT ─────────
def gpt_comment(snip: dict)->tuple[str,str]:
    style = ("Ты — весёлый, слегка саркастичный бот-астролог Gen Z. "
             "Дай 1–2 предложения вывода с шуткой («если что-то пойдёт не так, вините …»). "
             "Затем 3–4 коротких, небанальных, развлекательных совета.\n"
             "Формат:\nВывод: …\nСоветы:\n- …")
    rsp=OpenAI(api_key=os.environ["OPENAI_API_KEY"]).chat.completions.create(
        model="gpt-4o-mini",temperature=0.5,
        messages=[{"role":"user","content":style+"\n"+json.dumps(snip,ensure_ascii=False)}]
    ).choices[0].message.content.strip()
    summary=rsp.split("Советы:")[0].replace("Вывод:","").strip()
    tips=[l.strip("- ").strip("0123456789. ").strip() for l in rsp.split("Советы:")[-1].splitlines() if l.strip()]
    tips=[t for t in tips if t and summary not in t][:4]
    return summary,"\n".join(f"- {t}" for t in tips)

# ───────── digest ─────────
def build_md(d:Dict[str,Any])->str:
    P,snip=[],{}
    # weather
    if w:=d["weather"]:
        if "current" in w:
            cur,day=w["current"],w["daily"][0]["temp"]; wind=cur["wind_speed"]*3.6
            P+=["☀️ **Погода**",
                 f"**Температура:** днём до {day['max']:.0f} °C, ночью около {day['min']:.0f} °C",
                 f"**Облачность:** {cur.get('clouds','—')} %",
                 f"**Осадки:** не ожидаются" if w['daily'][0].get("rain",0)==0 else "**Осадки:** возможен дождь",
                 f"**Ветер:** {wind_phrase(wind)} ({wind:.1f} км/ч), {deg2dir(cur['wind_deg'])}",
                 f"**Давление:** {cur['pressure']} гПа"]
            snip.update(temp_min=day['min'],temp_max=day['max'],pressure=cur['pressure'])
        else:
            cw,dm=w["current_weather"],w["daily"]; cloud=w["hourly"]["cloud_cover"][0]; pp=w["daily"]["precipitation_probability_max"][0]
            pressure=w["hourly"]["surface_pressure"][0]
            P+=["☀️ **Погода**",
                 f"**Температура:** днём до {dm['temperature_2m_max'][0]:.0f} °C, "
                 f"ночью около {dm['temperature_2m_min'][0]:.0f} °C",
                 f"**Облачность:** {cloud} %",
                 f"**Осадки:** не ожидаются" if pp<20 else "**Осадки:** возможен дождь",
                 f"**Ветер:** {wind_phrase(cw['windspeed'])} ({cw['windspeed']:.1f} км/ч), {deg2dir(cw['winddirection'])}",
                 f"**Давление:** {pressure:.0f} гПа"]
            snip.update(temp_min=dm['temperature_2m_min'][0],temp_max=dm['temperature_2m_max'][0],pressure=pressure)

    # air
    if air:=d["air"]:
        pol=air["data"]["current"]["pollution"]; pm25=pol.get("p2") or aqi_to_pm25(pol["aqius"])
        pm10=pol.get("p1") or get_pm10_fallback() or "нет данных"
        P+=["","🌬️ **Качество воздуха**",
            f"**AQI:** {pol['aqius']}  |  **PM2.5:** {pm25} µg/m³  |  **PM10:** {pm10} µg/m³"]
        level=("хороший" if pol['aqius']<=50 else "умеренный" if pol['aqius']<=100 else "вредный")
        P+= [f"Воздух {level}."] ; snip["aqi"]=pol['aqius']

    if p:=d["pollen"]:
        P+=["","🌿 **Уровень пыльцы**",f"**Деревья:** {p['tree']}  |  **Травы:** {p['grass']}  |  **Сорняки:** {p['weed']}"]

    if (kp:=d["kp"]) is not None:
        state="буря (G1)" if kp>=5 else "спокойный" if kp<4 else "повышенный"
        P+=["","🌌 **Геомагнитная активность**",f"**Уровень:** {state} (Kp {kp:.1f})"]; snip["kp"]=kp

    # Schumann always visible
    if d["schumann"]:
        s=d["schumann"]; P+=["","📈 **Резонанс Шумана**",f"**Частота:** ≈{s['freq']:.1f} Гц",f"**Амплитуда:** {s['amp']}"]
    else:
        P+=["","📈 **Резонанс Шумана**","нет актуальных данных"]

    if d["sst"]:
        P+=["","🌊 **Температура воды в море**",f"**Сейчас:** {d['sst']} °C"]

    if (astro:=astro_events()):
        P+=["","🔮 **Астрологические события**",astro]

    P.append("---")
    summ,tips=gpt_comment(snip)
    P+=["**📝 Вывод**",summ,"","---","**✅ Рекомендации**",tips]
    return "\n".join(P)

# ───────── Telegram ─────────
async def send(md:str):
    await Bot(os.environ["TELEGRAM_TOKEN"]).send_message(
        chat_id=os.environ["CHANNEL_ID"], text=md[:4096],
        parse_mode="Markdown", disable_web_page_preview=True)

# ───────── main ─────────
async def main():
    d={"weather":get_weather(),"air":get_air(),"pollen":get_pollen(),"sst":get_sst(),
       "kp":get_kp(),"schumann":get_schumann()}
    md=build_md(d); print("MD preview:", md[:200].replace("\n"," | "))
    try: await send(md); print("✓ sent")
    except tg_err.TelegramError as e:
        print("Telegram error:", e, file=sys.stderr); raise

if __name__=="__main__":
    asyncio.run(main())
