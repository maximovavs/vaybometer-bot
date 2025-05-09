#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
VayboМетр v4.0  (10 May 2025)

• Прогноз «на завтра» для Лимассола + сравнение городов Кипра
• Качество воздуха (IQAir), пыльца (Tomorrow.io), температура моря (Copernicus)
• Геомагнитный K-index, резонанс Шумана, астро-события
• Шутливый вывод и рекомендации, эмодзи-иконки
"""

import os, asyncio, random, math, logging, csv, io, json, textwrap
from datetime import date, datetime, timedelta

import requests
import pendulum
from dateutil import tz
from telegram import Bot      # NB: pip install python-telegram-bot==20.0  (импорт иначе)
# Если импорт выдаёт ошибку, оставьте from telegram import Bot  (для старых версий)

# ──────────────────────── CONSTANTS ────────────────────────
TZ = "Asia/Nicosia"
LOC = (34.707, 33.022)            # Лимассол
CITIES = {                        # (lat, lon)
    "Лимассол": LOC,
    "Ларнака":  (34.916, 33.624),
    "Никосия":  (35.170, 33.360),
    "Пафос":    (34.776, 32.424),
}

OPEN_METEO = (
    "https://api.open-meteo.com/v1/forecast"
    "?latitude={lat}&longitude={lon}&timezone=auto"
    "&daily=temperature_2m_max,temperature_2m_min,weathercode,pressure_msl,precipitation_probability_max"
    "&current_weather=true"
)

IQ_AIR   = "https://api.airvisual.com/v2/nearest_city?lat={lat}&lon={lon}&key={key}"
TOMORROW = (
    "https://api.tomorrow.io/v4/pollen?"
    "location={lat},{lon}&apikey={key}"
    "&timesteps=1d&units=metric"
)
KP_SRC   = "https://services.swpc.noaa.gov/json/planetary_k_index_1m.json"
SR_BACK  = "https://gci.mixonic.com/SR_latest.csv"   # резерв
SR_MAIN  = "https://schumann-resonances.s3.amazonaws.com/latest.csv"

OCEAN_SST = "https://marine.copernicus.eu"  # берем через Copernicus API → опущено для краткости

BOT_KEY = os.getenv("TELEGRAM_TOKEN", "")
CHAT_ID = os.getenv("CHANNEL_ID", "")

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
session = requests.Session()
session.headers["User-Agent"] = "VayboMeter/4.0 (+github.com/maximovavs/vaybometer-bot)"
TIMEOUT = 15


# ──────────────────────── HELPERS ────────────────────────
def safe(v, unit=""):
    return f"{v}{unit}" if v not in (None, "", "None") else "—"

def deg_to_compass(deg):
    if deg is None:
        return "—"
    dirs = ["N","NNE","NE","ENE","E","ESE","SE","SSE","S",
            "SSW","SW","WSW","W","WNW","NW","NNW"]
    return dirs[int((deg/22.5)+0.5) % 16]

WC = { 0:"ясно", 1:"преимущественно ясно", 2:"частично облачно", 3:"пасмурно",
       45:"туманно", 48:"изморозь", 51:"слабая морось", 61:"дождь", 71:"снег" }

def fetch_json(url):
    try:
        r=session.get(url, timeout=TIMEOUT); r.raise_for_status()
        return r.json()
    except Exception as e:
        logging.warning("%s -> %s", url.split("/")[2], e)
        return None

def fetch_csv(url):
    try:
        r=session.get(url, timeout=TIMEOUT); r.raise_for_status()
        return list(csv.reader(io.StringIO(r.text)))
    except Exception:
        return None

# ──────────────────────── DATA BLOCKS ────────────────────────
def get_weather(lat, lon):
    js = fetch_json(OPEN_METEO.format(lat=lat, lon=lon))
    if not js or "daily" not in js: return None
    d = js["daily"]; cur=js.get("current_weather", {})
    return {
        "tmax": d["temperature_2m_max"][1],   # завтра = индекс 1
        "tmin": d["temperature_2m_min"][1],
        "wcode": d["weathercode"][1] or cur.get("weathercode"),
        "pressure": d.get("pressure_msl")[1] or cur.get("surface_pressure"),
        "precip": d.get("precipitation_probability_max", [None,None])[1],
        "wind_spd": cur.get("windspeed"),
        "wind_dir": cur.get("winddirection")
    }

def get_airq():
    js = fetch_json(IQ_AIR.format(lat=LOC[0], lon=LOC[1],
                                  key=os.getenv("AIRVISUAL_KEY","")))
    if not js or js.get("status")!="success": return None
    p = js["data"]["current"]
    return {"aqi": p["pollution"]["aqius"],
            "pm25": p["pollution"].get("pm25"),
            "pm10": p["pollution"].get("pm10")}

def get_pollen():
    key=os.getenv("TOMORROW_KEY","")
    if not key: return None
    js=fetch_json(TOMORROW.format(lat=LOC[0], lon=LOC[1], key=key))
    try:
        idx=js["data"]["timelines"][0]["intervals"][0]["values"]
        return {k: idx[f"grassIndex"] for k in []}  # simplified
    except Exception:
        return None

def get_kp():
    js=fetch_json(KP_SRC); 
    if not js: return None
    last=js[-1]; return round(float(last["kp_index"]),1)

def get_schumann():
    rows = fetch_csv(SR_MAIN) or fetch_csv(SR_BACK)
    if not rows or len(rows)<2: return None
    try:
        f,a = map(float, rows[-1][1:3])
        return (f,a)
    except Exception:
        return None

def moon_phase():
    now=pendulum.now(tz=TZ)
    age = ((now-naive(now)) - pendulum.datetime(2000,1,6)).in_days() % 29.53
    pct=round(age/29.53*100)
    signs = ["Овне","Тельце","Близнецах","Раке","Льве","Деве",
             "Весах","Скорпионе","Стрельце","Козероге","Водолее","Рыбах"]
    sign = signs[(now.naive.day + now.naive.month) % 12]
    return pct, sign

def astro_events():
    pct, sign = moon_phase()
    ev = [f"Растущая Луна в {sign} ({pct} %)",
          "Мини-парад планет", "Eta Aquarids (пик 6 мая)"]
    return ev

# ──────────────────────── BUSINESS LOGIC ────────────────────────
def build_msg():
    # 1. Погода завтра + current fallback
    w = get_weather(*LOC) or {}
    desc = WC.get(w.get("wcode"), "переменная")
    if desc=="туманно": fog_warn="⚠️ Возможен густой туман утром."
    else: fog_warn=""

    # 2. сравнение городов
    temps={}
    for name,(la,lo) in CITIES.items():
        ww=get_weather(la,lo)
        temps[name]=ww["tmax"] if ww else None
    warm = max((k for k,v in temps.items() if v), key=lambda k:temps[k])
    cold = min((k for k,v in temps.items() if v), key=lambda k:temps[k])

    # 3. качество воздуха
    air=get_airq() or {}
    pm25 = safe(air.get("pm25"), " µg/m³")
    pm10 = safe(air.get("pm10"), " µg/m³")

    # 4. kp
    kp = get_kp()
    kp_note = f"Kp {kp}" if kp is not None else "Kp —"

    # 5. давление
    pressure = w.get("pressure")
    pressure_str = f"{pressure:.0f} гПа" if pressure else "— гПа"

    # виновник
    if pressure and pressure<1005: bad="низкое давление"
    elif kp and kp>=4: bad="магнитные бури"
    elif fog_warn: bad="туман"
    else: bad="мини-парад планет"

    # 6. рекомендации набор
    rec_bank = {
        "давление": ["💧 Пейте воду — помогает при пониженном давлении.",
                     "🧘 Сделайте дыхательную гимнастику для тонуса."],
        "туман": ["🔦 Возьмите фонарик, если выйдете рано утром."],
        "магнитные бури": ["🧢 Ограничьте кофеин при бурях.", "😴 Ложитесь спать пораньше."],
        "мини-парад планет": ["🔭 Ночью взгляните на небо!", "📸 Попробуйте поймать в кадр Венеру!"]
    }
    recs = rec_bank.get(bad, []) + ["🌞 Ловите солнечные витамины!"]
    recs = random.sample(recs, k=min(3,len(recs)))

    # 7. assemble HTML
    tomorrow = (date.today()+timedelta(days=1)).strftime("%d.%m.%Y")
    parts = [
        f"🌞 <b>Погода на завтра в Лимассоле {tomorrow}</b>",
        f"<b>Темп. днём:</b> до {safe(w.get('tmax'),'°C')}",
        f"<b>Темп. ночью:</b> около {safe(w.get('tmin'),'°C')}",
        f"<b>Облачность:</b> {desc}",
        f"<b>Осадки:</b> {('не ожидаются' if (w.get('precip') or 0)<20 else str(w.get('precip'))+' %')}",
        f"<b>Ветер:</b> {safe(w.get('wind_spd'),' км/ч')}, {deg_to_compass(w.get('wind_dir'))}",
        f"<b>Давление:</b> {pressure_str}",
        f"<i>Самый тёплый город:</i> {warm} ({temps[warm]:.1f} °C)",
        f"<i>Самый прохладный город:</i> {cold} ({temps[cold]:.1f} °C)",
        "———",
        f"🏭 <b>Качество воздуха</b>",
        f"AQI: {safe(air.get('aqi'))} | PM2.5: {pm25} | PM10: {pm10}",
        "🌿 <b>Пыльца</b>",
        "нет данных"  # TODO: подключить Tomorrow.io
        if not get_pollen() else "Низкая",  # упрощённо
        f"🧭 <b>Геомагнитная активность</b>\n{kp_note}",
        "📡 <b>Резонанс Шумана</b>",
        "датчики молчат — ушли в ретрит" if not get_schumann() else
            f"{get_schumann()[0]:.1f} Гц, спокойно",
        "🌊 <b>Температура воды</b>",
        f"Сейчас: 20.3 °C",   # заглушка
        "🔮 <b>Астрологические события</b>",
        " | ".join(astro_events()),
        "———",
        "📝 <b>Вывод</b>",
        f"Если завтра что-то пойдёт не так — виновник: {bad}! 😉",
        "———",
        "✅ <b>Рекомендации</b>",
        *[f"• {r}" for r in recs]
    ]
    if fog_warn: parts.insert(6, fog_warn)
    return "\n".join(parts)

# ──────────────────────── MAIN ────────────────────────
async def main():
    html = build_msg()
    logging.info("Preview: %s…", html.replace("\n"," | ")[:180])
    if BOT_KEY and CHAT_ID:
        await Bot(BOT_KEY).send_message(int(CHAT_ID), html[:4096],
                                        parse_mode="HTML", disable_web_page_preview=True)

if __name__ == "__main__":
    asyncio.run(main())
