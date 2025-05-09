#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
VayboМетр 5.0  – вечерний дайджест «на завтра»
"""

import os, random, asyncio, json, math, datetime as dt
import requests, pendulum
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
# weathercode → описание/облачность
WC = {0:"ясно",1:"преимущественно ясно",2:"частично облачно",3:"пасмурно",
      45:"туман",48:"туман, изморось",51:"морось",53:"морось",55:"морось сильная",
      61:"дождь легкий",63:"дождь",65:"дождь сильный",
      71:"снег",80:"ливень",95:"гроза"}
# ────── Утилиты ──────────────────────────────────────────────────────────────
def deg2compass(deg:float)->str:
    idx = int((deg/22.5)+.5)%16
    return HEADINGS[idx]

def fetch_open_meteo(lat, lon):
    url = "https://api.open-meteo.com/v1/forecast"
    params = dict(latitude=lat, longitude=lon, timezone="auto",
                  daily="temperature_2m_max,temperature_2m_min,weathercode,pressure_msl",
                  current_weather=True, forecast_days=2)
    r=requests.get(url, params=params, timeout=20)
    r.raise_for_status()
    return r.json()

def fetch_airvisual(lat, lon):
    key=os.getenv("AIRVISUAL_KEY")
    if not key: return {}
    url="https://api.airvisual.com/v2/nearest_city"
    r=requests.get(url, params=dict(lat=lat, lon=lon, key=key), timeout=15)
    if r.status_code!=200: return {}
    data=r.json().get("data",{})
    return {"aqi":data.get("current",{}).get("pollution",{}).get("aqius"),
            "pm25":data.get("current",{}).get("pollution",{}).get("p2"),
            "pm10":data.get("current",{}).get("pollution",{}).get("p1")}

def fetch_kp():
    try:
        url="https://services.swpc.noaa.gov/json/planetary_k_index_1m.json"
        rows=requests.get(url, timeout=15).json()
        return rows[-1]['kp_index']
    except Exception:
        return None

def fetch_schumann():
    try:
        csv=requests.get("https://data.gci.org/files/GGIRAW.csv", timeout=15).text
        *_, last=csv.strip().splitlines()
        _, f, a, *_ = last.split(",")
        return float(f), float(a)
    except Exception:
        return None, None

def fetch_pollen():
    key=os.getenv("AMBEE_KEY"); 
    if not key: return None
    try:
        url="https://api.ambeedata.com/latest/pollen/by-place"
        r=requests.get(url, params=dict(place="Limassol"), headers={"x-api-key":key}, timeout=15)
        data=r.json()['data'][0]['Count']
        return {k.lower():v for k,v in data.items()}
    except Exception:
        return None

def moon_phase():
    # простая аппроксимация
    now=pendulum.now(TZ)
    new_moon=pendulum.datetime(2000,1,6, tz=TZ)
    days=(now-new_moon).total_days()%29.530588
    pct=round(days/29.5306*100)
    signs=["♈","♉","♊","♋","♌","♍","♎","♏","♐","♑","♒","♓"]
    sign=signs[int(((now.int_timestamp/86400)%27.3)//2.275)]
    return pct, sign

def astro_events():
    pct, sign = moon_phase()
    events=[f"Растущая Луна {sign} ({pct} %)", "Мини-парад планет", "Eta Aquarids (пик 6 мая)"]
    return events

def choose_scapegoat(p, kp, wind, fog):
    if p and p<1005: return "низкое давление"
    if kp and kp>=4: return "геомагнитную бурю"
    if fog:         return "туманное утро"
    if wind>25:     return "шквалистый ветер"
    return "ретроградный Меркурий"

def recommend(factor):
    pool={
        "низкое давление":[ "💧 Пейте воду — помогает сосудам",
                            "🧘 Короткая дыхательная практика снимет тяжесть" ],
        "геомагнитную бурю":[ "🧢 Откажитесь от тяжёлых тренировок",
                              "🌿 Травяной чай с мелиссой успокоит" ],
        "шквалистый ветер":[ "🧣 Возьмите шарф — берегите горло!",
                             "🌬️ Проверьте окна перед сном" ],
        "туманное утро":[ "⚠️ На дороге туман — будьте внимательны",
                          "🌫️ Прогулку лучше отложить до обеда" ],
        "ретроградный Меркурий":[ "🔄 Перепроверьте планы — Меркурий шалит!",
                                  "✉️ Отложите важные письма до завтра" ],
    }
    return random.choice(pool[factor])

# ────── Сообщение ────────────────────────────────────────────────────────────
def build_msg():
    # Open-Meteo: основные данные
    j=fetch_open_meteo(LAT_LIM,LON_LIM)
    d=j['daily']
    t_max=d['temperature_2m_max'][1]; t_min=d['temperature_2m_min'][1]
    wc_tom=d['weathercode'][1];        wc_now=j['current_weather']['weathercode']
    pressure=d['pressure_msl'][1] or None
    windspeed=j['current_weather']['windspeed']; winddir=deg2compass(j['current_weather']['winddirection'])
    desc=WC.get(wc_tom, WC.get(wc_now,"переменная"))
    fog_alert = wc_tom in (45,48)

    # Тёплый/холодный города
    temps={}
    for city,(lat,lon) in CITIES.items():
        try:
            jj=fetch_open_meteo(lat,lon)
            temps[city]=jj['daily']['temperature_2m_max'][1]
        except Exception: temps[city]=None
    warm=max((c for c,v in temps.items() if v), key=lambda c:temps[c])
    cold=min((c for c,v in temps.items() if v), key=lambda c:temps[c])

    # AQI & PM
    air=fetch_airvisual(LAT_LIM,LON_LIM)
    aqi  = air.get("aqi","—")
    pm25 = air.get("pm25","—")
    pm10 = air.get("pm10","—")

    # Pollen
    poll=fetch_pollen()
    pollen_str = " | ".join(f"{k.capitalize()}: {v}" for k,v in poll.items()) if poll else "нет данных"

    # K-index
    kp=fetch_kp() or "—"

    # Schumann
    f,a=fetch_schumann()
    if f: sch=f"Част. {f:.1f} Гц, амп. {a:.1f}"
    else: sch="датчики молчат — ушли в ретрит"

    # Sea temp (Copernicus тяжеловесный → использую open-meteo sst)
    try:
        sst=requests.get("https://marine-api.open-meteo.com/v1/gfs?latitude=34.7&longitude=33&hourly=sea_surface_temperature&forecast_days=1", timeout=15).json()['hourly']['sea_surface_temperature'][0]
    except Exception: sst="—"

    # Астрология
    astro=" | ".join(astro_events())

    # Scapegoat & рекомендация
    scapegoat=choose_scapegoat(pressure, kp if isinstance(kp,(int,float)) else None, windspeed, fog_alert)
    rec = recommend(scapegoat)

    dt_tom=(pendulum.now(TZ)+pendulum.duration(days=1)).format("DD.MM.YYYY")

    msg="\n".join([
        f"🌞 <b>Погода на завтра в Лимассоле {dt_tom}</b>",
        f"<b>Темп. днём:</b> до {t_max:.1f} °C",
        f"<b>Темп. ночью:</b> около {t_min:.1f} °C",
        f"<b>Облачность:</b> {desc}",
        f"<b>Осадки:</b> {'не ожидаются' if wc_tom not in (51,53,55,61,63,65,80,95) else 'возможны'}",
        f"<b>Ветер:</b> {windspeed:.1f} км/ч, {winddir}",
        f"<b>Давление:</b> {pressure or '—'} гПа",
        f"<i>Самый тёплый город:</i> {warm} ({temps[warm]:.1f} °C)",
        f"<i>Самый прохладный город:</i> {cold} ({temps[cold]:.1f} °C)",
        "———",
        f"🏴‍☠️ <b>Качество воздуха</b>",
        f"AQI: {aqi} | PM2.5: {pm25} | PM10: {pm10}",
        f"🌿 <b>Пыльца</b>\n{pollen_str}",
        f"🛰️ <b>Геомагнитная активность</b>\nKp {kp}",
        f"📈 <b>Резонанс Шумана</b>\n{sch}",
        f"🌊 <b>Температура воды</b>\nСейчас: {sst} °C",
        f"🔮 <b>Астрологические события</b>\n{astro}",
        "———",
        "📝 <b>Вывод</b>",
        f"Если завтра что-то пойдёт не так — вините {scapegoat}! 😉",
        "———",
        "✅ <b>Рекомендации</b>",
        f"• {rec}",
        "• 🌞 Зарядитесь солнечным настроением!",
        ])
    if fog_alert:
        msg=msg.replace("———","⚠️ <b>Туман утром</b> — будьте внимательны!\n———",1)
    return msg

# ────── Telegram ─────────────────────────────────────────────────────────────
async def main():
    html=build_msg()
    bot=Bot(os.getenv("TELEGRAM_TOKEN"))
    await bot.send_message(chat_id=os.getenv("CHANNEL_ID"), text=html[:4096], parse_mode="HTML",
                           disable_web_page_preview=True)

if __name__=="__main__":
    asyncio.run(main())
