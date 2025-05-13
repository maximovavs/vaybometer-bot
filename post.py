#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
post.py

1) build_msg() – собирает HTML-прогноз
2) main() – отправляет:
   • сам прогноз
   • по пятницам – опрос
   • каждые 3 дня – фото с Unsplash
"""

import os
import asyncio
import logging
import datetime as _dt
import pendulum
import requests

from telegram import Bot, error as tg_err

from utils import (
    compass, clouds_word, wind_phrase, safe,
    get_fact, WEATHER_ICONS, AIR_EMOJI
)
from weather import get_weather
from air import get_air, get_pollen, get_sst, get_kp
from schumann import get_schumann
from astro import astro_events
from gpt import gpt_blurb

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

# ─────────── 0. CONST ────────────────────────────────────────────
TZ           = pendulum.timezone("Asia/Nicosia")
TODAY        = pendulum.now(TZ).date()
TOMORROW     = TODAY + pendulum.duration(days=1)
TOKEN        = os.environ["TELEGRAM_TOKEN"]
CHAT         = os.environ["CHANNEL_ID"]
UNSPLASH_KEY = os.getenv("UNSPLASH_KEY")

# опрос
POLL_QUESTION = "Как сегодня ваше самочувствие? 🤔"
POLL_OPTIONS  = ["🔥 Энергия", "🙂 Нормально", "😴 Вялый", "🤒 Плохо"]

# для рейтинга городов
CITIES = {
    "Limassol": (34.707, 33.022),
    "Larnaca" : (34.916, 33.624),
    "Nicosia" : (35.170, 33.360),
    "Pafos"   : (34.776, 32.424),
}


def build_msg() -> str:
    """Собирает HTML-прогноз на завтра."""
    # 1) Погода для Лимассола
    w = get_weather(*CITIES["Limassol"])
    if not w:
        raise RuntimeError("Источники погоды недоступны")

    if "current" in w:
        cur       = w["current"]
        day_temp  = w["daily"][0]["temp"]
        wind_kmh  = cur["wind_speed"] * 3.6
        wind_deg  = cur["wind_deg"]
        press     = cur["pressure"]
        cloud     = clouds_word(cur.get("clouds", 0))
        day_max   = day_temp["max"]
        night_min = day_temp["min"]
    else:
        cw        = w["current_weather"]
        wind_kmh  = cw["windspeed"]
        wind_deg  = cw["winddirection"]
        press     = cw["pressure"]
        cloud     = clouds_word(w["hourly"]["cloud_cover"][0])
        blk       = w["daily"][0] if isinstance(w["daily"], list) else w["daily"]
        day_max   = blk["temperature_2m_max"][0]
        night_min = blk["temperature_2m_min"][0]

    strong = w.get("strong_wind", False)
    fog    = w.get("fog_alert",   False)

    # 2) Рейтинг городов (днём/ночью)
    temps = {}
    for city, (la, lo) in CITIES.items():
        w2 = get_weather(la, lo)
        if not w2:
            continue
        if "current" in w2:
            tmax = w2["daily"][0]["temp"]["max"]
            tmin = w2["daily"][0]["temp"]["min"]
        else:
            b2   = w2["daily"][0] if isinstance(w2["daily"], list) else w2["daily"]
            tmax = b2["temperature_2m_max"][0]
            tmin = b2["temperature_2m_min"][0]
        temps[city] = (tmax, tmin)

    warm = max(temps, key=lambda c: temps[c][0])
    cold = min(temps, key=lambda c: temps[c][1])

    # 3) Остальные блоки
    air    = get_air() or {}
    pollen = get_pollen()
    kp, kp_state = get_kp()
    sch    = get_schumann()
    sst    = get_sst()
    astro  = astro_events()

    # 4) Виновник + советы
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

    icon = WEATHER_ICONS.get(cloud, "🌦️")

    # 5) Сборка финального текста
    lines = [
        f"{icon} <b>Погода на завтра в Лимассоле {TOMORROW.format('DD.MM.YYYY')}</b>",
        f"<b>Темп. (дн/ночь):</b> {day_max:.1f}/{night_min:.1f} °C",
        f"<b>Давление:</b> {press:.0f} гПа",
        f"<b>Рейтинг (дн/ночь):</b> {warm} {temps[warm][0]:.1f}/{temps[warm][1]:.1f} °C | "
        f"{cold} {temps[cold][0]:.1f}/{temps[cold][1]:.1f} °C",
        "———",
        f"<b>Облачность:</b> {cloud}",
        f"<b>Ветер:</b> {wind_phrase(wind_kmh)} ({wind_kmh:.1f} км/ч, {compass(wind_deg)})",
        *(["⚠️ Ветер усилится"] if strong else []),
        *(["🌁 Возможен туман"] if fog else []),
        "———",
        "🏙️ <b>Качество воздуха</b>",
        f"{AIR_EMOJI.get(air.get('lvl'),'⚪')} AQI {air.get('aqi','—')} | "
        f"PM2.5: {safe(air.get('pm25'),' µg/м³')} | PM10: {safe(air.get('pm10'),' µg/м³')}",
    ]

    if pollen:
        idx = lambda v: ["нет","низкий","умеренный","высокий","оч. высокий","экстрим"][int(round(v))]
        lines += [
            "🌿 <b>Пыльца</b>",
            f"Деревья — {idx(pollen['treeIndex'])} | "
            f"Травы — {idx(pollen['grassIndex'])} | "
            f"Сорняки — {idx(pollen['weedIndex'])}",
        ]

    lines += [
        "———",
        "🧲 <b>Геомагнитка</b>",
        f"Kp={kp:.1f} ({kp_state})" if kp is not None else "нет данных",
    ]

    if sch.get("high"):
        lines += ["🎵 <b>Шуман:</b> ⚡️ вибрации повышены"]
    elif "freq" in sch:
        lines += [f"🎵 <b>Шуман:</b> ≈{sch['freq']:.1f} Гц"]
    else:
        lines += [f"🎵 <b>Шуман:</b> {sch.get('msg','—')}"]

    if sst is not None:
        lines += [f"🌊 <b>Вода:</b> {sst:.1f} °C"]

    if astro:
        lines += ["🔮 <b>Астрособытия</b>", " | ".join(astro)]

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


async def send_main(bot: Bot) -> None:
    msg = build_msg()
    logging.info("Preview: %s", msg.replace("\n"," | ")[:200])
    try:
        await bot.send_message(int(CHAT), msg,
                               parse_mode="HTML",
                               disable_web_page_preview=True)
    except tg_err.TelegramError as e:
        logging.error("Send error: %s", e)


async def send_friday_poll(bot: Bot) -> None:
    if pendulum.now(TZ).is_friday():
        try:
            await bot.send_poll(int(CHAT), POLL_QUESTION, POLL_OPTIONS,
                                is_anonymous=False,
                                allows_multiple_answers=False)
        except tg_err.TelegramError as e:
            logging.warning("Poll error: %s", e)


async def send_photo(bot: Bot) -> None:
    # раз в 3 дня по UTC
    if not UNSPLASH_KEY or _dt.datetime.utcnow().toordinal() % 3 != 0:
        return
    try:
        res = requests.get(
            "https://api.unsplash.com/photos/random",
            params={"query":"cyprus coast sunset","client_id":UNSPLASH_KEY},
            timeout=15
        ).json()
        url = res.get("urls",{}).get("regular")
        if url:
            await bot.send_photo(int(CHAT), photo=url,
                                 caption="Фото дня • Unsplash")
    except Exception as e:
        logging.warning("Photo error: %s", e)


async def main() -> None:
    bot = Bot(TOKEN)
    await send_main(bot)
    await send_friday_poll(bot)
    await send_photo(bot)
    logging.info("All done ✓")


if __name__ == "__main__":
    asyncio.run(main())
