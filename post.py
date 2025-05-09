#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
VayboМетр v5.0 • 11 May 2025
Полный вечерний дайджест «на завтра» для канала @vaybometer.
"""

import os, random, math, asyncio, csv, io, logging
from datetime import date, datetime, timedelta

import requests, pendulum
from dateutil import tz
from telegram import Bot

# ──── Константы ──────────────────────────────────────────────
ZONE       = "Asia/Nicosia"
TZ         = pendulum.timezone(ZONE)
LOC        = (34.707, 33.022)          # Лимассол
CITIES     = {
    "Лимассол": LOC,
    "Ларнака":  (34.916, 33.624),
    "Никосия":  (35.170, 33.360),
    "Пафос":    (34.776, 32.424),
}

OPEN_METEO = (
    "https://api.open-meteo.com/v1/forecast"
    "?latitude={lat}&longitude={lon}&timezone=auto"
    "&daily=temperature_2m_max,temperature_2m_min,weathercode,precipitation_probability_max"
    "&hourly=surface_pressure"
    "&current_weather=true&forecast_days=2"
)

IQAIR      = "https://api.airvisual.com/v2/nearest_city?lat={lat}&lon={lon}&key={key}"
TOMORROW   = "https://api.tomorrow.io/v4/pollen?location={lat},{lon}&apikey={key}&timesteps=1d"
KP_SRC     = "https://services.swpc.noaa.gov/json/planetary_k_index_1m.json"
SR_MAIN    = "https://schumann-resonances.s3.amazonaws.com/latest.csv"
SR_BACKUP  = "https://gufm.net/sr_latest.csv"     # запасной (fict.)

BOT_KEY    = os.getenv("TELEGRAM_TOKEN", "")
CHAT_ID    = os.getenv("CHANNEL_ID", "")

session = requests.Session()
session.headers["User-Agent"] = "VayboMeter/5.0"
TIMEOUT = 15
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

# ──── Справочники ────────────────────────────────────────────
WC = {0:"ясно",1:"преимущественно ясно",2:"частично облачно",3:"пасмурно",
      45:"туманно",48:"изморозь",51:"морось",61:"дождь",71:"снег"}
COMPASS = ["N","NNE","NE","ENE","E","ESE","SE","SSE",
           "S","SSW","SW","WSW","W","WNW","NW","NNW"]

# ──── Утилиты ────────────────────────────────────────────────
def safe(val, unit=""):            # красиво выводим None → «—»
    return f"{val}{unit}" if val not in (None,"", "None") else "—"

def deg2compass(deg):              # угол° → точка компаса
    return "—" if deg is None else COMPASS[int((deg/22.5)+.5)%16]

def get_json(url):
    try:
        r=session.get(url, timeout=TIMEOUT); r.raise_for_status(); return r.json()
    except Exception as e:
        logging.warning("%s -> %s", url.split('/')[2], e); return None

def get_csv(url):
    try:
        r=session.get(url, timeout=TIMEOUT); r.raise_for_status(); return list(csv.reader(io.StringIO(r.text)))
    except Exception as e:
        logging.warning("%s -> %s", url.split('/')[2], e); return None

# ──── Блоки данных ───────────────────────────────────────────
def weather(lat, lon):
    js = get_json(OPEN_METEO.format(lat=lat, lon=lon))
    if not js or "daily" not in js: return None
    daily = js["daily"]; cur = js.get("current_weather", {})
    tomorrow = (date.today()+timedelta(days=1)).isoformat()

    # давление (среднее за завтра) из hourly
    pres = None
    hr_t = js.get("hourly", {}).get("time", [])
    hr_p = js.get("hourly", {}).get("surface_pressure", [])
    if hr_t and hr_p:
        vals=[p for t,p in zip(hr_t,hr_p) if t.startswith(tomorrow)]
        pres=round(sum(vals)/len(vals),1) if vals else None
    if not pres: pres = cur.get("surface_pressure")

    return {
        "tmax": daily["temperature_2m_max"][1],
        "tmin": daily["temperature_2m_min"][1],
        "wcode": daily["weathercode"][1] or cur.get("weathercode"),
        "precip": daily["precipitation_probability_max"][1],
        "pressure": pres,
        "wind_spd": cur.get("windspeed"),
        "wind_dir": cur.get("winddirection")
    }

def airq():
    key=os.getenv("AIRVISUAL_KEY","")
    js=get_json(IQAIR.format(lat=LOC[0], lon=LOC[1], key=key))
    try:
        pol=js["data"]["current"]["pollution"]
        return {"aqi":pol["aqius"],"pm25":pol.get("pm25"),"pm10":pol.get("pm10")}
    except Exception: return {}

def pollen():
    key=os.getenv("TOMORROW_KEY","")
    if not key: return {}
    js=get_json(TOMORROW.format(lat=LOC[0], lon=LOC[1], key=key))
    try:
        v=js["data"]["timelines"][0]["intervals"][0]["values"]
        return {"tree":v["treeIndex"],"grass":v["grassIndex"],"weed":v["weedIndex"]}
    except Exception: return {}

def kp():
    js=get_json(KP_SRC)
    try: return round(float(js[-1]["kp_index"]),1)
    except Exception: return None

def schumann():
    rows=get_csv(SR_MAIN) or get_csv(SR_BACKUP)
    if not rows: return None
    try: f,a=map(float, rows[-1][1:3]); return f,a
    except Exception: return None

def moon_phase():
    now=pendulum.now(tz=TZ)
    syn=29.53058867
    days=(now - pendulum.datetime(2000,1,6,tz=TZ)).total_days() % syn
    pct=round(days/syn*100)
    sign = ["♈","♉","♊","♋","♌","♍","♎","♏","♐","♑","♒","♓"][int(days/(syn/12))]
    return pct, sign

def astro():
    pct, sign = moon_phase()
    return [f"Растущая Луна {sign} ({pct} %)",
            "Мини-парад планет",
            "Eta Aquarids (метеоры)"]

# ──── Сообщение ──────────────────────────────────────────────
def build_msg():
    w=weather(*LOC) or {}
    desc = WC.get(w.get("wcode"), "переменная")
    fog  = w.get("wcode") in (45,48)

    # сравнение городов
    temps={n:(weather(*xy) or {}).get("tmax") for n,xy in CITIES.items()}
    temps = {k:v for k,v in temps.items() if v}
    if temps:
        warm=max(temps, key=temps.get); cold=min(temps, key=temps.get)
        warm_txt=f"<i>Самый тёплый:</i> {warm} ({temps[warm]:.1f} °C)"
        cold_txt=f"<i>Самый прохладный:</i> {cold} ({temps[cold]:.1f} °C)"
    else:
        warm_txt=cold_txt="<i>Нет данных по городам</i>"

    air=airq(); pol=pollen(); kp_val=kp(); sr=schumann()
    pressure=w.get("pressure")

    # кто «виноват»
    culprit=("низкое давление"   if pressure and pressure<1005 else
             "магнитные бури"    if kp_val and kp_val>=4 else
             "туман"             if fog else
             "мини-парад планет")

    rec_bank={
        "низкое давление":[ "💧 Пейте воду", "🧘 Дыхательная гимнастика" ],
        "магнитные бури":[ "☕ Меньше кофеина", "😴 Ранний сон" ],
        "туман":[ "🔦 Фонарик в утренней прогулке" ],
        "мини-парад планет":[ "🔭 Посмотрите на небо", "📸 Поймайте Венеру" ]
    }
    recs=random.sample(rec_bank.get(culprit, [])+["🌞 Ловите солнце!"], k=3)

    # дата
    tomorrow=(date.today()+timedelta(days=1)).strftime("%d.%m.%Y")

    msg=[
        f"🌞 <b>Погода на завтра в Лимассоле {tomorrow}</b>",
        f"<b>Темп. днём:</b> до {safe(w.get('tmax'),' °C')}",
        f"<b>Темп. ночью:</b> около {safe(w.get('tmin'),' °C')}",
        f"<b>Облачность:</b> {desc}",
        f"<b>Осадки:</b> {'не ожидаются' if (w.get('precip') or 0)<20 else str(w.get('precip'))+' %'}",
        f"<b>Ветер:</b> {safe(w.get('wind_spd'),' км/ч')}, {deg2compass(w.get('wind_dir'))}",
        f"<b>Давление:</b> {safe(pressure,' гПа')}",
        "⚠️ Возможен густой туман утром." if fog else "",
        warm_txt, cold_txt,
        "———",
        "🏭 <b>Качество воздуха</b>",
        f"AQI {safe(air.get('aqi'))} | PM2.5 {safe(air.get('pm25'),' µg/m³')} | PM10 {safe(air.get('pm10'),' µg/m³')}",
        "🌿 <b>Пыльца</b>",
        ("Деревья {tree}, травы {grass}, сорняки {weed}".format(**pol) if pol else "источник недоступен"),
        "🧭 <b>Геомагнитная активность</b>\nKp "+safe(kp_val),
        "📡 <b>Резонанс Шумана</b>",
        f"{sr[0]:.1f} Гц (A={sr[1]:.1f})" if sr else "датчики молчат — ретрит 🌱",
        "🌊 <b>Температура воды</b>\nСейчас: 20.3 °C",  # static demo
        "🔮 <b>Астрологические события</b>",
        " | ".join(astro()),
        "———",
        "📝 <b>Вывод</b>",
        f"Если завтра что-то пойдёт не так — вините {culprit}! 😉",
        "———",
        "✅ <b>Рекомендации</b>",
        *[f"• {r}" for r in recs]
    ]
    return "\n".join(filter(None,msg))

# ──── Отправка ───────────────────────────────────────────────
async def main():
    html=build_msg()
    logging.info("Preview: %s…", html.replace('\n',' | ')[:200])
    if BOT_KEY and CHAT_ID:
        await Bot(BOT_KEY).send_message(CHAT_ID, html[:4096],
                                        parse_mode="HTML",
                                        disable_web_page_preview=True)

if __name__ == "__main__":
    asyncio.run(main())
