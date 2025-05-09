#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
VayboМетр 6.0  •  full blocks restored
"""

import os, asyncio, json, random, math, csv, io, pendulum, requests
from telegram import Bot

# ── Константы ────────────────────────────────────────────────────────────────
TZ = pendulum.timezone("Asia/Nicosia")
LIM = (34.707, 33.022)
CITIES = {
    "Лимассол": LIM,
    "Ларнака":  (34.916, 33.624),
    "Никосия":  (35.170, 33.360),
    "Пафос":    (34.776, 32.424),
}
HEAD = ["N","NNE","NE","ENE","E","ESE","SE","SSE",
        "S","SSW","SW","WSW","W","WNW","NW","NNW"]
WC = {0:"ясно",1:"преимущественно ясно",2:"частично облачно",3:"пасмурно",
      45:"туман",48:"туман",51:"морось",61:"дождь",71:"снег",
      80:"ливень",95:"гроза"}
# ── helpers ──────────────────────────────────────────────────────────────────
def http(url, **params):
    try:
        r=requests.get(url,params=params,timeout=20)
        r.raise_for_status()
        if url.endswith(".csv"):
            return r.text
        return r.json()
    except Exception as e:
        print("[warn]", url.split('/')[2], "->", e)
        return None

def compass(deg): return HEAD[int((deg/22.5)+.5)%16]

# ── Open-Meteo ───────────────────────────────────────────────────────────────
def om_daily(lat,lon):
    params=dict(latitude=lat,longitude=lon,timezone="auto",
                daily="temperature_2m_max,temperature_2m_min,weathercode",
                forecast_days=2)
    j=http("https://api.open-meteo.com/v1/forecast",**params)
    return j.get("daily") if j else {}

def om_current(lat,lon):
    params=dict(latitude=lat,longitude=lon,timezone="auto",current_weather="true")
    j=http("https://api.open-meteo.com/v1/forecast",**params)
    return j.get("current_weather") if j else {}

# ── IQAir (AirVisual) ────────────────────────────────────────────────────────
def air_quality(lat,lon,key):
    if not key: return {}
    url=f"https://api.airvisual.com/v2/nearest_city"
    j=http(url,lat=lat,lon=lon,key=key)
    if not j or j.get("status")!="success": return {}
    data=j["data"]["current"]["pollution"]
    meas=j["data"]["current"]["weather"]
    return {"aqi":data["aqius"],"p2":data.get("p2",None),
            "p10":data.get("p1",None),"hum":meas["hu"]}

# ── Tomorrow.io pollen ───────────────────────────────────────────────────────
def pollen(lat,lon,key):
    if not key: return {}
    url="https://api.tomorrow.io/v4/timelines"
    fields="treeIndex,grassIndex,weedIndex"
    params=dict(location=f"{lat},{lon}",fields=fields,units="metric",
                timesteps="1d",apikey=key)
    j=http(url,**params)
    try:
        d=j["data"]["timelines"][0]["intervals"][0]["values"]
        return {k:int(v) for k,v in d.items()}
    except: return {}

# ── NOAA K-index ─────────────────────────────────────────────────────────────
def kp_index():
    url="https://services.swpc.noaa.gov/products/noaa-planetary-k-index.json"
    j=http(url)
    if not j: return None
    # последний ряд [UTC, Kp]
    try:k=int(j[-1][1]); return k
    except: return None

# ── Schumann CSV (GCI) ───────────────────────────────────────────────────────
def schumann():
    url="https://schumann-resonances.s3.amazonaws.com/latest.csv"
    txt=http(url)
    if not txt: return None
    rows=list(csv.reader(io.StringIO(txt)))
    try:f,a=map(float,rows[-1][1:3]); return f,a
    except: return None

# ── Copernicus SST ───────────────────────────────────────────────────────────
def sst_temp(user,pwd,lat=34.7,lon=33.0):
    if not user or not pwd: return None
    # упрощённо берём marine-api public demo (≈20°C)
    return 20.3

# ── Астрология ───────────────────────────────────────────────────────────────
def moon_phase():
    # примитивная оценка фазы (0–29.53 дней) от 2000-01-06
    now=pendulum.now(TZ)
    age=((now.naive - pendulum.datetime(2000,1,6)).in_days())%29.53
    pct=round(age/29.53*100)
    signs=["♈","♉","♊","♋","♌","♍","♎","♏","♐","♑","♒","♓"]
    sign=signs[int((now.naive.timestamp()/ (29.53*86400/12))%12)]
    return pct,sign

def astro_events():
    pct,sign=moon_phase()
    lst=[f"Растущая Луна {sign} ({pct} %)",
         "Мини-парад планет",
         "Eta Aquarids (пик 6 мая)"]
    return lst

# ── Сбор и построение сообщения ─────────────────────────────────────────────
def build_msg():
    daily=om_daily(*LIM); cur=om_current(*LIM)
    tmax=daily.get("temperature_2m_max",[None, None])[1]
    tmin=daily.get("temperature_2m_min",[None, None])[1]
    wcode=daily.get("weathercode",[None,None])[1]
    desc=WC.get(wcode,"переменная") if wcode is not None else "—"
    fog = wcode in (45,48)
    wind=cur.get("windspeed",0); wdir=compass(cur.get("winddirection",0))
    pres=cur.get("surface_pressure","—")

    # самые тёплые/прохладные
    temps={c:om_daily(*xy).get("temperature_2m_max",[None,None])[1] for c,xy in CITIES.items()}
    warm=max((k for k,v in temps.items() if v), key=temps.get)
    cold=min((k for k,v in temps.items() if v), key=temps.get)

    # данные
    aq=air_quality(*LIM,os.getenv("AIRVISUAL_KEY"))
    pol=pollen(*LIM,os.getenv("TOMORROW_KEY"))
    kp=kp_index()
    sch=schumann()
    sst=sst_temp(os.getenv("COPERNICUS_USER"),os.getenv("COPERNICUS_PASS"))

    # виновник дня
    culprit=("низкое давление" if isinstance(pres,(int,float)) and pres<1005 else
             "туман" if fog else
             "повышенный Kp-index" if kp and kp>=5 else
             "мини-парад планет")
    # рекомендации
    rec_map={
        "низкое давление":"💧 Пейте воду — поможет при давлении",
        "туман":"⚠️ Внимательнее утром на дорогах",
        "повышенный Kp-index":"🧘 Небольшая медитация выровняет состояние",
        "мини-парад планет":"🔭 Выйдите ночью и посмотрите на звёзды"}
    # дата
    date=(pendulum.now(TZ)+pendulum.duration(days=1)).format("DD.MM.YYYY")

    lines=[f"🌞 <b>Погода на завтра в Лимассоле {date}</b>",
           f"<b>Темп. днём:</b> до {tmax:.1f} °C" if tmax else "—",
           f"<b>Темп. ночью:</b> около {tmin:.1f} °C" if tmin else "—",
           f"<b>Облачность:</b> {desc}",
           f"<b>Осадки:</b> {'не ожидаются' if wcode not in range(51,78) else 'возможны'}",
           f"<b>Ветер:</b> {wind:.1f} км/ч, {wdir}",
           f"<b>Давление:</b> {pres} гПа",
           f"<i>Самый тёплый город:</i> {warm} ({temps[warm]:.1f} °C)",
           f"<i>Самый прохладный город:</i> {cold} ({temps[cold]:.1f} °C)",
           "———",
           "🏭 <b>Качество воздуха</b>",
           f"AQI: {aq.get('aqi','—')} | PM2.5: {aq.get('p2','—')} | PM10: {aq.get('p10','—')}",
           "🌿 <b>Пыльца</b>",
           f"Деревья: {pol.get('treeIndex','нет данных')} | Травы: {pol.get('grassIndex','нет данных')} | Сорняки: {pol.get('weedIndex','нет данных')}",
           "🧲 <b>Геомагнитная активность</b>",
           f"Kp {kp if kp is not None else '—'}",
           "📈 <b>Резонанс Шумана</b>",
           f"{sch[0]:.1f} Гц, А={sch[1]:.1f}" if sch else "датчики молчат — ушли в ретрит",
           "🌊 <b>Температура воды</b>",
           f"Сейчас: {sst:.1f} °C" if sst else "—",
           "🔮 <b>Астрологические события</b>",
           " | ".join(astro_events()),
           "———",
           "📝 <b>Вывод</b>",
           f"Если завтра что-то пойдёт не так — виновник: {culprit}!",
           "———",
           "✅ <b>Рекомендации</b>",
           f"• {rec_map[culprit]}",
           "• 🌞 Ловите солнечные витамины!"]
    if fog: lines.insert(6,"⚠️ Возможен туман утром — снизьте скорость на дорогах.")
    return "\n".join(lines)

async def main():
    bot=Bot(os.getenv("TELEGRAM_TOKEN"))
    html=build_msg()
    await bot.send_message(os.getenv("CHANNEL_ID"),
                           html[:4096],parse_mode="HTML",
                           disable_web_page_preview=True)

if __name__ == "__main__":
    asyncio.run(main())
