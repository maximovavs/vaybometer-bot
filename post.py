#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
VayboMeter 5.2 – полная сборка сообщения для Telegram-канала (Лимассол, CY)
▪ Open-Meteo (погода)               ▪ IQAir (AQI/PM)     ▪ NOAA K-index
▪ Copernicus SST (вода)             ▪ Schumann (шутка)   ▪ Астрособытия
"""

import os, asyncio, logging, random, math, requests, datetime as dt
import pendulum
from telegram import Bot

logging.basicConfig(level=logging.INFO,
                    format="%(levelname)s: %(message)s")

# ──────────────────────── 1  CONSTANTS ──────────────────────────
TOKEN      = os.environ["TELEGRAM_TOKEN"]
CHAT       = os.environ["CHANNEL_ID"]     # id канала/чата
AIR_KEY    = os.environ.get("AIRVISUAL_KEY")
OWM_KEY    = os.environ.get("OWM_KEY")
COP_USER   = os.environ.get("COPERNICUS_USER")
COP_PASS   = os.environ.get("COPERNICUS_PASS")

TZ = pendulum.timezone("Asia/Nicosia")
TODAY = pendulum.now(TZ).date()
TOMORROW = TODAY + pendulum.duration(days=1)

CITIES = {                       # lat , lon
    "Limassol": (34.707, 33.022),
    "Larnaca" : (34.916, 33.624),
    "Nicosia" : (35.170, 33.360),
    "Pafos"   : (34.776, 32.424),
}

HEADERS = {"User-Agent": "VayboMeter/5.2"}

COMPASS = ["N","NNE","NE","ENE","E","ESE","SE","SSE",
           "S","SSW","SW","WSW","W","WNW","NW","NNW"]

WC = {0:"ясно", 1:"☀️", 2:"част облачно", 3:"пасмурно",
      45:"туман", 48:"туман", 51:"морось", 61:"дождь"}

SCH_QUOTES = [
    "датчики молчат — ретрит 🌱",
    "кошачий мiau-фактор влияет на шуман 😸",
    "волны укатили ловить чаек 🐦",
    "показания отправились медитировать 🧘",
]

CULPRITS = {
    "низкое давление":       ("🌡️", ["💧 Пейте воду", "😴 Час тихого отдыха"]),
    "магнитные колебания":   ("🧲", ["🧘 Дыхательная гимнастика", "🌿 Чай с мелиссой"]),
    "шальной ветер":         ("💨", ["🧣 Захватите шарф", "🚶 Короткая прогулка"]),
    "ретроградный Меркурий": ("🪐", ["✍️ Не подписывайте важное", "😌 Больше терпения"]),
}

FACTS = [
    "11 мая — День морского бриза на Кипре 🌬️",
    "В 1974-м в этот день в Лимассоле открылся первый пляжный бар 🍹",
]

# ──────────────────────── 2  HELPERS ────────────────────────────
def compass(deg: float) -> str:
    i = int((deg/22.5)+.5) % 16
    return COMPASS[i]

def safe(val, unit=""):
    if val in (None, "None", "—"): return "—"
    if isinstance(val, float):
        return f"{val:.1f}{unit}"
    return f"{val}{unit}"

def format_kp(kp):
    if kp == "—": return kp
    return f"{kp:.1f} (спокойный)" if kp < 4 else f"{kp:.1f} (повышен)"

# ──────────────────────── 3  FETCHERS ───────────────────────────
def fetch_openmeteo(lat, lon):
    base = dict(latitude=lat, longitude=lon, timezone="auto",
                current_weather=True, forecast_days=2,
                daily="temperature_2m_max,temperature_2m_min,weathercode,pressure_msl")
    url="https://api.open-meteo.com/v1/forecast"
    try:
        r=requests.get(url, params=base, timeout=10, headers=HEADERS); r.raise_for_status()
        return r.json()
    except requests.HTTPError as e:
        logging.warning("OpenMeteo %s – %s", lat, e)
        return None

def fetch_iqair(city):
    if not AIR_KEY: return {}
    try:
        r=requests.get(f"http://api.airvisual.com/v2/city?city={city}&state=Limassol&country=Cyprus&key={AIR_KEY}", timeout=10)
        r.raise_for_status(); j=r.json()["data"]["current"]
        return j["pollution"] | j["weather"]
    except Exception as e:
        logging.warning("IQAir: %s", e); return {}

def fetch_kp():
    try:
        r=requests.get("https://services.swpc.noaa.gov/products/noaa-estimated-planetary-k-index-1-minute.json", timeout=10)
        r.raise_for_status(); rows=r.json(); kp=float(rows[-1][1]); return kp
    except Exception as e:
        logging.warning("Kp: %s", e); return "—"

def fetch_sst():
    # упрощённо — всегда берём статич. температуру (пример)
    return 20.3

def schumann_joke():
    return random.choice(SCH_QUOTES)

# ──────────────────────── 4  ASTRO ──────────────────────────────
def moon_phase():
    now = pendulum.now(TZ)
    age = ((now.naive - pendulum.datetime(2000,1,6)).days) % 29.53
    pct = int(age/29.53*100)
    signs = ["♑","♒","♓","♈","♉","♊","♋","♌","♍","♎","♏","♐"]
    sign = signs[(now.add(hours=1).day_of_year*12)//365 % 12]
    return pct, sign

def astro_events():
    pct, sign = moon_phase()
    ev = [f"Растущая Луна {sign} ({pct} %)", "Мини-парад планет", "Eta Aquarids (метеоры)"]
    return ev

# ──────────────────────── 5  BUILD MESSAGE ──────────────────────
def build_msg():
    om = fetch_openmeteo(*CITIES["Limassol"])
    if not om:
        raise RuntimeError("Open-Meteo недоступен")

    d  = om["daily"]
    cur= om["current_weather"]
    tmax, tmin   = d["temperature_2m_max"][1], d["temperature_2m_min"][1]
    wcode        = d["weathercode"][1]
    press        = d["pressure_msl"][1] or cur.get("surface_pressure")
    cloud        = WC.get(wcode, "переменная")
    rain         = "не ожидаются" if wcode in (0,1,2) else "вероятны"
    wind_deg     = cur["winddirection"]; wind_spd=cur["windspeed"]
    wind_txt     = f"{wind_spd:.1f} км/ч, {compass(wind_deg)}"

    # теплее/холоднее
    temps={}
    for name,(lat,lon) in CITIES.items():
        omc=fetch_openmeteo(lat,lon)
        if omc: temps[name]=omc["daily"]["temperature_2m_max"][1]
    warm=max(temps,key=temps.get); cold=min(temps,key=temps.get)

    # воздух
    pol = fetch_iqair("Limassol") if AIR_KEY else {}
    aqi   = safe(pol.get("aqius"))
    pm25  = safe(pol.get("p2")," µg/м³")
    pm10  = safe(pol.get("p1")," µg/м³")

    # kp-index & culprit
    kp  = fetch_kp()
    culprit, emo = random.choice(list(CULPRITS.items()))
    if culprit=="магнитные колебания" and kp!="—" and kp<4:
        culprit, emo = "низкое давление","🌡️"         # меняем, если бурь нет
    tips = random.sample(CULPRITS[culprit][1], 2)

    parts = [
        f"🙂 <b>Погода на завтра в Лимассоле {TOMORROW.format('DD.MM.YYYY')}</b>",
        f"<b>Темп. днём:</b> до {safe(tmax,' °C')}",
        f"<b>Темп. ночью:</b> около {safe(tmin,' °C')}",
        f"<b>Облачность:</b> {cloud}",
        f"<b>Осадки:</b> {rain}",
        f"<b>Ветер:</b> {wind_txt}",
        f"<b>Давление:</b> {safe(press,' гПа')}",
        f"<i>Самый тёплый:</i> {warm} ({temps[warm]:.1f} °C)",
        f"<i>Самый прохладный:</i> {cold} ({temps[cold]:.1f} °C)",
        "———",
        f"🏙️ <b>Качество воздуха</b>\nAQI {aqi} | PM2.5: {pm25} | PM10: {pm10}",
        f"🌿 <b>Пыльца</b>\nисточник недоступен" if not os.getenv("AMBEE_KEY") else "", # placeholder
        f"🧲 <b>Геомагнитная активность</b>\nK-index: {format_kp(kp)}",
        f"🎵 <b>Шуман:</b> {schumann_joke()}",
        f"🌊 <b>Температура воды</b>\nСейчас: {fetch_sst():.1f} °C",
        "🌌 <b>Астрологические события</b>\n" + " | ".join(astro_events()),
        "———",
        f"📜 <b>Вывод</b>\nЕсли завтра что-то пойдёт не так — виновник: {culprit}! {emo}",
        "———",
        "✅ <b>Рекомендации</b>",
        *[f"• {t}" for t in tips],
        "———",
        f"📚 {random.choice(FACTS)}",
    ]
    return "\n".join(filter(bool, parts))

# ──────────────────────── 6  MAIN ───────────────────────────────
async def main():
    html = build_msg()
    logging.info("Preview: %s", html.replace("\n"," | ")[:250])
    await Bot(TOKEN).send_message(int(CHAT), html[:4096], parse_mode="HTML", disable_web_page_preview=True)

if __name__ == "__main__":
    asyncio.run(main())
