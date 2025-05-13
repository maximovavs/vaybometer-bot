#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import asyncio
import logging
import random
import datetime as dt

import pendulum
from telegram import Bot, error as tg_err

from utils    import compass, clouds_word, wind_phrase, safe, get_fact
from weather  import get_weather
from air      import get_air, get_pollen, get_sst, get_kp
from schumann import get_schumann
from astro    import astro_events
from gpt      import gpt_blurb

# ─────────── КОНСТАНТЫ ─────────────────────────────────────────
LAT, LON = 34.707, 33.022
CITIES   = {
    "Limassol": (34.707, 33.022),
    "Larnaca" : (34.916, 33.624),
    "Nicosia" : (35.170, 33.360),
    "Pafos"   : (34.776, 32.424),
}

TOKEN     = os.environ["TELEGRAM_TOKEN"]
CHAT      = os.environ["CHANNEL_ID"]

TZ        = pendulum.timezone("Asia/Nicosia")
TODAY     = pendulum.now(TZ).date()
TOMORROW  = TODAY.add(days=1)

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")


# ─────────── СБОРКА СООБЩЕНИЯ ────────────────────────────────────
def build_msg() -> str:
    w = get_weather(LAT, LON)
    if not w:
        raise RuntimeError("Источники погоды недоступны")

    # разбираем OpenWeather vs Open-Meteo
    if "current" in w:
        # ─ OpenWeather ─
        cur       = w["current"]
        day_block = w["daily"][0]["temp"]
        wind_kmh  = cur["wind_speed"] * 3.6
        wind_deg  = cur["wind_deg"]
        wcode     = cur.get("weather",[{"id":0}])[0]["id"]
        press     = cur["pressure"]
        cloud_w   = clouds_word(cur.get("clouds", 0))
        day_max   = day_block["max"]
        night_min = day_block["min"]
    else:
        # ─ Open-Meteo ─
        cur        = w["current_weather"]
        dblock     = w["daily"]
        wind_kmh   = cur["windspeed"]
        wind_deg   = cur["winddirection"]
        press      = cur["pressure"]
        cloud_w    = clouds_word(cur.get("clouds", 0))

        # завтрашний прогноз в daily[ ] 
        day_max   = dblock["temperature_2m_max"][1] if len(dblock["temperature_2m_max"])>1 else dblock["temperature_2m_max"][0]
        night_min = dblock["temperature_2m_min"][1] if len(dblock["temperature_2m_min"])>1 else dblock["temperature_2m_min"][0]
        wcode     = dblock["weathercode"][1]    if len(dblock["weathercode"])>1    else dblock["weathercode"][0]

    # флаги
    strong_wind = w.get("strong_wind", False)
    fog_alert   = w.get("fog_alert",   False)

    # диапазон по городам
    temps = {}
    for city,(la,lo) in CITIES.items():
        wc = get_weather(la, lo)
        if not wc: continue
        if "current" in wc:
            temps[city] = wc["daily"][0]["temp"]["max"]
        else:
            arr = wc["daily"]["temperature_2m_max"]
            temps[city] = arr[1] if len(arr)>1 else arr[0]
    warm = max(temps, key=temps.get)
    cold = min(temps, key=temps.get)

    # воздух / пыльца / kp / schumann / sst / astro
    air     = get_air() or {}
    pollen  = get_pollen()
    kp_val, kp_state = get_kp()
    sch     = get_schumann()
    sst     = get_sst()
    astro   = astro_events()

    # выбираем «виновника»
    if fog_alert:
        culprit = "туман"
    elif kp_state == "буря":
        culprit = "магнитные бури"
    elif press < 1007:
        culprit = "низкое давление"
    elif strong_wind:
        culprit = "шальной ветер"
    else:
        culprit = "мини-парад планет"
    summary, tips = gpt_blurb(culprit)

    # иконка по облачности
    ICONS = {"ясно":"☀️","переменная":"🌤️","пасмурно":"☁️"}
    icon = ICONS.get(cloud_w, "🌦️")

    # собираем блоки
    P = [
        f"{icon} <b>Погода на завтра в Лимассоле {TOMORROW.format('DD.MM.YYYY')}</b>",
        f"<b>Темп. днём:</b> до {day_max:.1f} °C",
        f"<b>Темп. ночью:</b> около {night_min:.1f} °C",
        f"<b>Облачность:</b> {cloud_w}",
        f"<b>Ветер:</b> {wind_phrase(wind_kmh)} ({wind_kmh:.1f} км/ч, {compass(wind_deg)})",
        *(["⚠️ Ветер может усилиться"] if strong_wind else []),
        *(["🌁 Возможен туман, водите аккуратно"] if fog_alert else []),
        f"<b>Давление:</b> {press:.0f} гПа",
        f"<i>Самый тёплый город:</i> {warm} ({temps[warm]:.1f} °C)",
        f"<i>Самый прохладный город:</i> {cold} ({temps[cold]:.1f} °C)",
        "———",
        "🏙️ <b>Качество воздуха</b>",
        f"{air.get('emoji','⚪️')} AQI {air.get('aqi','—')} | PM2.5: {safe(air.get('pm25','—'),' µg/м³')} | PM10: {safe(air.get('pm10','—'),' µg/м³')}",
    ]

    if pollen:
        idx = lambda v: ["нет","низкий","умеренный","высокий","оч. высокий","экстрим"][int(round(v))]
        P += [
            "🌿 <b>Пыльца</b>",
            f"Деревья — {idx(pollen['treeIndex'])} | Травы — {idx(pollen['grassIndex'])} | Сорняки — {idx(pollen['weedIndex'])}",
        ]

    P += [
        "🧲 <b>Геомагнитная активность</b>",
        f"K-index: {kp_val:.1f} ({kp_state})" if kp_val is not None else "нет данных",
    ]

    if sch.get("high"):
        P += ["🎵 <b>Шуман:</b> ⚡️ вибрации повышены (>8 Гц)"]
    elif sch.get("freq") is not None:
        P += [f"🎵 <b>Шуман:</b> ≈{sch['freq']:.1f} Гц, амплитуда стабильна"]
    else:
        P += [f"🎵 <b>Шуман:</b> {sch.get('msg','нет данных')}"]

    if sst is not None:
        P += [f"🌊 <b>Температура воды</b>\nСейчас: {sst:.1f} °C"]

    if astro:
        P += ["🌌 <b>Астрологические события</b>\n" + " | ".join(astro)]

    P += [
        "———",
        f"📜 <b>Вывод</b>\n{summary}",
        "———",
        "✅ <b>Рекомендации</b>",
        *[f"• {t}" for t in tips],
        "———",
        f"📚 {get_fact(TOMORROW)}",
    ]

    return "\n".join(P)


# ─────────── MAIN ────────────────────────────────────────────────
async def main() -> None:
    logging.info("Preview: %s", build_msg().replace("\n"," | ")[:200])
    bot = Bot(TOKEN)
    try:
        await bot.send_message(int(CHAT), build_msg()[:4096],
                               parse_mode="HTML",
                               disable_web_page_preview=True)
        logging.info("Sent ✓")
    except tg_err.TelegramError as e:
        logging.error("Telegram error: %s", e)
        raise

if __name__ == "__main__":
    asyncio.run(main())
