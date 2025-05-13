#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import asyncio
import logging
import datetime as dt
import pendulum
from telegram import Bot, error as tg_err

# 1. Импорт утилит из своих модулей
from utils import compass, clouds_word, wind_phrase, safe, get_fact
from weather import get_weather
from air_pollen_sst_kp import get_air, get_pollen, get_sst, get_kp
from schumann import get_schumann
from astro import astro_events
from gpt import gpt_blurb

# 2. Константы и настройки
LAT, LON = 34.707, 33.022
CITIES = {
    "Limassol": (34.707, 33.022),
    "Larnaca" : (34.916, 33.624),
    "Nicosia" : (35.170, 33.360),
    "Pafos"   : (34.776, 32.424),
}

TOKEN     = os.environ["TELEGRAM_TOKEN"]
CHAT      = os.environ["CHANNEL_ID"]
TZ        = pendulum.timezone("Asia/Nicosia")
TODAY     = pendulum.now(TZ).date()
TOMORROW  = TODAY + pendulum.duration(days=1)

WEATHER_ICONS = {
    "ясно": "☀️", "переменная": "🌤️", "пасмурно": "☁️", "дождь": "🌧️", "туман": "🌁"
}

AIR_EMOJI = {
    "good": "🟢", "moderate": "🟡", "unhealthy": "🟠",
    "very unhealthy": "🔴", "hazardous": "⚫",
}

# 3. Собираем текст сообщения
def build_msg() -> str:
    w = get_weather(LAT, LON)
    if not w:
        raise RuntimeError("Источники погоды недоступны")

    # —— 3.A Погода для Лимассола (объединённый интерфейс) ——
    if "current" in w:
        cur       = w["current"]
        day       = w["daily"][0]["temp"]
        wind_kmh  = cur["wind_speed"] * 3.6
        wind_deg  = cur["wind_deg"]
        code      = cur.get("weather", [{}])[0].get("id", 0)
        press     = cur["pressure"]
        cloud_w   = clouds_word(cur.get("clouds", 0))
        day_max   = day["max"]
        night_min = day["min"]
        strong    = cur.get("strong_wind", False)
        fog       = False
    else:
        cur       = w["current_weather"]
        dblock    = w["daily"]
        wind_kmh  = cur["windspeed"]
        wind_deg  = cur["winddirection"]
        press     = w["hourly"]["surface_pressure"][0]
        code      = (dblock["weathercode"][1] if len(dblock["weathercode"])>1 else dblock["weathercode"][0])
        cloud_w   = clouds_word(w["hourly"]["cloud_cover"][0])
        day_max   = dblock["temperature_2m_max"][1] if len(dblock["temperature_2m_max"])>1 else dblock["temperature_2m_max"][0]
        night_min = dblock["temperature_2m_min"][1] if len(dblock["temperature_2m_min"])>1 else dblock["temperature_2m_min"][0]
        strong    = w.get("strong_wind", False)
        fog       = w.get("fog_alert", False)

    # —— 3.B Температурные мини-лидеры по городам ——
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

    # —— 3.C Остальные блоки — воздух / пыльца / kp / schumann / sst / astro ——
    air    = get_air() or {}
    pollen = get_pollen()
    kp, kp_state = get_kp()
    sch    = get_schumann()
    sst    = get_sst()
    astro  = astro_events()

    # —— 3.D Выбор «виновника» и GPT-подсказки ——
    if fog:
        culprit = "туман"
    elif kp_state == "буря":
        culprit = "магнитные бури"
    elif press < 1007:
        culprit = "низкое давление"
    elif strong:
        culprit = "шальной ветер"
    else:
        culprit = "мини-парад планет"
    summary, tips = gpt_blurb(culprit)

    # —— 3.E Сборка финального списка строк ——
    lines = [
        f"{WEATHER_ICONS.get(cloud_w,'🌦️')} <b>Погода на завтра в Лимассоле {TOMORROW.format('DD.MM.YYYY')}</b>",
        f"<b>Темп. днём:</b> до {day_max:.1f}°C",
        f"<b>Темп. ночью:</b> около {night_min:.1f}°C",
        f"<b>Облачность:</b> {cloud_w}",
        f"<b>Ветер:</b> {wind_phrase(wind_kmh)} ({wind_kmh:.1f} км/ч, {compass(wind_deg)})",
        *(["⚠️ Ветер может усилиться"] if strong else []),
        *(["🌁 Возможен туман, водите аккуратно"] if fog else []),
        f"<b>Давление:</b> {press:.0f} гПа",
        f"<i>Самый тёплый город:</i> {warm} ({temps[warm]:.1f}°C)",
        f"<i>Самый прохладный город:</i> {cold} ({temps[cold]:.1f}°C)",
        "———",
        "🏙️ <b>Качество воздуха</b>",
        f"{AIR_EMOJI.get(air.get('lvl'),'⚪')} AQI {safe(air.get('aqi'),'')} | PM2.5: {safe(air.get('pm25'),' µg/м³')} | PM10: {safe(air.get('pm10'),' µg/м³')}",
    ]

    if pollen:
        idx = lambda v: ["нет","низкий","умеренный","высокий","оч. высокий","экстрим"][int(round(v))]
        lines += [
            "🌿 <b>Пыльца</b>",
            f"Деревья — {idx(pollen['treeIndex'])} | Травы — {idx(pollen['grassIndex'])} | Сорняки — {idx(pollen['weedIndex'])}",
        ]

    if kp is not None:
        lines += ["🧲 <b>Геомагнитная активность</b>", f"K-index: {kp:.1f} ({kp_state})"]
    else:
        lines += ["🧲 <b>Геомагнитная активность</b>", "нет данных"]

    if sch.get("high"):
        lines += ["🎵 <b>Шуман:</b> ⚡️ вибрации повышены (>8 Гц)"]
    elif "freq" in sch:
        lines += [f"🎵 <b>Шуман:</b> ≈{sch['freq']:.1f} Гц, амплитуда стабильна"]
    else:
        lines += [f"🎵 <b>Шуман:</b> {sch.get('msg','нет данных')}"]

    if sst is not None:
        lines += [f"🌊 <b>Температура воды</b>\nСейчас: {sst:.1f}°C"]

    if astro:
        lines += ["🌌 <b>Астрологические события</b>\n" + " | ".join(astro)]

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

# 4. Отправка и развлечение
async def main():
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    bot  = Bot(TOKEN)
    html = build_msg()
    logging.info("Preview: %s", html.replace("\n"," | ")[:200])

    # основной пост
    await bot.send_message(int(CHAT), html, parse_mode="HTML", disable_web_page_preview=True)
    # опрос в пятницу и фото раз в 3 дня — если подключены ключи (по необходимости)

if __name__ == "__main__":
    asyncio.run(main())
