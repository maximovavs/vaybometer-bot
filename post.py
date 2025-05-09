#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
VayboМетр v4.1 • 11 May 2025
"""

import os, random, math, asyncio, logging, csv, io, textwrap
from datetime import date, timedelta

import requests
import pendulum
from dateutil import tz
from telegram import Bot

# ───── настройки ────────────────────────────────────────────
TZ_ZONE  = "Asia/Nicosia"
TZ       = pendulum.timezone(TZ_ZONE)
LOC      = (34.707, 33.022)       # Лимассол
CITIES   = {
    "Лимассол": LOC,
    "Ларнака":  (34.916, 33.624),
    "Никосия":  (35.170, 33.360),
    "Пафос":    (34.776, 32.424),
}

OPEN_METEO = (
    "https://api.open-meteo.com/v1/forecast"
    "?latitude={lat}&longitude={lon}&timezone=auto"
    "&daily=temperature_2m_max,temperature_2m_min,weathercode,"
    "pressure_msl,precipitation_probability_max"
    "&current_weather=true&forecast_days=2"
)
IQ_AIR     = "https://api.airvisual.com/v2/nearest_city?lat={lat}&lon={lon}&key={key}"
TOMORROW   = "https://api.tomorrow.io/v4/pollen?location={lat},{lon}&apikey={key}&timesteps=1d"
KP_SRC     = "https://services.swpc.noaa.gov/json/planetary_k_index_1m.json"
SR_MAIN    = "https://schumann-resonances.s3.amazonaws.com/latest.csv"
SR_BACKUP  = "https://gci.mixonic.com/SR_latest.csv"

BOT_KEY    = os.getenv("TELEGRAM_TOKEN", "")
CHAT_ID    = os.getenv("CHANNEL_ID", "")

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
REQ = requests.Session()
REQ.headers["User-Agent"] = "VayboMeter/4.1"
TIMEOUT = 15

WC = {0: "ясно", 1: "преимущественно ясно", 2: "частично облачно", 3: "пасмурно",
      45: "туманно", 48: "изморозь", 51: "морось", 61: "дождь", 71: "снег"}

COMPASS = ["N","NNE","NE","ENE","E","ESE","SE","SSE",
           "S","SSW","SW","WSW","W","WNW","NW","NNW"]

# ───── helpers ───────────────────────────────────────────────
def safe(v, unit=""): return f"{v}{unit}" if v not in (None, "", "None") else "—"
def deg_to_compass(d): return "—" if d is None else COMPASS[int((d/22.5)+.5)%16]

def fetch_json(url):
    try:
        r=REQ.get(url, timeout=TIMEOUT); r.raise_for_status(); return r.json()
    except Exception as e:
        logging.warning("%s -> %s", url.split('/')[2], e); return None

def fetch_csv(url):
    try:
        r=REQ.get(url, timeout=TIMEOUT); r.raise_for_status(); return list(csv.reader(io.StringIO(r.text)))
    except Exception as e:
        logging.warning("%s -> %s", url.split('/')[2], e); return None

# ───── data blocks ───────────────────────────────────────────
def get_weather(lat, lon):
    js = fetch_json(OPEN_METEO.format(lat=lat, lon=lon))
    if not js or "daily" not in js:
        return None
    d = js["daily"]; cur = js.get("current_weather", {})
    return {
        "tmax": d["temperature_2m_max"][1],
        "tmin": d["temperature_2m_min"][1],
        "wcode": d["weathercode"][1] or cur.get("weathercode"),
        "pressure": d.get("pressure_msl", [None, None])[1] or cur.get("surface_pressure"),
        "precip": d.get("precipitation_probability_max", [None, None])[1],
        "wind_spd": cur.get("windspeed"),
        "wind_dir": cur.get("winddirection")
    }

def air_quality():
    key = os.getenv("AIRVISUAL_KEY", "")
    js  = fetch_json(IQ_AIR.format(lat=LOC[0], lon=LOC[1], key=key))
    if not js or js.get("status")!="success": return {}
    pol = js["data"]["current"]["pollution"]
    return {"aqi": pol["aqius"], "pm25": pol.get("pm25"), "pm10": pol.get("pm10")}

def pollen():
    key = os.getenv("TOMORROW_KEY", "")
    if not key: return {}
    js  = fetch_json(TOMORROW.format(lat=LOC[0], lon=LOC[1], key=key))
    try:
        vals = js["data"]["timelines"][0]["intervals"][0]["values"]
        return { "tree": vals["treeIndex"], "grass": vals["grassIndex"], "weed": vals["weedIndex"] }
    except Exception: return {}

def kp_index():
    js = fetch_json(KP_SRC); 
    try: return round(float(js[-1]["kp_index"]),1)
    except Exception: return None

def schumann():
    rows = fetch_csv(SR_MAIN) or fetch_csv(SR_BACKUP)
    if not rows: return None
    try: f,a = map(float, rows[-1][1:3]); return f,a
    except Exception: return None

def moon_phase():
    now = pendulum.now(TZ).int_timestamp
    ref = pendulum.datetime(2000,1,6,tz="UTC").int_timestamp
    age = ((now-ref)/86400) % 29.53
    pct = round(age/29.53*100)
    sign = ["♈","♉","♊","♋","♌","♍","♎","♏","♐","♑","♒","♓"][int(age/(29.53/12))]
    return pct, sign

def astro_events():
    pct, sign = moon_phase()
    return [
        f"Растущая Луна {sign} ({pct} %)",
        "Мини-парад планет",
        "Eta Aquarids (пик 6 мая)"
    ]

# ───── message builder ───────────────────────────────────────
def build_msg():
    w = get_weather(*LOC) or {}
    desc = WC.get(w.get("wcode"), "переменная")
    fog  = w.get("wcode") in (45,48)
    fog_warn = "⚠️ Возможен густой туман утром." if fog else ""

    # Сравнение городов
    temps = {}
    for name,(la,lo) in CITIES.items():
        ww=get_weather(la,lo); temps[name]=ww["tmax"] if ww else None
    if temps and any(temps.values()):
        warm = max((k for k,v in temps.items() if v), key=temps.get)
        cold = min((k for k,v in temps.items() if v), key=temps.get)
        warm_txt = f"<i>Самый тёплый город:</i> {warm} ({temps[warm]:.1f} °C)"
        cold_txt = f"<i>Самый прохладный город:</i> {cold} ({temps[cold]:.1f} °C)"
    else:
        warm_txt = cold_txt = "<i>Нет данных по другим городам</i>"

    air = air_quality()
    pol = pollen()
    kp  = kp_index()
    sch = schumann()

    pressure = w.get("pressure")
    culprit = ("низкое давление"  if pressure and pressure<1005 else
               "магнитные бури"   if kp and kp>=4 else
               "туман"            if fog else
               "мини-парад планет")

    rec_bank = {
        "низкое давление": ["💧 Пейте воду — помогает при пониженном давлении.",
                            "🧘 Лёгкая дыхательная гимнастика взбодрит."],
        "магнитные бури":  ["☕ Ограничьте кофеин в бурю.", "😴 Ложитесь спать пораньше."],
        "туман":           ["🔦 Захватите фонарик на раннюю прогулку."],
        "мини-парад планет": ["🔭 Ночью взгляните на небо!", "📸 Попробуйте поймать Венеру."]
    }
    recs = random.sample(rec_bank.get(culprit, []) + ["🌞 Ловите солнечный витамин!"], k=3)

    tomorrow = (date.today()+timedelta(days=1)).strftime("%d.%m.%Y")
    parts = [
        f"🌞 <b>Погода на завтра в Лимассоле {tomorrow}</b>",
        f"<b>Темп. днём:</b> до {safe(w.get('tmax'),' °C')}",
        f"<b>Темп. ночью:</b> около {safe(w.get('tmin'),' °C')}",
        f"<b>Облачность:</b> {desc}",
        f"<b>Осадки:</b> {'не ожидаются' if (w.get('precip') or 0)<20 else str(w.get('precip'))+' %'}",
        f"<b>Ветер:</b> {safe(w.get('wind_spd'),' км/ч')}, {deg_to_compass(w.get('wind_dir'))}",
        f"<b>Давление:</b> {safe(pressure,' гПа')}",
        fog_warn,
        warm_txt,
        cold_txt,
        "———",
        f"🏭 <b>Качество воздуха</b>",
        f"AQI: {safe(air.get('aqi'))} | PM2.5: {safe(air.get('pm25'),' µg/m³')} | PM10: {safe(air.get('pm10'),' µg/m³')}",
        "🌿 <b>Пыльца</b>",
        ("Деревья: {tree} | Травы: {grass} | Сорняки: {weed}".format(**pol)
         if pol else "источник недоступен"),
        f"🧭 <b>Геомагнитная активность</b>\nKp {safe(kp)}",
        "📡 <b>Резонанс Шумана</b>",
        (f"{sch[0]:.1f} Гц, A={sch[1]:.1f}" if sch else "датчики молчат — ушли в ретрит"),
        "🌊 <b>Температура воды</b>\nСейчас: 20.3 °C",  # place-holder
        "🔮 <b>Астрологические события</b>",
        " | ".join(astro_events()),
        "———",
        "📝 <b>Вывод</b>",
        f"Если завтра что-то пойдёт не так — виноват(а) {culprit}! 😉",
        "———",
        "✅ <b>Рекомендации</b>",
        *[f"• {r}" for r in recs]
    ]
    # фильтруем пустые строки
    return "\n".join(filter(None, parts))

# ───── main ────────────────────────────────────────────────
async def main():
    html = build_msg()
    logging.info("Preview: %s…", html.replace("\n"," | ")[:200])
    if BOT_KEY and CHAT_ID:
        await Bot(BOT_KEY).send_message(int(CHAT_ID), html[:4096],
                                        parse_mode="HTML", disable_web_page_preview=True)

if __name__ == "__main__":
    asyncio.run(main())
