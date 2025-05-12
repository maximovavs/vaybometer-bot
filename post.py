#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
VayboMeter v5.x — главный скрипт для сборки и отправки поста.

Импортирует:
  • utils.py      — общие функции (compass, clouds_word, wind_phrase, safe, get_fact)
  • weather.py    — get_weather() + флаги strong_wind, fog_alert
  • air.py        — get_air(), get_pollen(), get_sst(), get_kp()
  • schumann.py   — get_schumann()
  • astro.py      — astro_events()
  • gpt.py        — gpt_blurb()
"""

import os
import random
import asyncio
import logging

import pendulum
from telegram import Bot, error as tg_err

from utils      import compass, clouds_word, wind_phrase, safe, get_fact
from weather    import get_weather
from air        import get_air, get_pollen, get_sst, get_kp
from schumann   import get_schumann
from astro      import astro_events
from gpt        import gpt_blurb

# ─────────── 0.  КОНСТАНТЫ ───────────────────────────────────────
LAT, LON = 34.707, 33.022
CITIES = {
    "Limassol": (34.707, 33.022),
    "Larnaca" : (34.916, 33.624),
    "Nicosia" : (35.170, 33.360),
    "Pafos"   : (34.776, 32.424),
}

TOKEN = os.environ["TELEGRAM_TOKEN"]
CHAT  = os.environ["CHANNEL_ID"]

TZ       = pendulum.timezone("Asia/Nicosia")
TODAY    = pendulum.now(TZ).date()
TOMORROW = TODAY.add(days=1)

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
    "опасный":           "🟤",
    "н/д":               "⚪️",
}

UNSPLASH_KEY   = os.getenv("UNSPLASH_KEY")
POLL_QUESTION  = "Как сегодня ваше самочувствие? 🤔"
POLL_OPTIONS   = [
    "🔥 Полон(а) энергии",
    "🙂 Всё нормально",
    "😴 Слегка вялый(ая)",
    "🤒 Всё плохо",
]

logging.basicConfig(level=logging.INFO,
                    format="%(levelname)s: %(message)s")

# ─────────── 1.  СБОРКА МЕССЕДЖА ─────────────────────────────────
def build_msg() -> str:
    # 1. Погода
    w = get_weather(LAT, LON)
    if not w:
        raise RuntimeError("Источники погоды недоступны")

    if "current" in w:
        # OpenWeather One Call
        cur       = w["current"]
        day_block = w["daily"][0]["temp"]
        wind_kmh  = cur["wind_speed"] * 3.6
        wind_deg  = cur["wind_deg"]
        wcode     = cur.get("weather",[{}])[0].get("id",0)
        press     = cur["pressure"]
        cloud_w   = clouds_word(cur.get("clouds",0))
        day_max   = day_block["max"]
        night_min = day_block["min"]
    else:
        # Open-Meteo
        cw        = w["current_weather"]
        dblock    = w["daily"]
        wind_kmh  = cw["windspeed"]
        wind_deg  = cw["winddirection"]
        press     = w["pressure"]
        cloud_w   = cw["clouds"]

        # завтрашние значения (forecast_days=2)
        tm = dblock["temperature_2m_max"]
        tn = dblock["temperature_2m_min"]
        wc = dblock["weathercode"]
        day_max   = tm[1] if len(tm)>1 else tm[0]
        night_min = tn[1] if len(tn)>1 else tn[0]
        wcode     = wc[1] if len(wc)>1 else wc[0]

    strong_wind = w["strong_wind"]
    fog_alert   = w["fog_alert"]

    # 2. Тёплый/прохладный город
    temps = {}
    for city,(la,lo) in CITIES.items():
        wc = get_weather(la,lo)
        if not wc: continue
        if "current" in wc:
            temps[city] = wc["daily"][0]["temp"]["max"]
        else:
            arr = wc["daily"]["temperature_2m_max"]
            temps[city] = arr[1] if len(arr)>1 else arr[0]
    warm = max(temps, key=temps.get)
    cold = min(temps, key=temps.get)

    # 3. AQI / PM / Pollen / Kp / SST / Schumann / Astro
    air     = get_air() or {}
    pollen  = get_pollen()
    kp, kp_state = get_kp()
    sst     = get_sst()
    sch     = get_schumann()
    astro_list = astro_events()

    # 4. Виновник + советы
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

    icon = WEATHER_ICONS.get(cloud_w, "🌦️")

    # 5. Собираем все строчки
    P = [
        f"{icon} <b>Погода на завтра в Лимассоле {TOMORROW.format('DD.MM.YYYY')}</b>",
        f"<b>Темп. днём:</b> до {day_max:.1f}°C",
        f"<b>Темп. ночью:</b> около {night_min:.1f}°C",
        f"<b>Облачность:</b> {cloud_w}",
        f"<b>Ветер:</b> {wind_phrase(wind_kmh)} ({wind_kmh:.1f} км/ч, {compass(wind_deg)})",
        *(["⚠️ Ветер может усиливаться"] if strong_wind else []),
        *(["🌁 Возможен туман, водите аккуратно"] if fog_alert else []),
        f"<b>Давление:</b> {press:.0f} гПа",
        f"<i>Самый тёплый город:</i> {warm} ({temps[warm]:.1f}°C)",
        f"<i>Самый прохладный город:</i> {cold} ({temps[cold]:.1f}°C)",
        "———",
        "🏙️ <b>Качество воздуха</b>",
        f"{AIR_EMOJI.get(air.get('lvl'),'⚪️')} AQI {air.get('aqi','—')} | PM2.5: {safe(air.get('pm25'),' µg/м³')} | PM10: {safe(air.get('pm10'),' µg/м³')}",
    ]

    if pollen:
        idx = lambda v: ["нет","низкий","умеренный","высокий","оч. высокий","экстрим"][int(round(v))]
        P += [
            "🌿 <b>Пыльца</b>",
            f"Деревья — {idx(pollen['treeIndex'])} | Травы — {idx(pollen['grassIndex'])} | Сорняки — {idx(pollen['weedIndex'])}"
        ]

    if kp is not None:
        P += [ "🧲 <b>Геомагнитная активность</b>",
               f"K-index: {kp:.1f} ({kp_state})" ]
    else:
        P += [ "🧲 <b>Геомагнитная активность</b>", "нет данных" ]

    if sch.get("high"):
        P += ["🎵 <b>Шуман:</b> ⚡️ вибрации повышены (>8 Гц)"]
    elif "freq" in sch:
        P += [ f"🎵 <b>Шуман:</b> ≈{sch['freq']:.1f} Гц, амплитуда стабильна" ]
    else:
        P += [ f"🎵 <b>Шуман:</b> {sch.get('msg','нет данных')}" ]

    if sst is not None:
        P += [ f"🌊 <b>Температура воды</b>\nСейчас: {sst:.1f}°C" ]

    if astro_list:
        P += [ "🌌 <b>Астрологические события</b>\n" + " | ".join(astro_list) ]

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

# ─────────── 2.  SEND & EXTRA ────────────────────────────────────
async def send_main_post(bot: Bot, text: str) -> None:
    await bot.send_message(int(CHAT),
                           text[:4096],
                           parse_mode="HTML",
                           disable_web_page_preview=True)

async def send_friday_poll(bot: Bot) -> None:
    await bot.send_poll(int(CHAT),
                        question=POLL_QUESTION,
                        options=POLL_OPTIONS,
                        is_anonymous=False,
                        allows_multiple_answers=False)

async def fetch_unsplash_photo() -> str | None:
    if not UNSPLASH_KEY:
        return None
    j = await asyncio.get_event_loop().run_in_executor(
        None,
        lambda: __import__('utils')._get(
            "https://api.unsplash.com/photos/random",
            query="cyprus coast sunset",
            client_id=UNSPLASH_KEY
        )
    )
    return j and j.get("urls",{}).get("regular")

async def send_media(bot: Bot, photo_url: str) -> None:
    await bot.send_photo(int(CHAT), photo=photo_url, caption="Фото дня • Unsplash")

# ─────────── main ───────────────────────────────────────────────
async def main() -> None:
    logging.info("Starting build_msg()…")
    html = build_msg()
    logging.info("Preview: %s", html.replace("\n"," | ")[:200])

    bot = Bot(TOKEN)
    await send_main_post(bot, html)

    # По пятницам — опрос
    if pendulum.now(TZ).is_friday():
        await send_friday_poll(bot)

    # Каждые 3 дня — картинка
    if UNSPLASH_KEY and (pendulum.now(TZ).day % 3 == 0):
        if photo := await fetch_unsplash_photo():
            await send_media(bot, photo)

    logging.info("All messages sent ✓")

if __name__ == "__main__":
    asyncio.run(main())
