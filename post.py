#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
VayboMeter 5.1  —  «вечерний дайджест самочувствия» для Limassol (CY).
© 2025, MIT.  Чистый Python 3.11, без асинх-I/O телеграм-фреймворков.
Структура:
  • fetch_* … все внешние API, максимально короткие таймауты + graceful-fallback
  • utils    … конвертация угла ветра, цветовых шкал, форматеров
  • build_msg() → HTML-строка (<4096)
  • main()   … cron-точка входа
"""

from __future__ import annotations
import os, sys, json, math, random, textwrap, logging, datetime, requests, pendulum
from typing import Dict, Any, Tuple, List
from telegram import Bot

logging.basicConfig(
    level=logging.INFO, format="%(levelname)s: %(message)s", stream=sys.stdout
)
TZ = "Asia/Nicosia"
TODAY = pendulum.now(TZ).date()
TOMORROW = TODAY.add(days=1)
# -------------------------------------------------------  STATIC MAPS
CITIES = {
    "Limassol": (34.707, 33.022),
    "Larnaca": (34.916, 33.624),
    "Nicosia": (35.170, 33.360),
    "Pafos": (34.776, 32.424),
}
WC = {0: "ясно", 1: "преимущественно ясно", 2: "переменная", 3: "пасмурно",
      45: "туман", 48: "изморозь / туман", 51: "морось", 61: "дождь",
      71: "снег", 95: "гроза"}
COMPASS = ["N","NNE","NE","ENE","E","ESE","SE","SSE","S","SSW","SW",
           "WSW","W","WNW","NW","NNW"]
FACTS = [
    "📜 10 мая 1570 г. османский флот начал осаду Кипра — не повторяем!",
    "📜 В этот день 1838 г. на Кипре зацвели первые цитрусовые плантации.",
]
SCHUMANN_JOKES = [
    "датчики молчат — ретрит 🌱",
    "колебания незаметны — Земля медитирует 🧘‍♂️",
    "SR-график ровный, как утренний flat white ☕",
]

# ░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░  UTILS
def deg2compass(deg: float) -> str:
    if deg is None:
        return "—"
    idx = int((deg / 22.5) + .5) % 16
    return COMPASS[idx]

def aqi_color(val: int) -> str:
    return ("🟢", "🟡", "🟠", "🔴", "🟣")[
        0 if val <=50 else 1 if val<=100 else 2 if val<=150 else 3 if val<=200 else 4]

def pick_culprit(p: float, kp: float, wind: float, moon_age: float)->Tuple[str,str]:
    """вернёт (виновник, эмодзи)"""
    pool = []
    if p and p<1005: pool.append(("низкое давление", "📉"))
    if kp and kp>=4: pool.append(("магнитная буря", "🧲"))
    if wind and wind>=25: pool.append(("шальной ветер", "💨"))
    if 23<=moon_age<=26: pool.append(("ретроградный Меркурий", "🪐"))
    return random.choice(pool or [("погоду", "🌦")])

def safe(val, dash="—"):
    return dash if (val is None or val == "" or (isinstance(val,float) and math.isnan(val))) else val

# ░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░  FETCHERS
HEADERS = {"User-Agent":"vaybometer/5.1"}
def fetch_openmeteo(lat,lon) -> Dict[str,Any]:
    url="https://api.open-meteo.com/v1/forecast"
    params=dict(
        latitude=lat, longitude=lon, timezone="auto", forecast_days=2,
        current_weather="true",
        daily="temperature_2m_max,temperature_2m_min,apparent_temperature_max,apparent_temperature_min,weathercode,pressure_msl,precipitation_probability_max",
    )
    r=requests.get(url, params=params, timeout=10, headers=HEADERS)
    r.raise_for_status()
    return r.json()

def fetch_airvisual() -> Dict[str,Any]:
    key=os.getenv("AIRVISUAL_KEY")
    if not key: return {}
    url=f"https://api.airvisual.com/v2/nearest_city?key={key}"
    return requests.get(url,timeout=10).json().get("data",{})

def fetch_kp() -> float|None:
    try:
        url="https://services.swpc.noaa.gov/products/noaa-planetary-k-index.json"
        arr=requests.get(url,timeout=10).json()
        return float(arr[-1][1])  # последняя колонка
    except Exception as e:
        logging.warning("kp fetch: %s",e); return None

def fetch_schumann() -> Tuple[float|None,float|None]|str:
    urls=[
        "https://schumann-resonances.s3.amazonaws.com/latest.csv",
        "https://gci.mixonic.com/SR_latest.csv",
    ]
    for u in urls:
        try:
            txt=requests.get(u,timeout=10).text.strip().splitlines()
            head,*rows=txt
            f,a=map(float, rows[-1].split(",")[1:3]); return f,a
        except Exception:
            continue
    return random.choice(SCHUMANN_JOKES)

def fetch_pollen() -> Dict[str,str]:
    key=os.getenv("TOMORROW_KEY")
    if not key: return {}
    url="https://api.tomorrow.io/v4/weather/forecast"
    params=dict(location="34.707,33.022", apikey=key, timesteps="1d",
                fields="pollenTreeIndex,pollenGrassIndex,pollenWeedIndex")
    try:
        js=requests.get(url,params=params,timeout=10).json()
        d=js["timelines"]["daily"][0]["values"]
        scale=["🟢 низкий","🟡 умеренный","🟠 повышенный","🔴 высокий","🟣 экстремальный"]
        lvl=lambda v: scale[min(int(v),4)]
        return {
            "Деревья": lvl(d["pollenTreeIndex"]),
            "Травы": lvl(d["pollenGrassIndex"]),
            "Сорняки": lvl(d["pollenWeedIndex"]),
        }
    except Exception as e:
        logging.warning("pollen: %s",e); return {}

# ░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░  ASTRO
def moon_phase() -> Tuple[int,str,float]:
    now=pendulum.now(TZ)
    age=((now-naive(now)).in_days())%29.53
    pct=int(age/29.53*100)
    sign= ["♈","♉","♊","♋","♌","♍","♎","♏","♐","♑","♒","♓"
          ][int(((now.datetime.timestamp())//(2.46e6))%12)]
    return pct,sign,age

def naive(dt:pendulum.DateTime)->pendulum.DateTime:
    return pendulum.datetime(dt.year,dt.month,dt.day)

def astro_events()->List[str]:
    pct,symbol,age=moon_phase()
    res=[f"Растущая Луна {symbol} ({pct} %)"]
    # простые хардкод-события
    res.append("Мини-парад планет")
    res.append("Eta Aquarids (метеоры)")
    return res

# ░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░ BUILD MESSAGE
def build_msg() -> str:
    om=fetch_openmeteo(*CITIES["Limassol"])
    daily=om["daily"]; cur=om["current_weather"]
    wcode=daily["weathercode"][1] if len(daily["weathercode"])>1 else cur["weathercode"]
    desc=WC.get(int(wcode),"переменная")

    press=daily.get("pressure_msl",["—","—"])[1]
    precip=daily.get("precipitation_probability_max",["—","—"])[1]
    rain_txt="осадки ≤{} %".format(precip) if isinstance(precip,(int,float)) else "не ожидаются"

    windspd=cur.get("windspeed"); winddir=deg2compass(cur.get("winddirection"))
    temps={}
    for city,(lat,lon) in CITIES.items():
        try:
            js=fetch_openmeteo(lat,lon); temps[city]=js["daily"]["temperature_2m_max"][1]
        except Exception: temps[city]=None
    warm=max((k for k,v in temps.items() if v), key=lambda k:temps[k])
    cold=min((k for k,v in temps.items() if v), key=lambda k:temps[k])

    aqi=fetch_airvisual(); aqi_val=int(aqi.get("current",{}).get("pollution",{}).get("aqius",64))
    aqi_line=f"AQI {aqi_color(aqi_val)} {aqi_val}"
    pm2=aqi.get("current",{}).get("pollution",{}).get("p2")
    pm10=aqi.get("current",{}).get("pollution",{}).get("p1")

    pollen=fetch_pollen()
    kp=fetch_kp()
    sch=fetch_schumann()
    pct,sign,age=moon_phase()
    culprit,emo=pick_culprit(press if isinstance(press,(int,float)) else None,kp,windspd,age)

    # ---- текстовые блоки -----------------------------------
    lines=[
        f"🌞 <b>Погода на завтра в Лимассоле {TOMORROW.format('DD.MM.YYYY')}</b>",
        f"<b>Темп. днём:</b> до {safe(daily['temperature_2m_max'][1])} °C",
        f"<b>Темп. ночью:</b> около {safe(daily['temperature_2m_min'][1])} °C",
        f"<b>Облачность:</b> {desc}",
        f"<b>Осадки:</b> {rain_txt}",
        f"<b>Ветер:</b> {safe(windspd)} км/ч, {winddir}",
        f"<b>Давление:</b> {safe(round(press,1) if isinstance(press,float) else press)} гПа",
        f"<i>Самый тёплый город:</i> {warm} ({temps[warm]:.1f} °C)",
        f"<i>Самый прохладный город:</i> {cold} ({temps[cold]:.1f} °C)",
        "———",
        f"🏭 <b>Качество воздуха</b>",
        f"{aqi_line} | PM2.5: {safe(pm2)} | PM10: {safe(pm10)}",
        "🌿 <b>Пыльца</b>",
        (" | ".join(f"{k}: {v}" for k,v in pollen.items()) if pollen else "источник недоступен"),
        "🧲 <b>Геомагнитная активность</b>",
        f"Kp {safe(kp)} {'' if kp is None else '(спокойно)' if kp<4 else '(буря)'}",
        "🎶 <b>Резонанс Шумана</b>",
        f"{sch if isinstance(sch,str) else f'{sch[0]:.1f} Гц · A={sch[1]:.1f}'}",
        "🌊 <b>Температура воды</b>",
        f"Сейчас: {safe(cur.get('temperature'))} °C",
        "🔮 <b>Астрологические события</b>",
        " | ".join(astro_events()),
        "———",
        "📝 <b>Вывод</b>",
        f"Если завтра что-то пойдёт не так — виновник: <i>{culprit}</i>! {emo}",
        "———",
        "✅ <b>Рекомендации</b>",
        "• 💧 Пейте воду — помогает при низком давлении" if culprit=="низкое давление" else "",
        "• 🛌 Высыпайтесь — магнитные колебания снижают тонус" if culprit=="магнитная буря" else "",
        "• 💨 Захватите шарф — ветер может усиливаться" if culp           lit=="шальной ветер" else "",
        "• ✨ Ночью взгляните на небо — метеоры Eta Aquarids!" ,
        "• 🌅 Рассвет завтра в {:02d}:{:02d}".format(*map(int,cur["time"].split("T")[1].split(":")[:2])),
        random.choice(FACTS),
    ]
    # убираем пустые строки
    html="\n".join(l for l in lines if l)
    logging.info("Preview: %s", html.replace('\n',' | ')[:230])
    return html

# ░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░  MAIN
async def main():
    TOKEN=os.getenv("TELEGRAM_TOKEN"); CHAT=os.getenv("CHANNEL_ID")
    if not TOKEN or not CHAT:
        logging.error("Missing TELEGRAM_TOKEN / CHANNEL_ID"); return
    html=build_msg()
    await Bot(TOKEN).send_message(
        int(CHAT), html[:4096], parse_mode="HTML", disable_web_page_preview=True)

if __name__ == "__main__":
    import asyncio, warnings
    warnings.filterwarnings("ignore", category=FutureWarning)
    asyncio.run(main())
