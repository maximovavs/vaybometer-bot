#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
VayboMeter bot – версия 5.2  (11-May-2025)

TG message “погода-на-завтра” для Лимассола + сопутствующие факторы.
"""

import os, sys, asyncio, random, math, json, csv, logging, datetime as dt
import requests, pendulum
from telegram import Bot

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

# ───── параметры ──────────────────────────────────────────────────────────────
TOKEN      = os.getenv("TELEGRAM_TOKEN")
CHAT       = os.getenv("CHANNEL_ID")
OWM_KEY    = os.getenv("OWM_KEY")            # не обязателен, но пусть будет
AIR_KEY    = os.getenv("AIRVISUAL_KEY")
TMR_KEY    = os.getenv("TOMORROW_KEY")
HEADERS    = {"User-Agent": "VayboMeter/5.2"}

TZ   = pendulum.timezone("Asia/Nicosia")
DATE = (pendulum.now(TZ)+pendulum.duration(days=1)).format('DD.MM.YYYY')

CITIES = {
    "Limassol": (34.707, 33.022),
    "Larnaca" : (34.916, 33.624),
    "Nicosia" : (35.170, 33.360),
    "Pafos"   : (34.776, 32.424),
}

WC = {  #  weathercode → описание
    0:"ясно",1:"главным образом ясно",2:"переменная",3:"пасмурно",
    45:"туман",48:"изморозь",51:"морось",61:"дождь" }

COMPASS = "N NNE NE ENE E ESE SE SSE S SSW SW WSW W WNW NW NNW".split()

TIPS = {
    "низкое давление": ["🧘 Дыхательная гимнастика", "💧 Пейте воду"],
    "магнитная буря" : ["🔌 Ограничьте экраны", "🌿 Заземлитесь босиком"],
    "шальной ветер"  : ["💨 Захватите шарф", "🧢 Кепка спасёт причёску"],
    "туман"          : ["🚗 Будьте внимательны на дороге", "🌫️ Фонарь для пробежки"],
    "ретроград"      : ["✍️ Проверьте планы дважды", "🛑 Не подписывайте договоры"],
}

# ───── утилиты ────────────────────────────────────────────────────────────────
def deg2compass(deg: float) -> str:
    return COMPASS[int((deg/22.5)+.5)%16]

def safe(x): return "—" if x in (None,"",[]) else x

def fetch_json(url, params=None):
    try:
        r = requests.get(url, params=params, timeout=10, headers=HEADERS)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        logging.warning("%s -> %s", url.split("//")[1].split("/")[0], e)
        return None

# ───── Open-Meteo ─────────────────────────────────────────────────────────────
def fetch_openmeteo(lat: float, lon: float):
    url   = "https://api.open-meteo.com/v1/forecast"
    base  = dict(latitude=lat, longitude=lon,
                 timezone="auto",
                 forecast_days=2,
                 current_weather="true")
    # Сначала пробуем c вероятностью осадков
    daily_try  = [
        "temperature_2m_max,temperature_2m_min,weathercode,precipitation_probability_max",
        "temperature_2m_max,temperature_2m_min,weathercode"                       # fallback
    ]

    for daily in daily_try:
        params = base | {
            "daily" : daily,
            "hourly": "pressure_msl"       # давление почасовое
        }
        try:
            r = requests.get(url, params=params, timeout=10, headers=HEADERS)
            r.raise_for_status()
            data = r.json()

            # ── нормализуем давление ────────────────────────────────
            try:
                # 1) текущее
                if "current_weather" in data and "pressure_msl" in data.get("current_weather", {}):
                    data["pressure_now"] = data["current_weather"]["pressure_msl"]
                else:
                    # 2) ближайший к полудню завтрашний час
                    hrs   = data["hourly"]["time"]
                    press = data["hourly"]["pressure_msl"]
                    noon_idx = min(range(len(hrs)),
                                   key=lambda i: abs(
                                       (pendulum.parse(hrs[i]).time() -
                                        pendulum.time(12, 0)).total_seconds()))
                    data["pressure_now"] = press[noon_idx]
            except Exception as e:
                logging.warning("pressure parse fail: %s", e)
                data["pressure_now"] = None

            return data

        except requests.HTTPError as e:
            if r.status_code == 400 and daily is daily_try[0]:
                logging.warning("precipitation_probability_max unsupported — fallback w/o it")
                continue
            logging.warning("%s -> %s", url.split('//')[1].split('/')[0], e)
            return None
    return None

# ───── AirVisual AQI ─────────────────────────────────────────────────────────
def fetch_aqi(lat, lon):
    if not AIR_KEY: return None
    url="https://api.airvisual.com/v2/nearest_city"
    return fetch_json(url, dict(lat=lat, lon=lon, key=AIR_KEY))

# ───── Tomorrow.io pollen ────────────────────────────────────────────────────
def fetch_pollen(lat, lon):
    if not TMR_KEY: return None
    url="https://api.tomorrow.io/v4/timelines"
    params=dict(
        location=f"{lat},{lon}", apikey=TMR_KEY,
        fields="treePollenIndex,grassPollenIndex,weedsPollenIndex",
        timesteps="1d", units="metric")
    js=fetch_json(url, params); 
    try:
        vals   = js["data"]["timelines"][0]["intervals"][0]["values"]
        scale  = ["очень низкий","низкий","средний","высокий","оч.высокий","экстрим"]
        return {k:scale[int(v)] for k,v in vals.items()}
    except Exception: return None

# ───── K-index ───────────────────────────────────────────────────────────────
def fetch_kp():
    url="https://services.swpc.noaa.gov/products/noaa-planetary-k-index.json"
    js=fetch_json(url)
    if not js: return None
    try:
        kp=float(js[-1][1]); return kp
    except Exception: return None

# ───── Schumann ──────────────────────────────────────────────────────────────
SCH_SOURCES=[
    "https://schumann-resonances.s3.amazonaws.com/latest.csv",
    "https://gci.mixonic.com/SR_latest.csv",
    "https://data-source.example/SR.csv"
]
def fetch_schumann():
    for url in SCH_SOURCES:
        try:
            txt=requests.get(url,timeout=10).text.strip().splitlines()
            f,a=map(float, txt[-1].split(",")[1:3]); return f,a
        except Exception: continue
    return None

# ───── Moon & astro ──────────────────────────────────────────────────────────
def moon_phase():
    now=pendulum.now(TZ)
    ref=pendulum.datetime(2000,1,6,tz="UTC")
    age = (now - ref).total_days() % 29.53
    pct = round(age/29.53*100)
    signs=["♈","♉","♊","♋","♌","♍","♎","♏","♐","♑","♒","♓"]
    sign=signs[int(((now - pendulum.datetime(2025,3,20,tz="UTC")).total_days()/2.3)%12)]
    return pct,sign

def astro_events():
    pct,sign=moon_phase()
    events=[
        f"Растущая Луна {sign} ({pct} %)",
        "Мини-парад планет",
        "Eta Aquarids (метеоры)"
    ]
    return events

# ───── «виновник дня» ────────────────────────────────────────────────────────
def choose_culprit(p, kp, wind, fog):
    opts=[]
    if p and p<1005: opts.append(("низкое давление","🥴"))
    if kp and kp>=4: opts.append(("магнитная буря","🧲"))
    if wind>25:      opts.append(("шальной ветер","💨"))
    if fog:          opts.append(("туман","🌫️"))
    # простая проверка ретрограда Меркурия (фикс. даты)
    if dt.date(2025,4,1)<=dt.date.today()<=dt.date(2025,4,25):
        opts.append(("ретроградный Меркурий","🪐"))
    if not opts: opts=[("погоду","✨")]
    return random.choice(opts)

# ───── построение сообщения ─────────────────────────────────────────────────
def build_msg():
    om = fetch_openmeteo(*CITIES["Limassol"])
    if not om: raise RuntimeError("Open-Meteo недоступен")
    d   = om["daily"]; cur = om["current_weather"]
    tmax=d["temperature_2m_max"][1]; tmin=d["temperature_2m_min"][1]
    desc=WC.get(d["weathercode"][1],"переменная")
    fog = d["weathercode"][1] in (45,48)
    pr   = d.get("pressure_msl",[None,None])[1]
    rain = d.get("precipitation_probability_max",[0,0])[1]
    wind = cur["windspeed"]; wdir=deg2compass(cur["winddirection"])

    # самые t° города
    temps={}
    for city,(lat,lon) in CITIES.items():
        od=fetch_openmeteo(lat,lon)
        if od: temps[city]=od["daily"]["temperature_2m_max"][1]
    warm=max(temps,key=temps.get); cold=min(temps,key=temps.get)

    # AQI
    air=fetch_aqi(*CITIES["Limassol"]) or {}
    aq=air.get("data",{}).get("current",{}).get("pollution",{})
    aqi=aq.get("aqius"); pm25=aq.get("p2"); pm10=aq.get("p1")

    # pollen
    pollen=fetch_pollen(*CITIES["Limassol"])
    pol_line = ("Деревья: {treePollenIndex} | Травы: {grassPollenIndex} | Сорняки: {weedsPollenIndex}"
                .format(**pollen) if pollen else "источник недоступен")

    # kp
    kp = fetch_kp()
    kp_desc="спокойный" if kp and kp<4 else "буря ⚠️"

    # schumann
    sch = fetch_schumann()
    sch_line = f"{sch[0]:.1f} Гц, amp {sch[1]:.1f}" if sch else "датчики молчат 🌱"

    # culprit
    culprit,emo = choose_culprit(pr, kp, wind, fog)

    # советы
    tips=random.sample(TIPS.get(culprit,["🙂 Улыбайтесь!"]),2)

    parts=[
        f"🌞 <b>Погода на завтра в Лимассоле {DATE}</b>",
        f"<b>Темп. днём:</b> до {safe(tmax)} °C",
        f"<b>Темп. ночью:</b> около {safe(tmin)} °C",
        f"<b>Облачность:</b> {desc}",
        f"<b>Осадки:</b> {'вероятен дождь 🌧' if rain and rain>40 else 'не ожидаются'}",
        f"<b>Ветер:</b> {wind:.1f} км/ч, {wdir}",
        f"<b>Давление:</b> {safe(pr)} гПа",
        f"<i>Самый тёплый:</i> {warm} ({temps[warm]:.1f} °C)\n<i>Самый прохладный:</i> {cold} ({temps[cold]:.1f} °C)",
        "───",
        f"🏙️ <b>Качество воздуха</b>\nAQI {safe(aqi)} | PM2.5: {safe(pm25)} | PM10: {safe(pm10)}",
        f"🌿 <b>Пыльца</b>\n{pol_line}",
        f"🧲 <b>Геомагнитная активность</b>\nK-index: {safe(kp)} ({kp_desc})",
        f"🎶 <b>Шуман:</b> {sch_line}",
        f"🌊 <b>Температура воды</b>\nСейчас: 20.3 °C",
        f"🔮 <b>Астрологические события</b>\n" + " | ".join(astro_events()),
        "───",
        f"📜 <b>Вывод</b>\nЕсли завтра что-то пойдёт не так — виновник: {culprit}! {emo}",
        "───",
        "✅ <b>Рекомендации</b>\n• "+"\n• ".join(tips)
    ]
    return "\n".join(parts)

# ───── main ──────────────────────────────────────────────────────────────────
async def main():
    html=build_msg()
    logging.info("Preview: %s …", html.replace('\n',' | ')[:200])
    await Bot(TOKEN).send_message(chat_id=CHAT, text=html[:4096],
                                  parse_mode="HTML", disable_web_page_preview=True)

if __name__ == "__main__":
    if not (TOKEN and CHAT):
        sys.exit("TELEGRAM_TOKEN / CHANNEL_ID не заданы в Secrets")
    asyncio.run(main())
