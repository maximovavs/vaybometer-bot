#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
VayboMeter 5.1-hotfix3  (11 May 2025)
— вечерний «дайджест самочувствия» для Лимассола.

ЧТО ИСПРАВЛЕНО:
  • safe() теперь всегда возвращает str → ошибка с K-index исчезла
  • мелкая чистка логики culprit
"""

from __future__ import annotations
import os, math, random, logging, requests, pendulum, asyncio
from telegram import Bot

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
TZ        = "Asia/Nicosia"
TODAY     = pendulum.now(TZ).date()
TOMORROW  = TODAY.add(days=1)
HEADERS   = {"User-Agent": "vaybometer/5.1"}

# ─────────── статические таблицы
CITIES = {
    "Limassol": (34.707, 33.022),
    "Larnaca":  (34.916, 33.624),
    "Nicosia":  (35.170, 33.360),
    "Pafos":    (34.776, 32.424),
}
WC = {0:"ясно",1:"преимущественно ясно",2:"переменная",3:"пасмурно",
      45:"туман",48:"туман/изморозь",51:"морось",61:"дождь",95:"гроза"}
COMPASS = ["N","NNE","NE","ENE","E","ESE","SE","SSE",
           "S","SSW","SW","WSW","W","WNW","NW","NNW"]

# ─────────── helpers
def safe(v, dash="—") -> str:
    """возвращает строку с данными или '—'"""
    if v in (None, "") or (isinstance(v, float) and math.isnan(v)): 
        return dash
    if isinstance(v, float):
        return f"{v:.1f}"
    return str(v)

def deg2compass(d: float|None) -> str:
    return "—" if d is None else COMPASS[int((d/22.5)+.5) % 16]

def fetch_json(url: str, **kw):
    try:
        r = requests.get(url, headers=HEADERS, timeout=10, **kw)
        r.raise_for_status(); return r.json()
    except Exception as e:
        logging.warning("%s -> %s", url.split('//')[1].split('/')[0], e)
        return {}

# ─────────── сети
def fetch_openmeteo(lat, lon):
    url = "https://api.open-meteo.com/v1/forecast"
    params = dict(latitude=lat, longitude=lon, timezone="auto",
                  forecast_days=2, current_weather="true",
                  daily="temperature_2m_max,temperature_2m_min,weathercode")
    return fetch_json(url, params=params)

def fetch_airvisual():
    key = os.getenv("AIRVISUAL_KEY")
    if not key: return {}
    return fetch_json(f"https://api.airvisual.com/v2/nearest_city?key={key}").get("data", {})

def fetch_kp():
    arr = fetch_json("https://services.swpc.noaa.gov/products/noaa-planetary-k-index.json")
    return float(arr[-1][1]) if arr else None

def fetch_pressure_owm():
    key = os.getenv("OWM_KEY")
    if not key: return None
    js = fetch_json("https://api.openweathermap.org/data/2.5/weather",
                    params=dict(lat=34.707, lon=33.022, appid=key, units="metric"))
    return js.get("main", {}).get("pressure")

def fetch_schumann():
    for u in ("https://schumann-resonances.s3.amazonaws.com/latest.csv",
              "https://gci.mixonic.com/SR_latest.csv"):
        try:
            lines = requests.get(u, timeout=10).text.strip().splitlines()
            _, *rows = lines
            f, a = map(float, rows[-1].split(",")[1:3])
            return f"{f:.1f} Гц · A={a:.1f}"
        except Exception:
            continue
    return random.choice(["датчики молчат 🌱", "Земля медитирует 🧘‍♂️", "SR-flat ☕"])

# ─────────── основная сборка
def build_msg() -> str:
    om = fetch_openmeteo(*CITIES["Limassol"])
    if not om:
        raise RuntimeError("Open-Meteo недоступен")

    daily, cur = om["daily"], om["current_weather"]
    dmax, dmin  = daily["temperature_2m_max"][1], daily["temperature_2m_min"][1]
    desc        = WC.get(int(daily["weathercode"][1]), "переменная")
    windspd     = cur.get("windspeed")
    winddir     = deg2compass(cur.get("winddirection"))
    pressure    = cur.get("pressure_msl") or fetch_pressure_owm()
    
    # температурный «топ»
    temps = {c: fetch_openmeteo(*coords).get("daily",{}).get("temperature_2m_max",[None,None])[1]
             for c, coords in CITIES.items()}
    warm = max((k for k,v in temps.items() if v is not None), key=lambda k: temps[k])
    cold = min((k for k,v in temps.items() if v is not None), key=lambda k: temps[k])

    # воздух
    av  = fetch_airvisual()
    pol = av.get("current", {}).get("pollution", {})
    aqi = int(pol.get("aqius", 64)); pm2, pm10 = pol.get("p2"), pol.get("p1")
    aqi_color = ("🟢","🟡","🟠","🔴","🟣")[0 if aqi<=50 else 1 if aqi<=100 else 2 if aqi<=150 else 3 if aqi<=200 else 4]

    kp  = fetch_kp()
    sch = fetch_schumann()

    # culprit
    options = []
    if isinstance(pressure,(int,float)) and pressure < 1005: options.append(("низкое давление","📉"))
    if kp and kp >= 4:                                       options.append(("магнитная буря","🧲"))
    if windspd and windspd >= 25:                            options.append(("шальной ветер","💨"))
    if not options: options.append(("погоду","🌦"))
    culprit, emoji = random.choice(options)

    lines = [
        f"🌞 <b>Погода на завтра в Лимассоле {TOMORROW.format('DD.MM.YYYY')}</b>",
        f"<b>Темп. днём:</b> до {dmax} °C",
        f"<b>Темп. ночью:</b> около {dmin} °C",
        f"<b>Облачность:</b> {desc}",
        "<b>Осадки:</b> не ожидаются",
        f"<b>Ветер:</b> {safe(windspd)} км/ч, {winddir}",
        f"<b>Давление:</b> {safe(pressure)} гПа",
        f"<i>Самый тёплый:</i> {warm} ({safe(temps[warm])} °C)",
        f"<i>Самый прохладный:</i> {cold} ({safe(temps[cold])} °C)",
        "———",
        "🏭 <b>Качество воздуха</b>",
        f"AQI {aqi_color} {aqi} | PM2.5: {safe(pm2)} | PM10: {safe(pm10)}",
        f"🧲 <b>K-index:</b> {safe(kp)}",
        f"🎶 <b>Шуман:</b> {sch}",
        f"🌊 <b>Вода:</b> {safe(cur.get('temperature'))} °C",
        "———",
        "📝 <b>Вывод</b>",
        f"Если завтра что-то пойдёт не так — вините {culprit}! {emoji}",
    ]
    html = "\n".join(lines)
    logging.info("Preview: %s", html.replace('\n',' | ')[:220])
    return html

# ─────────── main
async def main():
    tok, chat = os.getenv("TELEGRAM_TOKEN"), os.getenv("CHANNEL_ID")
    if not tok or not chat:
        raise SystemExit("☠️  Secrets TELEGRAM_TOKEN / CHANNEL_ID отсутствуют")
    html = build_msg()
    await Bot(tok).send_message(chat, html, parse_mode="HTML",
                                disable_web_page_preview=True)

if __name__ == "__main__":
    asyncio.run(main())
