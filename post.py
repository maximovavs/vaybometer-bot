#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import asyncio
import logging
import requests
from typing import Optional, Tuple, Dict

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
TZ           = pendulum.timezone("Asia/Nicosia")
TODAY        = pendulum.now(TZ).date()
TOMORROW     = TODAY.add(days=1)
TOKEN        = os.environ["TELEGRAM_TOKEN"]
CHAT_ID      = int(os.environ["CHANNEL_ID"])
UNSPLASH_KEY = os.getenv("UNSPLASH_KEY")

POLL_QUESTION = "Как сегодня ваше самочувствие? 🤔"
POLL_OPTIONS  = ["🔥 Полон(а) энергии", "🙂 Нормально", "😴 Слегка вялый(ая)", "🤒 Всё плохо"]

CITIES = {
    "Limassol": (34.707, 33.022),
    "Larnaca" : (34.916, 33.624),
    "Nicosia" : (35.170, 33.360),
    "Pafos"   : (34.776, 32.424),
}

# ─────────── Прямой запрос Open-Meteo для завтрашних макс/мин ────────
def fetch_tomorrow_temps(lat: float, lon: float) -> Tuple[Optional[float], Optional[float]]:
    """
    Возвращает (max_temp, min_temp) на завтра, запрашивая
    только ту единственную дату через start_date/end_date.
    """
    date = TOMORROW.to_date_string()  # 'YYYY-MM-DD'
    url = "https://api.open-meteo.com/v1/forecast"
    params = {
        "latitude":      lat,
        "longitude":     lon,
        "timezone":      TZ.name,
        "daily":         "temperature_2m_max,temperature_2m_min,weathercode",
        "start_date":    date,
        "end_date":      date,
    }
    r = requests.get(url, params=params, timeout=15)
    r.raise_for_status()
    j = r.json()
    daily = j.get("daily", {})
    max_arr = daily.get("temperature_2m_max", [])
    min_arr = daily.get("temperature_2m_min", [])
    tmax = max_arr[0] if len(max_arr) >= 1 else None
    tmin = min_arr[0] if len(min_arr) >= 1 else None
    return tmax, tmin

# ─────────── Сборка сообщения ─────────────────────────────────────
def build_msg() -> str:
    P: list[str] = []

    # 1) Завтрашние макс/мин для Лимассола
    lat, lon = CITIES["Limassol"]
    day_max, night_min = fetch_tomorrow_temps(lat, lon)
    if day_max is None or night_min is None:
        raise RuntimeError("Не удалось получить температуру на завтра")

    # 2) Текущие условия для Лимассола (давление, облака, ветер)
    w = get_weather(lat, lon)
    if not w:
        raise RuntimeError("Источники погоды недоступны")
    # определяем общий флаг
    strong = w.get("strong_wind", False)
    fog    = w.get("fog_alert", False)

    # Достаем параметры
    if "current" in w:
        cur      = w["current"]
        wind_kmh = cur["wind_speed"] * 3.6
        wind_deg = cur["wind_deg"]
        press    = cur["pressure"]
        cloud_w  = clouds_word(cur.get("clouds", 0))
    else:
        cw       = w["current_weather"]
        wind_kmh = cw["windspeed"]
        wind_deg = cw["winddirection"]
        press    = w["hourly"]["surface_pressure"][0]
        cloud_w  = clouds_word(w["hourly"]["cloud_cover"][0])

    # Заголовок
    icon = WEATHER_ICONS.get(cloud_w, "🌦️")
    P.append(f"{icon} <b>Погода на завтра в Лимассоле {TOMORROW.format('DD.MM.YYYY')}</b>")
    P.append(f"<b>Темп. днём/ночью:</b> {day_max:.1f}/{night_min:.1f} °C")
    P.append(f"<b>Облачность:</b> {cloud_w}")
    P.append(f"<b>Ветер:</b> {wind_phrase(wind_kmh)} ({wind_kmh:.1f} км/ч, {compass(wind_deg)})")
    if strong: P.append("⚠️ Ветер может усилиться")
    if fog:    P.append("🌁 Возможен туман, водите аккуратно")
    P.append(f"<b>Давление:</b> {press:.0f} гПа")
    P.append("———")

    # 3) Рейтинг городов по завтрашней дн./ночн. темп.
    temps: Dict[str, tuple[float, float]] = {}
    for city, (la, lo) in CITIES.items():
        d, n = fetch_tomorrow_temps(la, lo)
        if d is None: 
            continue
        temps[city] = (d, n if n is not None else d)

    P.append("🎖️ <b>Рейтинг городов (дн./ночь)</b>")
    sorted_c = sorted(temps.items(), key=lambda kv: kv[1][0], reverse=True)
    medals   = ["🥇","🥈","🥉","4️⃣"]
    for i, (city, (d_v, n_v)) in enumerate(sorted_c[:4]):
        P.append(f"{medals[i]} {city}: {d_v:.1f}/{n_v:.1f} °C")
    P.append("———")

    # 4) Качество воздуха + пыльца
    air    = get_air() or {}
    P.append("🏙️ <b>Качество воздуха</b>")
    if air:
        lvl = air["lvl"]
        P.append(f"{AIR_EMOJI.get(lvl,'⚪')} {lvl} (AQI {air['aqi']}) | "
                 f"PM2.5: {safe(air['pm25'],'µg/м³')} | PM10: {safe(air['pm10'],'µg/м³')}")
    else:
        P.append("нет данных")
    pollen = get_pollen()
    if pollen:
        idxf = lambda v: ["нет","низкий","умеренный","высокий","оч. высокий","экстрим"][int(round(v))]
        P.append("🌿 <b>Пыльца</b>")
        P.append(
            f"Деревья – {idxf(pollen['treeIndex'])} | "
            f"Травы – {idxf(pollen['grassIndex'])} | "
            f"Сорняки – {idxf(pollen['weedIndex'])}"
        )
    P.append("———")

    # 5) Геомагнитка, Шуман, морская вода, астрособытия
    kp, kp_state = get_kp()
    sch          = get_schumann()
    sst          = get_sst()
    astro        = astro_events()

    if kp is not None:
        P.append(f"🧲 <b>Геомагнитка</b> K-index: {kp:.1f} ({kp_state})")
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

    # 6) Виновник + советы от GPT
    if     fog:        culprit = "туман"
    elif   kp_state=="буря": culprit = "магнитные бури"
    elif   press<1007: culprit = "низкое давление"
    elif   strong:     culprit = "шальной ветер"
    else:              culprit = "мини-парад планет"

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
async def send_main_post(bot: Bot) -> None:
    html = build_msg()
    logging.info("Preview: %s", html.replace("\n"," | ")[:200])
    try:
        await bot.send_message(
            CHAT_ID,
            html,
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
        logging.info("Message sent ✓")
    except tg_err.TelegramError as e:
        logging.error("Telegram error: %s", e)
        raise

async def send_poll_if_friday(bot: Bot) -> None:
    # Monday=0 … Friday=4
    if pendulum.now(TZ).weekday() == 4:
        try:
            await bot.send_poll(
                CHAT_ID,
                question=POLL_QUESTION,
                options=POLL_OPTIONS,
                is_anonymous=False,
                allows_multiple_answers=False,
            )
        except tg_err.TelegramError as e:
            logging.warning("Poll send error: %s", e)

async def fetch_unsplash_photo() -> Optional[str]:
    if not UNSPLASH_KEY:
        return None
    url = "https://api.unsplash.com/photos/random"
    j   = requests.get(
        url,
        params={"query":"cyprus coast sunset","client_id":UNSPLASH_KEY},
        timeout=15
    ).json()
    return j.get("urls",{}).get("regular")

async def send_photo(bot: Bot, photo_url: str) -> None:
    try:
        await bot.send_photo(CHAT_ID, photo=photo_url, caption="Фото дня • Unsplash")
    except tg_err.TelegramError as e:
        logging.warning("Photo send error: %s", e)

async def main() -> None:
    bot = Bot(token=TOKEN)
    await send_main_post(bot)
    await send_poll_if_friday(bot)
    # каждые 3 дня – фото
    if UNSPLASH_KEY and (TODAY.day % 3 == 0):
        if photo := await fetch_unsplash_photo():
            await send_photo(bot, photo)
    logging.info("All tasks done ✓")

if __name__ == "__main__":
    asyncio.run(main())
