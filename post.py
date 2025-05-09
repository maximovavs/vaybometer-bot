#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
VayboМетр 5.1.1 – fix Open-Meteo 400 (pressure_msl removed from daily)
"""

import os, random, asyncio, requests, pendulum
from telegram import Bot

TZ=pendulum.timezone("Asia/Nicosia")
LAT,LON=34.707,33.022
HEADINGS=["N","NNE","NE","ENE","E","ESE","SE","SSE",
          "S","SSW","SW","WSW","W","WNW","NW","NNW"]
WC={0:"ясно",1:"преимущественно ясно",2:"частично облачно",3:"пасмурно",
    45:"туман",48:"туман, изморось",51:"морось",61:"дождь",71:"снег",80:"ливень",95:"гроза"}
CITIES={"Лимассол":(34.707,33.022),"Ларнака":(34.916,33.624),
        "Никосия":(35.17,33.36),"Пафос":(34.776,32.424)}
def deg2compass(d):return HEADINGS[int((d/22.5)+.5)%16]
def http(url,**kw):
    try:r=requests.get(url,timeout=20,**kw);r.raise_for_status();return r.json()
    except Exception as e:print("[warn]",url.split('/')[2],"->",e);return {}

def om_daily(lat,lon):
    p=dict(latitude=lat,longitude=lon,timezone="auto",
           daily="temperature_2m_max,temperature_2m_min,weathercode",forecast_days=2)
    return http("https://api.open-meteo.com/v1/forecast",params=p).get("daily",{})

def om_current(lat,lon):
    p=dict(latitude=lat,longitude=lon,timezone="auto",current_weather="true")
    return http("https://api.open-meteo.com/v1/forecast",params=p).get("current_weather",{})

def build_msg():
    d=om_daily(LAT,LON); cur=om_current(LAT,LON)
    tmax=d.get("temperature_2m_max",[cur.get("temperature")])[1] if d else cur.get("temperature")
    tmin=d.get("temperature_2m_min",[cur.get("temperature")])[1] if d else cur.get("temperature")
    wcode=d.get("weathercode",[cur.get("weathercode")])[1] if d else cur.get("weathercode")
    desc=WC.get(wcode,"переменная"); fog=wcode in (45,48)
    wind=cur.get("windspeed",0); wdir=deg2compass(cur.get("winddirection",0))
    pressure=cur.get("surface_pressure","—")

    temps={c:om_daily(*xy).get("temperature_2m_max",[None, None])[1] for c,xy in CITIES.items()}
    warm=max((k for k,v in temps.items() if v),key=temps.get); cold=min((k for k,v in temps.items() if v),key=temps.get)

    culprit=("низкое давление" if isinstance(pressure,(int,float)) and pressure<1005 else
             "туман" if fog else "ветер" if wind>25 else "погоду")

    rec={"низкое давление":"💧 Вода + паузы помогут пережить давление",
         "туман":"⚠️ Внимательнее на дорогах в утренний туман",
         "ветер":"🧣 Лёгкий шарф спасёт от сквозняка",
         "погоду":"🙂 Наслаждайтесь днём"}[culprit]

    date=(pendulum.now(TZ)+pendulum.duration(days=1)).format("DD.MM.YYYY")
    lines=[
        f"🌞 <b>Погода на завтра в Лимассоле {date}</b>",
        f"<b>Темп. днём:</b> до {tmax:.1f} °C",
        f"<b>Темп. ночью:</b> около {tmin:.1f} °C",
        f"<b>Облачность:</b> {desc}",
        f"<b>Осадки:</b> {'не ожидаются' if wcode not in range(51,78) else 'возможны'}",
        f"<b>Ветер:</b> {wind:.1f} км/ч, {wdir}",
        f"<b>Давление:</b> {pressure} гПа",
        f"<i>Самый тёплый город:</i> {warm} ({temps[warm]:.1f} °C)",
        f"<i>Самый прохладный город:</i> {cold} ({temps[cold]:.1f} °C)",
        "———",
        "📝 <b>Вывод</b>",
        f"Если завтра что-то пойдёт не так — вините {culprit}! 😉",
        "———",
        "✅ <b>Рекомендации</b>",
        f"• {rec}",
        "• 🌞 Ловите солнечные витамины!"
    ]
    if fog:
        lines.insert(6,"⚠️ Утром возможен густой туман — внимательнее на дорогах.")
    return "\n".join(lines)

async def main():
    html=build_msg()
    await Bot(os.getenv("TELEGRAM_TOKEN")).send_message(
        os.getenv("CHANNEL_ID"),html[:4096],parse_mode="HTML",
        disable_web_page_preview=True)

if __name__=="__main__":
    asyncio.run(main())
