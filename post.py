#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import asyncio
import logging
import random
import pendulum
from telegram import Bot, error as tg_err

from utils import (
    compass, clouds_word, wind_phrase, safe,
    get_fact, WEATHER_ICONS, AIR_EMOJI, aqi_color
)
from weather import get_weather
from air import get_air, get_pollen, get_sst, get_kp
from schumann import get_schumann
from astro import astro_events
from gpt import gpt_blurb

# ─────────── Настройка логирования ─────────────────────────────────────
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

# ─────────── Константы ─────────────────────────────────────────────────
TZ         = pendulum.timezone("Asia/Nicosia")
TODAY      = pendulum.now(TZ).date()
TOMORROW   = TODAY.add(days=1)

# География
LAT, LON   = 34.707, 33.022
CITIES     = {
    "Limassol": (34.707, 33.022),
    "Larnaca" : (34.916, 33.624),
    "Nicosia" : (35.170, 33.360),
    "Pafos"   : (34.776, 32.424),
}

# Telegram
TOKEN      = os.environ["TELEGRAM_TOKEN"]
CHAT_ID    = int(os.environ["CHANNEL_ID"])

# Опционально: фото с Unsplash
UNSPLASH_KEY = os.getenv("UNSPLASH_KEY")

# Опрос
POLL_QUESTION = "Как сегодня ваше самочувствие? 🤔"
POLL_OPTIONS  = ["🔥 Полон(а) энергии","🙂 Нормально","😴 Слегка вялый(ая)","🤒 Всё плохо"]


# ─────────── Функция сборки сообщения ─────────────────────────────────
def build_msg() -> str:
    P: list[str] = []

    # 1) Погода в Лимассоле
    w = get_weather(LAT, LON)
    if not w:
        raise RuntimeError("Источники погоды недоступны")

    if "current" in w:
        # OpenWeather
        cur       = w["current"]
        day_blk   = w["daily"][0]["temp"]
        wind_kmh  = cur["wind_speed"] * 3.6
        wind_deg  = cur["wind_deg"]
        press     = cur["pressure"]
        cloud_w   = clouds_word(cur.get("clouds", 0))
        day_max   = day_blk["max"]
        night_min = day_blk["min"]
        strong    = w.get("strong_wind", False)
        fog       = w.get("fog_alert", False)
    else:
        # Open-Meteo
        cw        = w["current_weather"]
        wind_kmh  = cw["windspeed"]
        wind_deg  = cw["winddirection"]
        # давление берём из hourly
        press     = w["hourly"]["surface_pressure"][0]
        cloud_w   = clouds_word(w["hourly"]["cloud_cover"][0])
        strong    = w.get("strong_wind", False)
        fog       = w.get("fog_alert", False)

        # дневная/ночная и код погоды
        daily = w["daily"]
        blk = daily[0] if isinstance(daily, list) else daily
        tm = blk["temperature_2m_max"]
        tn = blk["temperature_2m_min"]
        day_max   = tm[1] if len(tm)>1 else tm[0]
        night_min = tn[1] if len(tn)>1 else tn[0]

    # иконка заголовка
    icon = WEATHER_ICONS.get(cloud_w, "🌦️")
    P.append(f"{icon} <b>Погода на завтра в Лимассоле {TOMORROW.format('DD.MM.YYYY')}</b>")
    P.append(f"<b>Темп. днём/ночью:</b> {day_max:.1f}/{night_min:.1f} °C")
    P.append(f"<b>Облачность:</b> {cloud_w}")
    P.append(f"<b>Ветер:</b> {wind_phrase(wind_kmh)} ({wind_kmh:.1f} км/ч, {compass(wind_deg)})")
    if strong:
        P.append("⚠️ Ветер может усилиться")
    if fog:
        P.append("🌁 Возможен туман, водите аккуратно")
    P.append(f"<b>Давление:</b> {press:.0f} гПа")
    P.append("———")

    # 2) Рейтинг городов по дн./ночн. t˚
    temps: dict[str, tuple[float, float]] = {}
    for city, (la, lo) in CITIES.items():
        w2 = get_weather(la, lo)
        if not w2:
            continue
        if "current" in w2:
            tb = w2["daily"][0]["temp"]
            temps[city] = (tb["max"], tb["min"])
        else:
            db = w2["daily"]
            blk2 = db[0] if isinstance(db, list) else db
            tm2 = blk2["temperature_2m_max"]
            tn2 = blk2["temperature_2m_min"]
            d2 = tm2[1] if len(tm2)>1 else tm2[0]
            n2 = tn2[1] if len(tn2)>1 else tn2[0]
            temps[city] = (d2, n2)
    # сортируем по дневной t˚
    sorted_c = sorted(temps.items(), key=lambda kv: kv[1][0], reverse=True)
    P.append("🎖️ <b>Рейтинг городов (дн./ночь)</b>")
    medals = ["🥇","🥈","🥉"]
    for i, (city, (dval, nval)) in enumerate(sorted_c[:3]):
        P.append(f"{medals[i]} {city}: {dval:.1f}/{nval:.1f} °C")
    P.append("———")

    # 3) Качество воздуха + пыльца
    air = get_air() or {}
    if air:
        em = AIR_EMOJI.get(air["lvl"], "⚪")
        P.append("🏙️ <b>Качество воздуха</b>")
        P.append(f"{em} AQI {air['aqi']} | PM2.5: {safe(air['pm25'],' µg/м³')} | PM10: {safe(air['pm10'],' µg/м³')}")
    else:
        P.append("🏙️ <b>Качество воздуха</b>")
        P.append("нет данных")

    pollen = get_pollen()
    if pollen:
        idx = lambda v: ["нет","низкий","умеренный","высокий","оч. высокий","экстрим"][int(round(v))]
        P.append("🌿 <b>Пыльца</b>")
        P.append(f"Деревья — {idx(pollen['treeIndex'])} | Травы — {idx(pollen['grassIndex'])} | Сорняки — {idx(pollen['weedIndex'])}")
    P.append("———")

    # 4) Геомагнитка, Шуман, вода, астрособытия...
    kp, kp_state = get_kp()
    sch = get_schumann()
    sst = get_sst()
    astro = astro_events()

    P.append(f"🧲 <b>Геомагнитка</b> K-index: {kp:.1f} ({kp_state})" if kp is not None else "🧲 нет данных")
    if sch.get("high"):
        P.append("🎵 <b>Шуман:</b> ⚡️ вибрации повышены")
    elif "freq" in sch:
        P.append(f"🎵 <b>Шуман:</b> ≈{sch['freq']:.1f} Гц")
    else:
        P.append(f"🎵 <b>Шуман:</b> {sch.get('msg','нет данных')}")

    if sst is not None:
        P.append(f"🌊 <b>Темп. воды:</b> {sst:.1f} °C")
    if astro:
        P.append("🌌 <b>Астрособытия</b>\n" + " | ".join(astro))

    # 5) Вывод, рекомендации и факт дня
    # …ваш существующий выбор «culprit» и советы через gpt_blurb…
    summary, tips = gpt_blurb(culprit)
    P.append("———")
    P.append(f"📜 <b>Вывод</b>\n{summary}")
    P.append("———")
    P.append("✅ <b>Рекомендации</b>")
    for t in tips:
        P.append(f"• {t}")
    P.append("———")
    P.append(f"📚 {get_fact(TOMORROW)}")

    return "\n".join(P)



# ─────────── Опрос и фото ───────────────────────────────────────────
async def send_poll_if_friday(bot: Bot):
    if pendulum.now(TZ).is_friday():
        try:
            await bot.send_poll(CHAT_ID, POLL_QUESTION, POLL_OPTIONS,
                                is_anonymous=False, allows_multiple_answers=False)
        except tg_err.TelegramError as e:
            logging.warning("Poll error: %s", e)

async def send_unsplash_photo(bot: Bot):
    if not UNSPLASH_KEY:
        return
    # отправляем раз в 3 дня по UTC
    if (_ := pendulum.now("UTC").day_of_year) % 3 != 0:
        return
    url = f"https://api.unsplash.com/photos/random?query=cyprus sunset&client_id={UNSPLASH_KEY}"
    try:
        j = get_weather._get(url)  # можно вызвать общий _get
        photo = j.get("urls", {}).get("regular")
        if photo:
            await bot.send_photo(CHAT_ID, photo, caption="Фото дня • Unsplash")
    except Exception as e:
        logging.warning("Photo error: %s", e)


# ─────────── main() ─────────────────────────────────────────────────
async def main() -> None:
    bot = Bot(TOKEN)
    msg = build_msg()
    logging.info("Preview: %s", msg.replace("\n"," | ")[:200])
    try:
        await bot.send_message(CHAT_ID, msg, parse_mode="HTML", disable_web_page_preview=True)
    except tg_err.TelegramError as e:
        logging.error("Telegram error: %s", e)
        raise

    await send_poll_if_friday(bot)
    await send_unsplash_photo(bot)
    logging.info("All done ✓")


if __name__ == "__main__":
    asyncio.run(main())
