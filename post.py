#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
VayboМетр 5.1  – вечерний дайджест «на завтра»
* Pollen-block now comes from Tomorrow.io (pollenGrassRisk / pollenTreeRisk / pollenWeedRisk)
"""

import os, random, asyncio, json, math, datetime as dt, requests, pendulum
from telegram import Bot

# ────── Константы ────────────────────────────────────────────────────────────
TZ              = pendulum.timezone("Asia/Nicosia")
LAT_LIM, LON_LIM= 34.707, 33.022
CITIES          = {
    "Лимассол": (34.707, 33.022),
    "Ларнака" : (34.916, 33.624),
    "Никосия" : (35.170, 33.360),
    "Пафос"   : (34.776, 32.424),
}
HEADINGS        = ["N","NNE","NE","ENE","E","ESE","SE","SSE",
                   "S","SSW","SW","WSW","W","WNW","NW","NNW"]
WC = {0:"ясно",1:"преимущественно ясно",2:"частично облачно",3:"пасмурно",
      45:"туман",48:"туман, изморось",51:"морось",61:"дождь",71:"снег",80:"ливень",95:"гроза"}

# ────── Служебные функции ────────────────────────────────────────────────────
def deg2compass(deg:float)->str: return HEADINGS[int((deg/22.5)+.5)%16]

def http(url, **kw):
    try:
        r=requests.get(url, timeout=kw.pop("timeout",20), **kw)
        r.raise_for_status(); return r.json()
    except Exception as e:
        print("[warn]", url.split("/")[2], "->", e); return {}

# ────── Источники данных ─────────────────────────────────────────────────────
def fetch_open_meteo(lat,lon):
    return http("https://api.open-meteo.com/v1/forecast", params=dict(
        latitude=lat, longitude=lon, timezone="auto",
        daily="temperature_2m_max,temperature_2m_min,weathercode,pressure_msl",
        current_weather=True, forecast_days=2))

def fetch_airvisual(lat,lon):
    k=os.getenv("AIRVISUAL_KEY"); 
    if not k: return {}
    return http("https://api.airvisual.com/v2/nearest_city",
           params=dict(lat=lat,lon=lon,key=k))

def fetch_kp():
    j=http("https://services.swpc.noaa.gov/json/planetary_k_index_1m.json")
    try: return j[-1]["kp_index"]
    except Exception: return None

def fetch_schumann():
    try:
        txt=requests.get("https://data.gci.org/files/GGIRAW.csv",timeout=15).text
        *_,last=txt.strip().splitlines(); _,f,a,*_=last.split(",")
        return float(f),float(a)
    except Exception: return (None,None)

# --- НОВОЕ: пыльца от Tomorrow.io
def fetch_pollen(lat=LAT_LIM,lon=LON_LIM):
    key=os.getenv("TOMORROW_KEY")
    if not key: return None
    params=dict(location=f"{lat},{lon}",
                apikey=key,
                fields="pollenGrassRisk,pollenTreeRisk,pollenWeedRisk",
                timesteps="1d", units="metric")
    j=http("https://api.tomorrow.io/v4/weather/forecast", params=params)
    try:
        vals=j["timelines"]["daily"][0]["values"]
        risk={0:"Низкий 🌿",1:"Низкий 🌿",
              2:"Средний 🌱",3:"Средний 🌱",
              4:"Высокий 🌾",5:"Очень высокий 🌾"}
        return { "Травы": risk.get(vals["pollenGrassRisk"],"—"),
                 "Деревья": risk.get(vals["pollenTreeRisk"],"—"),
                 "Сорняки": risk.get(vals["pollenWeedRisk"],"—") }
    except Exception:
        return None

def moon_phase():
    new=pendulum.datetime(2000,1,6,tz=TZ)
    age=(pendulum.now(TZ)-new).total_days()%29.5306
    pct=round(age/29.5306*100)
    signs="♈♉♊♋♌♍♎♏♐♑♒♓"
    sign=signs[int(((pendulum.now(TZ).int_timestamp/86400)%27.3)//2.275)]
    return pct,sign

# ────── Формируем сообщение ──────────────────────────────────────────────────
def build_msg():
    om=fetch_open_meteo(LAT_LIM,LON_LIM)
    d=om["daily"]; cur=om["current_weather"]
    tmax,tmin=d["temperature_2m_max"][1],d["temperature_2m_min"][1]
    wc=d["weathercode"][1]; desc=WC.get(wc,"переменная")
    wind=cur["windspeed"]; winddir=deg2compass(cur["winddirection"])
    press=d["pressure_msl"][1] or None
    fog=wc in (45,48)

    temps={city:fetch_open_meteo(*coords)["daily"]["temperature_2m_max"][1]
           for city,coords in CITIES.items()}
    warm=max(temps,key=temps.get); cold=min(temps,key=temps.get)

    air=fetch_airvisual(LAT_LIM,LON_LIM).get("data",{})
    pol=fetch_pollen()
    kp=fetch_kp(); fsch,asch=fetch_schumann()
    sst=http("https://marine-api.open-meteo.com/v1/gfs",
             params=dict(latitude=34.7,longitude=33,
                         hourly="sea_surface_temperature",forecast_days=1)
             ).get("hourly",{}).get("sea_surface_temperature",[None])[0]

    pct,sign=moon_phase()
    astro=f"Растущая Луна {sign} ({pct} %) | Мини-парад планет | Eta Aquarids"

    culprit = ("низкое давление" if press and press<1005 else
               "туман" if fog else
               "магнитную бурю" if kp and kp>=4 else
               "ветер" if wind>25 else
               "ретроградный Меркурий")
    rec={"низкое давление":"💧 Пейте воду и делайте паузы",
         "магнитную бурю":"🧘 По возможности избегайте стресса",
         "ветер":"🧣 Захватите лёгкий шарф",
         "туман":"⚠️ Будьте внимательны на дорогах",
         "ретроградный Меркурий":"🔄 Перепроверьте планы"}[culprit]

    tomorrow=(pendulum.now(TZ)+pendulum.duration(days=1)).format("DD.MM.YYYY")
    lines=[
        f"🌞 <b>Погода на завтра в Лимассоле {tomorrow}</b>",
        f"<b>Темп. днём:</b> до {tmax:.1f} °C",
        f"<b>Темп. ночью:</b> около {tmin:.1f} °C",
        f"<b>Облачность:</b> {desc}",
        f"<b>Осадки:</b> {'не ожидаются' if wc not in range(51,78) else 'возможны'}",
        f"<b>Ветер:</b> {wind:.1f} км/ч, {winddir}",
        f"<b>Давление:</b> {press or '—'} гПа",
        f"<i>Самый тёплый город:</i> {warm} ({temps[warm]:.1f} °C)",
        f"<i>Самый прохладный город:</i> {cold} ({temps[cold]:.1f} °C)",
        "———",
        "🌬️ <b>Качество воздуха</b>",
        f"AQI {air.get('current',{}).get('pollution',{}).get('aqius','—')} "
        f"| PM2.5 {air.get('current',{}).get('pollution',{}).get('p2','—')} "
        f"| PM10 {air.get('current',{}).get('pollution',{}).get('p1','—')}",
        "🌿 <b>Пыльца</b>",
        " | ".join(f"{k}: {v}" for k,v in pol.items()) if pol else "нет данных",
        "🌌 <b>Геомагнитная активность</b>",
        f"Kp {kp if kp is not None else '—'}",
        "📈 <b>Резонанс Шумана</b>",
        f"{fsch:.1f} Гц, А={asch:.1f}" if fsch else "датчики молчат — ретрит 🧘",
        "🌊 <b>Температура воды</b>",
        f"Сейчас: {sst if sst else '—'} °C",
        "🔮 <b>Астрологические события</b>",
        astro,
        "———",
        "📝 <b>Вывод</b>",
        f"Если завтра что-то пойдёт не так — вините {culprit}! 😉",
        "———",
        "✅ <b>Рекомендации</b>",
        f"• {rec}",
        "• 🌞 Ловите солнечные витамины!",
    ]
    if fog:
        lines.insert(6,"⚠️ Утром возможен густой туман — снизьте скорость на дорогах.")
    return "\n".join(lines)

# ────── Telegram ─────────────────────────────────────────────────────────────
async def main():
    html=build_msg(); print("Preview:",html.replace('\n',' | ')[:230])
    await Bot(os.getenv("TELEGRAM_TOKEN")).send_message(
        os.getenv("CHANNEL_ID"), html[:4096],
        parse_mode="HTML", disable_web_page_preview=True)

if __name__=="__main__":
    asyncio.run(main())
