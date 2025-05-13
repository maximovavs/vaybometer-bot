#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
post.py — объединяет все модули и шлёт Telegram-сообщение с дайджестом
"""

import os, asyncio, logging, random, dt as _dt
import pendulum
from telegram import Bot, error as tg_err

from utils import compass, clouds_word, wind_phrase, safe, get_fact
from weather import get_weather
from air import get_air, get_pollen, get_sst, get_kp
from schumann import get_schumann
from astro import astro_events
from gpt import gpt_blurb, FACTS

# ─────────── 0.  КОНСТАНТЫ ─────────────────────────────────────────
LAT, LON = 34.707, 33.022
CITIES = {
    "Limassol": (34.707, 33.022),
    "Larnaca" : (34.916, 33.624),
    "Nicosia" : (35.170, 33.360),
    "Pafos"   : (34.776, 32.424),
}

TOKEN      = os.environ["TELEGRAM_TOKEN"]
CHAT_ID    = int(os.environ["CHANNEL_ID"])
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

AIR_EMOJI = {
    "хороший":           "🟢",
    "умеренный":         "🟡",
    "вредный для чувствительных": "🟠",
    "вредный":           "🔴",
    "оч. вредный":       "🟣",
    "опасный":           "⚫",
    "н/д":               "⚪️",
}

logging.basicConfig(level=logging.INFO,
                    format="%(levelname)s: %(message)s")


# ─────────── 6.  BUILD MESSAGE ────────────────────────────────────
def build_msg() -> str:
    # 1. Погода в Лимассоле
    w = get_weather(LAT, LON)
    if not w:
        raise RuntimeError("Источники погоды недоступны")

    # различаем OpenWeather и Open-Meteo
    if "current" in w:
        cur       = w["current"]
        daily     = w["daily"][0]["temp"]
        wind_kmh  = cur.get("wind_speed", 0) * 3.6
        wind_deg  = cur.get("wind_deg", 0)
        wcode     = cur.get("weather",[{"id":0}])[0]["id"]
        press     = cur.get("pressure",
                       w.get("hourly", {}).get("surface_pressure",[None])[0])
        cloud_w   = clouds_word(cur.get("clouds",0))
        day_max   = daily.get("max",0)
        night_min = daily.get("min",0)
    else:
        cur       = w["current_weather"]
        dblock    = w["daily"]
        wind_kmh  = cur.get("windspeed",0)
        wind_deg  = cur.get("winddirection",0)
        press     = cur.get("pressure",
                       w.get("hourly",{}).get("surface_pressure",[None])[0])
        cloud_w   = clouds_word(cur.get("clouds",
                       w.get("hourly",{}).get("cloud_cover",[0])[0]))
        # завтрашние
        tmax = dblock.get("temperature_2m_max",[])
        tmin = dblock.get("temperature_2m_min",[])
        code= dblock.get("weathercode",[])
        day_max   = tmax[1] if len(tmax)>1 else (tmax[0] if tmax else 0)
        night_min = tmin[1] if len(tmin)>1 else (tmin[0] if tmin else 0)
        wcode     = code[1] if len(code)>1 else (code[0] if code else 0)

    strong_wind = wind_kmh > 30
    fog_alert   = wcode in (45, 48)

    # 2. Температурные лидеры
    temps = {}
    for city,(la,lo) in CITIES.items():
        ww = get_weather(la,lo)
        if not ww: continue
        if "current" in ww:
            temps[city] = ww["daily"][0]["temp"]["max"]
        else:
            arr = ww["daily"].get("temperature_2m_max",[])
            temps[city] = arr[1] if len(arr)>1 else (arr[0] if arr else 0)
    warm = max(temps,key=temps.get)
    cold = min(temps,key=temps.get)

    # 3. Воздух, пыльца, kp, sst, шуман
    air     = get_air() or {}
    pollen  = get_pollen()
    kp_val, kp_state = get_kp()
    sst     = get_sst()
    sch     = get_schumann()
    astro   = astro_events()

    # 4. Виновник дня + советы
    if fog_alert:
        culprit = "туман"
    elif kp_state=="буря":
        culprit = "магнитные бури"
    elif press is not None and press<1007:
        culprit = "низкое давление"
    elif strong_wind:
        culprit = "шальной ветер"
    else:
        culprit = "мини-парад планет"
    summary, tips = gpt_blurb(culprit)

    # 5. Собираем строки
    icon = WEATHER_ICONS.get(cloud_w,"🌦️")
    lines = [
        f"{icon} <b>Погода на завтра в Лимассоле {TOMORROW.format('DD.MM.YYYY')}</b>",
        f"<b>Темп. днём:</b> до {day_max:.1f} °C",
        f"<b>Темп. ночью:</b> около {night_min:.1f} °C",
        f"<b>Облачность:</b> {cloud_w}",
        f"<b>Ветер:</b> {wind_phrase(wind_kmh)} ({wind_kmh:.1f} км/ч, {compass(wind_deg)})",
        *(["⚠️ Ветер может усиливаться"] if strong_wind else []),
        *(["🌁 Возможен туман, водите аккуратно"] if fog_alert else []),
        f"<b>Давление:</b> {safe(press,' гПа')}",
        f"<i>Самый тёплый город:</i> {warm} ({temps[warm]:.1f} °C)",
        f"<i>Самый прохладный город:</i> {cold} ({temps[cold]:.1f} °C)",
        "———",
        "🏙️ <b>Качество воздуха</b>",
        f"{AIR_EMOJI.get(air.get('lvl','н/д'))} AQI {air.get('aqi','—')} | PM2.5: {safe(air.get('pm25'))} | PM10: {safe(air.get('pm10'))}",
    ]
    if pollen:
        idx = lambda v:["нет","низкий","умеренный","высокий","оч. высокий","экстрим"][int(round(v))]
        lines += [
            "🌿 <b>Пыльца</b>",
            f"Деревья — {idx(pollen['treeIndex'])} | Травы — {idx(pollen['grassIndex'])} | Сорняки — {idx(pollen['weedIndex'])}"
        ]
    if kp_val is not None:
        lines += ["🧲 <b>Геомагнит.</b>", f"K-index: {kp_val:.1f} ({kp_state})"]
    else:
        lines += ["🧲 <b>Геомагнит.</b>", "нет данных"]
    if sch.get("high"):
        lines += ["🎵 <b>Шуман:</b> ⚡️ вибрации повышены (>8 Гц)"]
    elif "freq" in sch:
        lines += [f"🎵 <b>Шуман:</b> ≈{sch['freq']:.1f} Гц, амплитуда стабильна"]
    else:
        lines += [f"🎵 <b>Шуман:</b> {sch.get('msg','нет данных')}"]
    if sst is not None:
        lines += [f"🌊 <b>Темп. воды</b>\nСейчас: {sst:.1f} °C"]
    if astro:
        lines += ["🌌 <b>Астрособытия</b>\n" + " | ".join(astro)]
    lines += [
        "———",
        f"📜 <b>Вывод</b>\n{summary}",
        "———",
        "✅ <b>Рекомендации</b>",
        *[f"• {t}" for t in tips],
        "———",
        f"📚 {get_fact(TOMORROW)}",
    ]

    return "\n".join(lines)


# ─────────── 7.  MAIN ─────────────────────────────────────────────
async def main():
    bot = Bot(TOKEN)
    html = build_msg()
    logging.info("Preview: %s", html.replace("\n"," | ")[:200])
    try:
        await bot.send_message(CHAT_ID, html[:4096],
                               parse_mode="HTML", disable_web_page_preview=True)
        logging.info("Message sent ✓")
    except tg_err.TelegramError as e:
        logging.error("Telegram error: %s", e)
        raise


if __name__ == "__main__":
    asyncio.run(main())
