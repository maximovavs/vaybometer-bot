#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
VayboМетр 6.1  •  full blocks, bug-fix moon_phase() & Schumann
"""

import os, asyncio, json, math, csv, io, random, pendulum, requests
from telegram import Bot

# ── Параметры ───────────────────────────────────────────────────────────────
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

# ── HTTP helper ─────────────────────────────────────────────────────────────
def http(url, **params):
    try:
        r = requests.get(url, params=params, timeout=20)
        r.raise_for_status()
        return r.text if url.endswith(".csv") else r.json()
    except Exception as e:
        print("[warn]", url.split('/')[2], "->", e)
        return None

def compass(deg): return HEAD[int((deg/22.5)+.5)%16]

# ── Open-Meteo (погода) ──────────────────────────────────────────────────────
def om_daily(lat, lon):
    p = dict(latitude=lat, longitude=lon, timezone="auto",
             daily="temperature_2m_max,temperature_2m_min,weathercode",
             forecast_days=2)
    j = http("https://api.open-meteo.com/v1/forecast", **p)
    return j.get("daily") if j else {}

def om_current(lat, lon):
    p = dict(latitude=lat, longitude=lon, timezone="auto",
             current_weather="true", hourly="surface_pressure")
    j = http("https://api.open-meteo.com/v1/forecast", **p)
    if not j: return {}
    cur = j.get("current_weather", {})
    # давление сидит в hourly на том же timestamp
    try:
        idx = j["hourly"]["time"].index(cur["time"])
        cur["surface_pressure"] = j["hourly"]["surface_pressure"][idx]
    except Exception:
        pass
    return cur

# ── AQI (IQAir) ─────────────────────────────────────────────────────────────
def air_quality(lat, lon, key):
    if not key: return {}
    j = http("https://api.airvisual.com/v2/nearest_city",
             lat=lat, lon=lon, key=key)
    if not j or j.get("status") != "success": return {}
    pol = j["data"]["current"]["pollution"]
    return {"aqi": pol["aqius"],
            "p2":  pol.get("p2"),      # PM2.5
            "p10": pol.get("p1")}      # PM10

# ── Pollen (Tomorrow.io) ─────────────────────────────────────────────────────
def pollen(lat, lon, key):
    if not key: return {}
    fields = "treeIndex,grassIndex,weedIndex"
    j = http("https://api.tomorrow.io/v4/timelines",
             location=f"{lat},{lon}", fields=fields,
             timesteps="1d", units="metric", apikey=key)
    try:
        return j["data"]["timelines"][0]["intervals"][0]["values"]
    except Exception:
        return {}

# ── K-index (NOAA) ──────────────────────────────────────────────────────────
def kp_index():
    j = http("https://services.swpc.noaa.gov/products/noaa-planetary-k-index.json")
    try: return int(j[-1][1]) if j else None
    except Exception: return None

# ── Schumann (GCI backup) ────────────────────────────────────────────────────
def schumann():
    urls = [
        "https://schumann-resonances.s3.amazonaws.com/latest.csv",
        "https://sosrff.tsu.ru/schumann/current_data.csv"]
    for u in urls:
        txt = http(u)
        if not txt: continue
        rows = list(csv.reader(io.StringIO(txt)))
        try: f,a = map(float, rows[-1][1:3]); return f,a
        except Exception: continue
    return None

# ── Sea-surface temperature (заглушка) ───────────────────────────────────────
def sst_temp(user, pwd):
    if user and pwd:
        return 20.3  # demo
    return None

# ── Астрология ───────────────────────────────────────────────────────────────
def moon_phase():
    now_ts = pendulum.now(TZ).int_timestamp
    ref_ts = pendulum.datetime(2000, 1, 6, tz="UTC").int_timestamp
    age = ((now_ts - ref_ts) / 86400) % 29.53
    pct = round(age / 29.53 * 100)
    signs = "♈♉♊♋♌♍♎♏♐♑♒♓"
    sign = signs[int(((now_ts / 86400) / (29.53 / 12)) % 12)]
    return pct, sign

def astro_events():
    pct, sign = moon_phase()
    return [f"Растущая Луна {sign} ({pct} %)",
            "Мини-парад планет",
            "Eta Aquarids (пик 6 мая)"]

# ── Главный builder ──────────────────────────────────────────────────────────
def build_msg():
    daily, cur = om_daily(*LIM), om_current(*LIM)
    tmax = daily.get("temperature_2m_max", [None, None])[1]
    tmin = daily.get("temperature_2m_min", [None, None])[1]
    wcode = daily.get("weathercode", [None, None])[1]
    desc  = WC.get(wcode, "переменная")
    fog   = wcode in (45, 48)

    wind, wdir = cur.get("windspeed", 0), compass(cur.get("winddirection", 0))
    pres = cur.get("surface_pressure", "—")

    # сравниваем города
    temps = {c: om_daily(*xy).get("temperature_2m_max", [None, None])[1] for c, xy in CITIES.items()}
    warm = max((k for k, v in temps.items() if v), key=temps.get)
    cold = min((k for k, v in temps.items() if v), key=temps.get)

    aq  = air_quality(*LIM, os.getenv("AIRVISUAL_KEY"))
    pol = pollen(*LIM, os.getenv("TOMORROW_KEY"))
    kp  = kp_index()
    sch = schumann()
    sst = sst_temp(os.getenv("COPERNICUS_USER"), os.getenv("COPERNICUS_PASS"))

    culprit = ("низкое давление"  if isinstance(pres, (int, float)) and pres < 1005 else
               "туман"            if fog else
               "повышенный Kp-индекс" if kp and kp >= 5 else
               "мини-парад планет")
    rec = {
        "низкое давление": "💧 Пейте воду — помогает при давлении",
        "туман":           "⚠️ Утром внимательнее на дорогах",
        "повышенный Kp-индекс": "🧘 Небольшая медитация выровняет состояние",
        "мини-парад планет":    "🔭 Ночью взгляните на небо",
    }[culprit]

    date = (pendulum.now(TZ) + pendulum.duration(days=1)).format("DD.MM.YYYY")
    parts = [
        f"🌞 <b>Погода на завтра в Лимассоле {date}</b>",
        f"<b>Темп. днём:</b> до {tmax:.1f} °C" if tmax else "",
        f"<b>Темп. ночью:</b> около {tmin:.1f} °C" if tmin else "",
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
        (f"Деревья: {pol.get('treeIndex','—')} | Травы: {pol.get('grassIndex','—')} | "
         f"Сорняки: {pol.get('weedIndex','—')}"),
        "🧲 <b>Геомагнитная активность</b>",
        f"Kp {kp if kp is not None else '—'}",
        "📈 <b>Резонанс Шумана</b>",
        f"{sch[0]:.1f} Гц, A={sch[1]:.1f}" if sch else "датчики молчат — ушли в ретрит",
        "🌊 <b>Температура воды</b>",
        f"Сейчас: {sst:.1f} °C" if sst else "—",
        "🔮 <b>Астрологические события</b>",
        " | ".join(astro_events()),
        "———",
        "📝 <b>Вывод</b>",
        f"Если завтра что-то пойдёт не так — виновник: {culprit}! 😉",
        "———",
        "✅ <b>Рекомендации</b>",
        f"• {rec}",
        "• 🌞 Ловите солнечные витамины!",
        "• 🚶‍♀️ Прогуляйтесь на свежем воздухе",
    ]
    if fog:
        parts.insert(6, "⚠️ Возможен туман утром — снизьте скорость на дорогах.")
    return "\n".join(filter(None, parts))

async def main():
    bot  = Bot(os.getenv("TELEGRAM_TOKEN"))
    html = build_msg()
    await bot.send_message(
        os.getenv("CHANNEL_ID"), html[:4096],
        parse_mode="HTML", disable_web_page_preview=True
    )

if __name__ == "__main__":
    asyncio.run(main())
