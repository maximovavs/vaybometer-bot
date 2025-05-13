#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import asyncio
import logging
import random
import datetime as dt
import pendulum

from telegram import Bot, error as tg_err
from openai import OpenAI

from weather import get_weather
# предполагаем, что вы вынесли в модули:
# - air_pollen_sst_kp.py: get_air(), get_pollen(), get_sst(), get_kp()
# - astro.py: moon_phase(), planet_parade(), eta_aquarids(), upcoming_event(), astro_events()
# - utils.py: compass(), clouds_word(), wind_phrase(), safe(), get_fact()
from air import get_air, get_pollen, get_sst, get_kp
from astro import astro_events
from utils import compass, clouds_word, wind_phrase, safe, get_fact

# ─────────── 0. CONST ───────────────────────────────────────────
LAT, LON = 34.707, 33.022
CITIES = {
    "Limassol": (34.707, 33.022),
    "Larnaca":  (34.916, 33.624),
    "Nicosia":  (35.170, 33.360),
    "Pafos":    (34.776, 32.424),
}

TOKEN      = os.environ["TELEGRAM_TOKEN"]
CHAT       = os.environ["CHANNEL_ID"]
OPENAI_KEY = os.getenv("OPENAI_API_KEY")
TZ         = pendulum.timezone("Asia/Nicosia")
TODAY      = pendulum.now(TZ).date()
TOMORROW   = TODAY + pendulum.duration(days=1)

WEATHER_ICONS = {
    "ясно":       "☀️",
    "переменная": "🌤️",
    "пасмурно":   "☁️",
    "дождь":      "🌧️",
    "туман":      "🌁",
}

# ─────────── 5. GPT / CULPRITS ──────────────────────────────────
CULPRITS = {
    "туман": {"emoji":"🌁","tips":["🔦 Светлая одежда","🚗 Водите аккуратно","⏰ Выходите заранее"]},
    "магнитные бури": {"emoji":"🧲","tips":["🧘 Дыхательная пауза","🌿 Чай с мелиссой","😌 Избегайте стресса"]},
    "низкое давление": {"emoji":"🌡️","tips":["💧 Пейте воду","😴 Днём отдохните","🥗 Лёгкий ужин"]},
    "шальной ветер": {"emoji":"💨","tips":["🧣 Захватите шарф","🚶 Прогулка по ветру","🕶️ Очки от пыли"]},
    "мини-парад планет": {"emoji":"✨","tips":["🔭 Смотрите на небо","📸 Фото на память","🤔 Задумайтесь"]},
    "жара": {"emoji":"🔥","tips":["💦 Пейте воду","🧢 Наденьте шляпу","🌳 Ищите тень"]},
    "сырость": {"emoji":"💧","tips":["👟 Сменная обувь","🌂 Держите зонт","🌬️ Проветривайте"]},
}

def gpt_blurb(culprit: str) -> tuple[str, list[str]]:
    pool = CULPRITS[culprit]["tips"]
    if not OPENAI_KEY:
        return f"Если завтра что-то пойдёт не так, вините {culprit}! 😉", random.sample(pool, 2)
    prompt = (f"Одна строка «Если завтра что-то пойдёт не так, вините {culprit}!». "
              "Через точку — позитив. Далее 3 буллета ≤12 слов.")
    out = OpenAI(api_key=OPENAI_KEY).chat.completions.create(
        model="gpt-4o-mini", temperature=0.6,
        messages=[{"role":"user","content":prompt}]
    ).choices[0].message.content.strip().splitlines()
    lines = [l.strip() for l in out if l.strip()]
    summary = lines[0]
    tips = [l.lstrip("-• ").strip() for l in lines[1:4]]
    if len(tips) < 2:
        tips = random.sample(pool, 2)
    return summary, tips

# ─────────── 6. BUILD MESSAGE ───────────────────────────────────
def build_msg() -> str:
    w = get_weather(LAT, LON)
    if not w:
        raise RuntimeError("Источники погоды недоступны")

    if "current" in w:  # OpenWeather
        cur      = w["current"]
        press    = cur["pressure"]
        wind_kmh = cur["wind_speed"] * 3.6
        wind_deg = cur["wind_deg"]
        cloud_w  = clouds_word(cur.get("clouds", 0))
        day_max  = w["daily"][0]["temp"]["max"]
        night_min = w["daily"][0]["temp"]["min"]
        wcode    = cur.get("weather", [{}])[0].get("id", 0)
    else:               # Open-Meteo
        cur      = w["current_weather"]
        # если в cur нет pressure, берём из hourly
        press    = cur.get("pressure", w["hourly"]["surface_pressure"][0])
        wind_kmh = cur["windspeed"]
        wind_deg = cur["winddirection"]
        cloud_w  = clouds_word(w["hourly"]["cloud_cover"][0])
        # из daily
        tm = w["daily"]["temperature_2m_max"]
        tn = w["daily"]["temperature_2m_min"]
        codes = w["daily"]["weathercode"]
        day_max   = tm[1] if len(tm) > 1 else tm[0]
        night_min = tn[1] if len(tn) > 1 else tn[0]
        wcode     = codes[1] if len(codes) > 1 else codes[0]

    # … дальше ваш код как было …


    strong_wind = w.get("strong_wind", False)
    fog_alert   = w.get("fog_alert", False)

    # лидеры
    temps = {}
    for city,(la,lo) in CITIES.items():
        ww = get_weather(la, lo)
        if not ww: continue
        d0 = ww["daily"][0]
        mv = d0["temperature_2m_max"][1] if len(d0["temperature_2m_max"])>1 else d0["temperature_2m_max"][0]
        temps[city] = mv
    warm = max(temps, key=temps.get)
    cold = min(temps, key=temps.get)

    # воздух/пыльца/kp/sst/schumann
    air    = get_air() or {}
    pollen = get_pollen()
    kp, kp_state = get_kp()
    sst    = get_sst()
    sch    = get_schumann()
    astro  = astro_events()

    # виновник
    if fog_alert:
        culprit="туман"
    elif kp_state=="буря":
        culprit="магнитные бури"
    elif press<1007:
        culprit="низкое давление"
    elif strong_wind:
        culprit="шальной ветер"
    else:
        culprit="мини-парад планет"
    summary, tips = gpt_blurb(culprit)

    icon = WEATHER_ICONS.get(cloud_w,"🌦️")

    lines = [
        f"{icon} <b>Погода на завтра в Лимассоле {TOMORROW.format('DD.MM.YYYY')}</b>",
        f"<b>Днём:</b> до {tmax:.1f}°C  <b>Ночью:</b> около {tmin:.1f}°C",
        f"<b>Облачность:</b> {cloud_w}",
        f"<b>Ветер:</b> {wind_phrase(wind_kmh)} ({wind_kmh:.1f} км/ч, {compass(wind_deg)})",
        *(["⚠️ Ветер усилится"] if strong_wind else []),
        *(["🌁 Возможен туман"] if fog_alert else []),
        f"<b>Давление:</b> {press:.0f} гПа",
        f"<i>Самый тёплый город:</i> {warm} ({temps[warm]:.1f}°C)",
        f"<i>Самый прохладный город:</i> {cold} ({temps[cold]:.1f}°C)",
        "———",
        f"🏙️ <b>Качество воздуха</b>  AQI {air.get('aqi','—')} ({air.get('lvl','—')})",
        f"PM2.5: {safe(air.get('pm25'))}  PM10: {safe(air.get('pm10'))}",
    ]

    if pollen:
        idx = lambda v: ["нет","низкий","умеренный","высокий","оч. высокий","экстрим"][int(round(v))]
        lines += [
            "🌿 <b>Пыльца</b>",
            f"Деревья — {idx(pollen['treeIndex'])} | Травы — {idx(pollen['grassIndex'])} | Сорняки — {idx(pollen['weedIndex'])}"
        ]

    lines += [
        f"🧲 <b>Геомагнитка:</b> Kp {kp:.1f} ({kp_state})" if kp is not None else "🧲 <b>Геомагнитка:</b> —",
    ]

    if sch.get("high"):
        lines.append("🎵 <b>Шуман:</b> ⚡️ вибрации повышены")
    elif "freq" in sch:
        lines.append(f"🎵 <b>Шуман:</b> ≈{sch['freq']:.1f} Гц")
    else:
        lines.append(f"🎵 <b>Шуман:</b> {sch.get('msg','—')}")

    if sst is not None:
        lines.append(f"🌊 <b>Вода:</b> {sst:.1f}°C")

    if astro:
        lines.append("🌌 <b>Астрособытия</b>\n" + " | ".join(astro))

    lines += [
        "———",
        f"📜 <b>Вывод</b>\n{summary}",
        "———",
        "✅ <b>Рекомендации</b>",
        *[f"• {t}" for t in tips],
        "———",
        f"📚 {get_fact(TOMORROW)}"
    ]

    return "\n".join(lines)

# ─────────── 7. SEND ───────────────────────────────────────────────
async def main():
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    bot = Bot(TOKEN)
    html = build_msg()
    logging.info("Preview: %s", html.replace("\n"," | ")[:200])
    try:
        await bot.send_message(int(CHAT), html, parse_mode="HTML", disable_web_page_preview=True)
        logging.info("Sent ✓")
    except tg_err.TelegramError as e:
        logging.error("Telegram error: %s", e)
        raise

if __name__ == "__main__":
    asyncio.run(main())
