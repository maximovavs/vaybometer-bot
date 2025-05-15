#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import asyncio
import logging
from typing import Any, Dict, Optional

import pendulum
from telegram import Bot, error as tg_err

from utils import (
    compass, clouds_word, wind_phrase, safe, get_fact,
    WEATHER_ICONS, AIR_EMOJI
)
from weather import get_weather
from air import get_air, get_pollen, get_sst, get_kp
from schumann import get_schumann
from astro import astro_events
from gpt import gpt_blurb

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

# ─────────── Constants ────────────────────────────────────────────
TZ        = pendulum.timezone("Asia/Nicosia")
TODAY     = pendulum.now(TZ).date()
TOMORROW  = TODAY.add(days=1)
TOKEN     = os.environ["TELEGRAM_TOKEN"]
CHAT_ID   = int(os.environ["CHANNEL_ID"])
UNSPLASH  = os.getenv("UNSPLASH_KEY")

POLL_Q    = "Как сегодня ваше самочувствие? 🤔"
POLL_OPTS = ["🔥 Полон(а) энергии", "🙂 Нормально", "😴 Слегка вялый(ая)", "🤒 Всё плохо"]

CITIES = {
    "Limassol": (34.707, 33.022),
    "Larnaca" : (34.916, 33.624),
    "Nicosia" : (35.170, 33.360),
    "Pafos"   : (34.776, 32.424),
}

# ─────────── build_msg ────────────────────────────────────────────
def build_msg() -> str:
    P: list[str] = []

    # 1️⃣ Получаем прогноз для Лимассола
    lat, lon = CITIES["Limassol"]
    w = get_weather(lat, lon)
    if not w:
        raise RuntimeError("Источники погоды недоступны")

    # Вынесем common
    strong = w.get("strong_wind", False)
    fog    = w.get("fog_alert", False)

    # …но cur может быть в w["current"] или только в w["current_weather"]
    if "current" in w:
        cur = w["current"]
    else:
        # fallback: Open-Meteo без «current»
        cw = w["current_weather"]
        cur = {
            "windspeed":     cw["windspeed"],
            "winddirection": cw["winddirection"],
            "pressure":      w["hourly"]["surface_pressure"][0],
            "clouds":        w["hourly"]["cloud_cover"][0],
        }

    wind_kmh = cur["windspeed"]
    wind_deg = cur["winddirection"]
    press    = cur["pressure"]
    cloud_w  = clouds_word(cur["clouds"])

    # Достаем завтра из массива daily (два элемента: сегодня и завтра)
    day_arr   = w["daily"]["temperature_2m_max"]
    night_arr = w["daily"]["temperature_2m_min"]
    # гарантированно есть index 1
    day_max   = day_arr[1]
    night_min = night_arr[1]

    # Заголовок и базовый блок
    icon = WEATHER_ICONS.get(cloud_w, "🌦️")
    P.append(f"{icon} <b>Погода на завтра в Лимассоле {TOMORROW.format('DD.MM.YYYY')}</b>")
    P.append(f"<b>Темп. днём/ночью:</b> {day_max:.1f}/{night_min:.1f} °C")
    P.append(f"<b>Облачность:</b> {cloud_w}")
    P.append(f"<b>Ветер:</b> {wind_phrase(wind_kmh)} ({wind_kmh:.1f} км/ч, {compass(wind_deg)})")
    if strong: P.append("⚠️ Ветер может усилиться")
    if fog:    P.append("🌁 Возможен туман, водите аккуратно")
    P.append(f"<b>Давление:</b> {press:.0f} гПа")
    P.append("———")

    # 2️⃣ Рейтинг городов (дн./ночь)
    temps: Dict[str, tuple[float, float]] = {}
    for city, (la, lo) in CITIES.items():
        w2 = get_weather(la, lo)
        if not w2:
            continue
        d2 = w2["daily"]["temperature_2m_max"][1]
        n2 = w2["daily"]["temperature_2m_min"][1]
        temps[city] = (d2, n2)

    P.append("🎖️ <b>Рейтинг городов (дн./ночь)</b>")
    sorted_c = sorted(temps.items(), key=lambda kv: kv[1][0], reverse=True)
    medals   = ["🥇","🥈","🥉","4️⃣"]
    for i, (city, (d_v, n_v)) in enumerate(sorted_c[:4]):
        P.append(f"{medals[i]} {city}: {d_v:.1f}/{n_v:.1f} °C")
    P.append("———")

    # 3️⃣ Качество воздуха + пыльца
    air = get_air() or {}
    P.append("🏙️ <b>Качество воздуха</b>")
    if air:
        lvl = air["lvl"]
        aqi = air["aqi"]
        P.append(f"{AIR_EMOJI.get(lvl,'⚪')} {lvl} (AQI {aqi}) | "
                 f"PM2.5: {safe(air['pm25'],'µg/м³')} | PM10: {safe(air['pm10'],'µg/м³')}")
    else:
        P.append("нет данных")
    pollen = get_pollen()
    if pollen:
        idx = lambda v: ["нет","низкий","умеренный","высокий","оч. высокий","экстрим"][int(round(v))]
        P.append("🌿 <b>Пыльца</b>")
        P.append(f"Деревья — {idx(pollen['treeIndex'])} | "
                 f"Травы — {idx(pollen['grassIndex'])} | "
                 f"Сорняки — {idx(pollen['weedIndex'])}")
    P.append("———")

    # 4️⃣ Геомагнитка / Шуман / вода / астрособытия
    kp, kp_st = get_kp()
    sch       = get_schumann()
    sst       = get_sst()
    astro     = astro_events()

    if kp is not None:
        P.append(f"🧲 <b>Геомагнитка</b> K-index: {kp:.1f} ({kp_st})")
    else:
        P.append("🧲 <b>Геомагнитка</b> нет данных")

    if sch.get("high"):
        P.append("🎵 <b>Шуман:</b> ⚡️ вибрации повышены")
    elif "freq" in sch:
        P.append(f"🎵 <b>Шуман:</b> ≈{sch['freq']:.1f} Гц")
    else:
        P.append(f"🎵 <b>Шуман:</b> {sch.get('msg','нет данных')}")

    if sst is not None:
        P.append(f"🌊 <b>Температура воды:</b> {sst:.1f} °C")
    if astro:
        P.append("🌌 <b>Астрособытия</b> – " + " | ".join(astro))
    P.append("———")

    # 5️⃣ Вывод + советы
    if fog:
        culprit = "туман"
    elif kp_st == "буря":
        culprit = "магнитные бури"
    elif press < 1007:
        culprit = "низкое давление"
    elif strong:
        culprit = "шальной ветер"
    else:
        culprit = "мини-парад планет"

    summary, tips = gpt_blurb(culprit)
    P.append(f"📜 <b>Вывод</b>\n{summary}")
    P.append("———")
    P.append("✅ <b>Рекомендации</b>")
    for t in tips:
        P.append(f"• {t}")
    P.append("———")
    P.append(f"📚 {get_fact(TOMORROW)}")

    return "\n".join(P)

# ─────────── Отправка ──────────────────────────────────────────────
async def send_main(bot: Bot) -> None:
    html = build_msg()
    logging.info("Preview: %s", html.replace("\n"," | ")[:200])
    try:
        await bot.send_message(
            CHAT_ID, html,
            parse_mode="HTML",
            disable_web_page_preview=True
        )
    except tg_err.TelegramError as e:
        logging.error("Telegram error: %s", e)
        raise

async def send_poll(bot: Bot) -> None:
    if pendulum.now(TZ).weekday() == 4:
        try:
            await bot.send_poll(
                CHAT_ID,
                question=POLL_Q,
                options=POLL_OPTS,
                is_anonymous=False,
                allows_multiple_answers=False
            )
        except tg_err.TelegramError as e:
            logging.warning("Poll error: %s", e)

async def fetch_photo() -> Optional[str]:
    if not UNSPLASH:
        return None
    j = get_weather._get(  # <-- поправьте на ваш _get из utils, если требуется
        "https://api.unsplash.com/photos/random",
        query="cyprus coast sunset",
        client_id=UNSPLASH
    )
    return j.get("urls", {}).get("regular")

async def send_photo(bot: Bot, url: str) -> None:
    try:
        await bot.send_photo(CHAT_ID, photo=url, caption="Фото дня • Unsplash")
    except tg_err.TelegramError as e:
        logging.warning("Photo error: %s", e)

async def main() -> None:
    bot = Bot(TOKEN)
    await send_main(bot)
    await send_poll(bot)
    if UNSPLASH and pendulum.now(TZ).day % 3 == 0:
        if url := await fetch_photo():
            await send_photo(bot, url)
    logging.info("All done ✓")

if __name__ == "__main__":
    asyncio.run(main())
