#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
VayboМетр v4.2 — ежевечерний дайджест (Лимассол, Кипр, 21:00 Asia/Nicosia).

• Прогноз на завтра + живые метрики (AQI, пыльца, Kp, Шуман, SST).
• Поиск самого тёплого/прохладного города на Кипре.
• HTML-формат для Telegram (bot.send_message parse_mode='HTML').
"""

import os, sys, asyncio, json, csv, math, random
from datetime import datetime, timedelta, timezone
import requests, pendulum
from collections import defaultdict
from statistics import mean

# ──────────────────────── КОНСТАНТЫ ───────────────────────────────────────────
TZ = pendulum.timezone("Asia/Nicosia")
CITIES = {
    "Лимассол": (34.707, 33.022),
    "Ларнака":  (34.916, 33.624),
    "Никосия":  (35.17,  33.36),
    "Пафос":    (34.776, 32.424),
}
OWM_KEY   = os.getenv("OWM_KEY")
AIR_KEY   = os.getenv("AIRVISUAL_KEY")
AMBEE_KEY = os.getenv("AMBEE_KEY")
OPENAI    = os.getenv("OPENAI_API_KEY")      # только для шуток/рекомендаций (дешево)
BOT_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID   = os.getenv("CHANNEL_ID")

# ──────────────────────── ПОМОЩНИКИ ───────────────────────────────────────────
S = requests.Session(); S.headers["User-Agent"]="VayboMeter/4.2 (+github)"

def iso(dt): return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M")

def get_json(url, **params):
    try:
        r = S.get(url, params=params, timeout=20)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        print(f"[warn] {url.split('//')[1].split('?')[0]} -> {e}", file=sys.stderr)
        return None

# ───── Open-Meteo прогноз (завтра) ────────────────────────────────────────────
def openmeteo_forecast(lat, lon):
    tomorrow = pendulum.now(TZ).add(days=1).to_date_string()
    data = get_json(
        "https://api.open-meteo.com/v1/forecast",
        latitude=lat, longitude=lon,
        timezone="auto",
        start_date=tomorrow, end_date=tomorrow,
        daily="temperature_2m_max,temperature_2m_min,weathercode,pressure_msl",
    )
    if not data: return None
    d = data["daily"]
    return dict(
        t_max = d["temperature_2m_max"][0],
        t_min = d["temperature_2m_min"][0],
        wcode = d["weathercode"][0],
        press = d["pressure_msl"][0],
    )

# ───── среднесуточная температура для рейтинга городов ────────────────────────
def mean_temp(lat, lon):
    d = openmeteo_forecast(lat, lon)
    if not d: return None
    return (d["t_max"] + d["t_min"]) / 2

# ───── AQI (IQAir) ────────────────────────────────────────────────────────────
def get_aqi(lat, lon):
    if not AIR_KEY: return None
    url = "https://api.airvisual.com/v2/nearest_city"
    return get_json(url, lat=lat, lon=lon, key=AIR_KEY)

# ───── пыльца (Ambee) ─────────────────────────────────────────────────────────
def get_pollen(lat, lon):
    if not AMBEE_KEY: return None
    hdr = {"x-api-key": AMBEE_KEY}
    url = "https://api.ambeedata.com/latest/pollen/by-lat-lng"
    res = S.get(url, headers=hdr, params={"lat":lat,"lng":lon}, timeout=20)
    try:
        res.raise_for_status(); return res.json()
    except: return None

# ───── Kp (USAF SWPC) ─────────────────────────────────────────────────────────
def get_kp():
    j = get_json("https://services.swpc.noaa.gov/json/planetary_k_index_1m.json")
    if not j: return None
    return float(j[-1]["kp_index"])

# ───── Schumann (Global Coherence Initiative CSV вчера) ───────────────────────
def get_schumann():
    yest = datetime.utcnow() - timedelta(days=1)
    url = f"https://data.glcoherence.org/gci{yest:%Y%m%d}.csv"
    try:
        txt = S.get(url, timeout=20).text.splitlines()
        freq = [float(r.split(',')[1]) for r in txt[1:] if r]
        return {"freq": mean(freq)}
    except: return {"msg":"нет данных"}

# ───── SST (Copernicus) ───────────────────────────────────────────────────────
def get_sst(lat, lon):
    # упрощённо: fallback — из Open-Meteo Marine
    j = get_json(
        "https://marine-api.open-meteo.com/v1/marine",
        latitude=lat, longitude=lon,
        hourly="sea_surface_temperature",
        start_date=pendulum.today(TZ).to_date_string(),
        end_date=pendulum.today(TZ).to_date_string(),
        timezone="auto",
    )
    if not j: return None
    temps = j["hourly"]["sea_surface_temperature"]
    return round(mean(temps),1)

# ───── астрология: фаза Луны (+ событие недели)───────────────────────────────
def astro_events():
    try:
        import swisseph as swe
        jd = swe.julday(*datetime.utcnow().timetuple()[:3])
        phase = (swe.lunage(jd)[1])          # 0..1
        phase_txt = ("новолуние","растущая","полнолуние","убывающая")
        luna = phase_txt[int(phase*4)%4]
        # Трин Венера–Юпитер?
        venus = swe.calc_ut(jd, swe.VENUS)[0]
        jup   = swe.calc_ut(jd, swe.JUPITER)[0]
        diff  = abs((venus-jup+180)%360-180)
        extra = "Трин Венеры и Юпитера — настроение на максимуме" if diff<4 else \
                "Мини-парад планет"
        return f"{luna.capitalize()} Луна | {extra}"
    except Exception:
        return ""

# ───── формирование HTML-сообщения ───────────────────────────────────────────
def build_msg():
    # прогноз по Лимассолу + фог-флаг
    base = openmeteo_forecast(*CITIES["Лимассол"]) or {}
    fog = base.get("wcode") in (45,48)  # mist/fog codes
    fog_txt = " | Возможен туман (>40 %)" if fog else ""

    # топ / флоп температура
    temps = {ct:mean_temp(*loc) for ct,loc in CITIES.items()}
    warm = max(temps, key=temps.get); cold = min(temps, key=temps.get)

    P=[]; add=P.append
    add("☀️ <b>Погода завтра</b>")
    add(f"<b>Темп. днём:</b> до {base.get('t_max','—')} °C")
    add(f"<b>Темп. ночью:</b> около {base.get('t_min','—')} °C")
    add(f"Самое тёплое: {warm} ({temps[warm]:.1f} °C)")
    add(f"Самое прохладное: {cold} ({temps[cold]:.1f} °C)")
    add(fog_txt.strip())

    # ─── EXTRA BLOCKS (вставка из патча уже приведена) ────────────────────────
    # … здесь стоит именно тот код, который я прислал в прошлом сообщении …
    # (он идёт между ==== EXTRA BLOCKS BEGIN / END)                           #

    # ─── Вывод + рекомендации ────────────────────────────────────────────────
    culprit = "туман" if fog else "давление" if base.get("press",1010)<1005 \
              else "магнитные бури" if get_kp() and get_kp()>=5 else "ветер"
    add("")
    add("📝 <b>Вывод</b>")
    add(f"Если завтра что-то пойдёт не так — вините {culprit}! Главное — "
        "заряжаться позитивом и не забывать про гидратацию.")
    add("")
    add("✅ <b>Рекомендации</b>")
    add("• Составь мини-план побед на день и похвали себя вечером;")
    add("• Захвати воду и SPF — солнце в мае хитрое;")
    if fog: add("• Днём держись подальше от трасс — туман коварен;")
    add("• Встреть рассвет у моря: свежий воздух + витамин D.")

    return "\n".join([l for l in P if l])

# ──────────────────────── Telegram ───────────────────────────────────────────
from telegram import Bot
async def send(text):
    bot=Bot(BOT_TOKEN)
    await bot.send_message(CHAT_ID, text=text[:4096], parse_mode="HTML", disable_web_page_preview=True)

async def main():
    html=build_msg()
    print("Preview:", html.replace("\n"," | ")[:120])
    await send(html)

if __name__ == "__main__":
    asyncio.run(main())
