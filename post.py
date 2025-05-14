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

    # ветка OpenWeather
    if "current" in w:
        cur      = w["current"]
        day_blk  = w["daily"][0]["temp"]
        wind_kmh = cur["wind_speed"] * 3.6
        wind_dir = cur["wind_deg"]
        press    = cur["pressure"]
        cloud_w  = clouds_word(cur.get("clouds", 0))
        day_max  = day_blk["max"]
        night_min = day_blk["min"]
        strong   = w.get("strong_wind", False)
        fog      = w.get("fog_alert", False)

    # ветка Open-Meteo
    else:
        cw       = w["current_weather"]
        wind_kmh = cw["windspeed"]
        wind_dir = cw["winddirection"]
        press    = w["hourly"]["surface_pressure"][0]    # <-- именно так
        cloud_w  = clouds_word(w["hourly"]["cloud_cover"][0])
        strong   = w.get("strong_wind", False)
        fog      = w.get("fog_alert", False)

        # извлечение завтрашних t˚ из daily
        daily = w["daily"]
        blk   = daily[0] if isinstance(daily, list) else daily
        arr_d = blk["temperature_2m_max"]
        arr_n = blk["temperature_2m_min"]
        codes = blk["weathercode"]
        day_max   = arr_d[1] if len(arr_d) > 1 else arr_d[0]
        night_min = arr_n[1] if len(arr_n) > 1 else arr_n[0]

    # теперь все переменные определены: icon, day_max, night_min, wind_kmh, wind_dir, press, cloud_w, strong, fog
    icon = WEATHER_ICONS.get(cloud_w, "🌦️")
    P.append(f"{icon} <b>Погода на завтра в Лимассоле {TOMORROW.format('DD.MM.YYYY')}</b>")
    P.append(f"<b>Темп.: {day_max:.1f}/{night_min:.1f} °C</b>")
    P.append(f"<b>Облачность:</b> {cloud_w}")
    P.append(f"<b>Ветер:</b> {wind_phrase(wind_kmh)} ({wind_kmh:.1f} км/ч, {compass(wind_dir)})")
    if strong:
        P.append("⚠️ Ветер может усилиться")
    if fog:
        P.append("🌁 Возможен туман, водите аккуратно")
    P.append(f"<b>Давление:</b> {press:.0f} гПа")
    P.append("———")

    # …далее по остальной логике без изменений…


    # 2) Рейтинг городов (дён./ночн.) с медалями
    temps_d, temps_n = {}, {}
    for city, (la, lo) in CITIES.items():
        w2 = get_weather(la, lo)
        if not w2: 
            continue
        if "current" in w2:
            tb = w2["daily"][0]["temp"]
            temps_d[city] = tb["max"]
            temps_n[city] = tb["min"]
        else:
            blk2         = w2["daily"][0] if isinstance(w2["daily"], list) else w2["daily"]
            arr_d, arr_n = blk2["temperature_2m_max"], blk2["temperature_2m_min"]
            temps_d[city] = arr_d[1] if len(arr_d)>1 else arr_d[0]
            temps_n[city] = arr_n[1] if len(arr_n)>1 else arr_n[0]

    ranked = sorted(temps_d.items(), key=lambda x: x[1], reverse=True)
    medals = ["🥇","🥈","🥉","🏅"]
    P.append("🎖️ <b>Рейтинг по дн./ночн. темп.</b>")
    for i, (city, dval) in enumerate(ranked):
        med = medals[i] if i < len(medals) else ""
        nval = temps_n[city]
        P.append(f"{med} {city}: {dval:.1f}/{nval:.1f} °C")
    P.append("———")

    # 3) Качество воздуха и пыльца
    air    = get_air() or {}
    pollen = get_pollen()
    if air:
        aqi   = air["aqi"]
        lvl   = air["lvl"]
        em    = aqi_color(aqi)
        pm25  = safe(air["pm25"], " µg/м³")
        pm10  = safe(air["pm10"], " µg/м³")
        P.append("🏙️ <b>Качество воздуха</b>")
        P.append(f"{em} AQI {aqi} | PM₂.₅: {pm25} | PM₁₀: {pm10}")
    else:
        P.append("🏙️ <b>Качество воздуха</b>")
        P.append("нет данных")

    if pollen:
        idx = lambda v: ["нет","низкий","умеренный","высокий","оч. высокий","экстрим"][int(round(v))]
        P.append("🌿 <b>Пыльца</b>")
        P.append(
            f"Деревья — {idx(pollen['treeIndex'])} | "
            f"Травы — {idx(pollen['grassIndex'])} | "
            f"Сорняки — {idx(pollen['weedIndex'])}"
        )
    P.append("———")

    # 4) Геомагнитная + Шуман + SST
    kp_val, kp_state = get_kp()
    sch = get_schumann()
    sst = get_sst()

    P.append(f"🧲 <b>Геомагнитка</b> K-index: {kp_val:.1f} ({kp_state})" if kp_val is not None else "🧲 <b>Геомагнитка</b> нет данных")
    if sch.get("high"):
        P.append("🎵 <b>Шуман:</b> ⚡️ вибрации повышены (>8 Гц)")
    elif "freq" in sch:
        P.append(f"🎵 <b>Шуман:</b> ≈{sch['freq']:.1f} Гц, амплитуда стабильна")
    else:
        P.append(f"🎵 <b>Шуман:</b> {sch.get('msg','нет данных')}")
    if sst is not None:
        P.append(f"🌊 <b>Темп. воды</b> {sst:.1f} °C")
    P.append("———")

    # 5) Астрособытия
    ev = astro_events()
    if ev:
        main_phase, *others = ev
        line = main_phase + ((" | " + " | ".join(others)) if others else "")
        P.append(f"🌌 <b>Астрособытия</b>\n{line}")
    P.append("———")

    # 6) Вывод и советы
    # Выбираем виновника по приоритету
    culprit = "мини-парад планет"
    if fog:             culprit = "туман"
    elif kp_state=="буря": culprit = "магнитные бури"
    elif press < 1007:  culprit = "низкое давление"
    elif strong:        culprit = "шальной ветер"

    summary, tips = gpt_blurb(culprit)
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
