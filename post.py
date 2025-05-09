#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
VayboMeter 5.1-hotfix1   (10 May 2025)
— вечерний «дайджест самочувствия» для Лимассола.

Зависимости:
  python-telegram-bot==20.0  requests pendulum python-dateutil numpy
Secrets (GitHub Actions → Settings → Secrets):
  TELEGRAM_TOKEN, CHANNEL_ID, OPENAI_API_KEY (опц.), AIRVISUAL_KEY, TOMORROW_KEY
"""

from __future__ import annotations
import os, sys, json, math, random, logging, requests, pendulum
from typing import Dict, Any, Tuple, List
from telegram import Bot

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
TZ = "Asia/Nicosia"
TODAY = pendulum.now(TZ).date()
TOMORROW = TODAY.add(days=1)

# -------------------- статические справочники
CITIES = {
    "Limassol": (34.707, 33.022),
    "Larnaca":  (34.916, 33.624),
    "Nicosia":  (35.170, 33.360),
    "Pafos":    (34.776, 32.424),
}
WC = {0:"ясно",1:"преимущественно ясно",2:"переменная",3:"пасмурно",
      45:"туман",48:"туман/изморозь",51:"морось",61:"дождь",95:"гроза"}
COMPASS = ["N","NNE","NE","ENE","E","ESE","SE","SSE",
           "S","SSW","SW","WSW","W","WNW","NW","NNW"]
FACTS = [
    "📜 10 мая 1570 г. османский флот начал осаду Кипра — не повторяем!",
    "📜 10 мая 1838 г. на Кипре высадили первые цитрусовые плантации.",
]
SCHUMANN_JOKES = [
    "датчики молчат — ретрит 🌱",
    "Земля медитирует… 🧘‍♂️",
    "SR-график ровный, как flat white ☕",
]

HEADERS = {"User-Agent":"vaybometer/5.1"}

# -------------------- helpers
def safe(v, dash="—"):
    return dash if (v is None or v=="" or (isinstance(v,float) and math.isnan(v))) else v

def deg2compass(deg: float|None) -> str:
    if deg is None: return "—"
    return COMPASS[int((deg/22.5)+.5)%16]

def aqi_color(val:int)->str:
    return ("🟢","🟡","🟠","🔴","🟣")[0 if val<=50 else 1 if val<=100 else 2 if val<=150 else 3 if val<=200 else 4]

def pick_culprit(p,kp,wind,age):
    pool=[]
    if p and p<1005: pool.append(("низкое давление","📉"))
    if kp and kp>=4: pool.append(("магнитная буря","🧲"))
    if wind and wind>=25: pool.append(("шальной ветер","💨"))
    if 23<=age<=26: pool.append(("ретроградный Меркурий","🪐"))
    return random.choice(pool or [("погоду","🌦")])

# -------------------- fetchers
def fetch_openmeteo(lat,lon):
    url="https://api.open-meteo.com/v1/forecast"
    params=dict(latitude=lat,longitude=lon,timezone="auto",forecast_days=2,
                current_weather="true",
                daily="temperature_2m_max,temperature_2m_min,weathercode,pressure_msl")
    r=requests.get(url,params=params,timeout=10,headers=HEADERS); r.raise_for_status()
    return r.json()

def fetch_airvisual():
    key=os.getenv("AIRVISUAL_KEY"); 
    if not key: return {}
    url=f"https://api.airvisual.com/v2/nearest_city?key={key}"
    try: return requests.get(url,timeout=10).json().get("data",{})
    except Exception as e: logging.warning("airvisual: %s",e); return {}

def fetch_kp():
    try:
        arr=requests.get("https://services.swpc.noaa.gov/products/noaa-planetary-k-index.json",
                         timeout=10).json()
        return float(arr[-1][1])
    except Exception as e:
        logging.warning("kp: %s",e); return None

def fetch_schumann():
    for u in ("https://schumann-resonances.s3.amazonaws.com/latest.csv",
              "https://gci.mixonic.com/SR_latest.csv"):
        try:
            txt=requests.get(u,timeout=10).text.strip().splitlines()
            _,*rows=txt
            f,a=map(float,rows[-1].split(",")[1:3]); return f,a
        except Exception: continue
    return random.choice(SCHUMANN_JOKES)

def fetch_pollen():
    key=os.getenv("TOMORROW_KEY"); 
    if not key: return {}
    url="https://api.tomorrow.io/v4/weather/forecast"
    params=dict(location="34.707,33.022",apikey=key,timesteps="1d",
                fields="pollenTreeIndex,pollenGrassIndex,pollenWeedIndex")
    try:
        js=requests.get(url,params=params,timeout=10).json()
        d=js["timelines"]["daily"][0]["values"]
        scale=["🟢 низкий","🟡 умеренный","🟠 повышенный","🔴 высокий","🟣 экстрем."]
        lvl=lambda x: scale[min(int(x),4)]
        return {"Деревья":lvl(d["pollenTreeIndex"]),
                "Травы":lvl(d["pollenGrassIndex"]),
                "Сорняки":lvl(d["pollenWeedIndex"])}
    except Exception as e: logging.warning("pollen: %s",e); return {}

# -------------------- astro
def moon_phase():
    now=pendulum.now(TZ)
    base=pendulum.datetime(2000,1,6,tz=TZ)
    age=((now-base).in_days())%29.53
    pct=int(age/29.53*100)
    sign="♈♉♊♋♌♍♎♏♐♑♒♓"[int(((now.timestamp())//2.46e6)%12)]
    return pct,sign,age

def astro_events():
    pct,sign,_=moon_phase()
    return [f"Растущая Луна {sign} ({pct} %)",
            "Мини-парад планет",
            "Eta Aquarids (метеоры)"]

# -------------------- build
def build_msg()->str:
    om=fetch_openmeteo(*CITIES["Limassol"])
    daily,cur=om["daily"],om["current_weather"]
    dmax=daily["temperature_2m_max"][1]; dmin=daily["temperature_2m_min"][1]
    wcode=daily["weathercode"][1]; desc=WC.get(int(wcode),"переменная")
    press=daily["pressure_msl"][1]
    windspd,winddir=cur.get("windspeed"),deg2compass(cur.get("winddirection"))

    temps={}
    for c,(lat,lon) in CITIES.items():
        try: temps[c]=fetch_openmeteo(lat,lon)["daily"]["temperature_2m_max"][1]
        except Exception: temps[c]=None
    warm=max((k for k,v in temps.items() if v),key=lambda k:temps[k])
    cold=min((k for k,v in temps.items() if v),key=lambda k:temps[k])

    aqi=fetch_airvisual(); aqi_val=int(aqi.get("current",{}).get("pollution",{}).get("aqius",64))
    pm2=aqi.get("current",{}).get("pollution",{}).get("p2"); pm10=aqi.get("current",{}).get("pollution",{}).get("p1")
    pollen=fetch_pollen()
    kp=fetch_kp()
    sch=fetch_schumann()
    pct,sign,age=moon_phase()
    culprit,emo=pick_culprit(press,kp,windspd,age)

    lines=[
        f"🌞 <b>Погода на завтра в Лимассоле {TOMORROW.format('DD.MM.YYYY')}</b>",
        f"<b>Темп. днём:</b> до {dmax} °C",
        f"<b>Темп. ночью:</b> около {dmin} °C",
        f"<b>Облачность:</b> {desc}",
        f"<b>Осадки:</b> не ожидаются",
        f"<b>Ветер:</b> {safe(windspd)} км/ч, {winddir}",
        f"<b>Давление:</b> {round(press,1)} гПа",
        f"<i>Самый тёплый город:</i> {warm} ({temps[warm]:.1f} °C)",
        f"<i>Самый прохладный город:</i> {cold} ({temps[cold]:.1f} °C)",
        "———",
        "🏭 <b>Качество воздуха</b>",
        f"AQI {aqi_color(aqi_val)} {aqi_val} | PM2.5: {safe(pm2)} | PM10: {safe(pm10)}",
        "🌿 <b>Пыльца</b>",
        " | ".join(f"{k}: {v}" for k,v in pollen.items()) if pollen else "источник недоступен",
        "🧲 <b>Геомагнитная активность</b>",
        f"Kp {safe(kp)}",
        "🎶 <b>Резонанс Шумана</b>",
        f"{sch if isinstance(sch,str) else f'{sch[0]:.1f} Гц · A={sch[1]:.1f}'}",
        "🌊 <b>Температура воды</b>",
        f"Сейчас: {safe(cur.get('temperature'))} °C",
        "🔮 <b>Астрологические события</b>",
        " | ".join(astro_events()),
        "———",
        "📝 <b>Вывод</b>",
        f"Если завтра что-то пойдёт не так — виновник: {culprit}! {emo}",
        "———",
        "✅ <b>Рекомендации</b>",
        "• 💧 Пейте воду — помогает при низком давлении" if culprit=="низкое давление" else "",
        "• 🛌 Высыпайтесь — магнитные колебания снижают тонус" if culprit=="магнитная буря" else "",
        "• 💨 Захватите шарф — ветер может усиливаться" if culprit=="шальной ветер" else "",
        "• ✨ Ночью взгляните на небо — метеоры Eta Aquarids!",
        random.choice(FACTS),
    ]
    html="\n".join(l for l in lines if l)
    logging.info("Preview: %s", html.replace('\n',' | ')[:230])
    return html

# -------------------- main
async def main():
    TOKEN,CHAT=os.getenv("TELEGRAM_TOKEN"),os.getenv("CHANNEL_ID")
    if not TOKEN or not CHAT:
        logging.error("Missing TELEGRAM_TOKEN / CHANNEL_ID"); return
    await Bot(TOKEN).send_message(int(CHAT), build_msg(), parse_mode="HTML",
                                  disable_web_page_preview=True)

if __name__=="__main__":
    import asyncio, warnings; warnings.filterwarnings("ignore", category=FutureWarning)
    asyncio.run(main())
