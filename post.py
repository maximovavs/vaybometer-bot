#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import asyncio
import logging
import requests
from typing import Optional, Tuple, Dict, Any

import pendulum
from telegram import Bot, error as tg_err

from utils import (
    compass, clouds_word, wind_phrase, safe, get_fact,
    WEATHER_ICONS, AIR_EMOJI, pressure_trend, kp_emoji, pm_color
)
from weather import get_weather
from air import get_air, get_sst, get_kp
from pollen import get_pollen
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
POLL_OPTIONS  = ["🔥 Полон(а) энергии", "🙂 Нормально",
                 "😴 Слегка вялый(ая)", "🤒 Всё плохо"]

CITIES = {
    "Limassol": (34.707, 33.022),
    "Larnaca" : (34.916, 33.624),
    "Nicosia" : (35.170, 33.360),
    "Pafos"   : (34.776, 32.424),
}


def fetch_tomorrow_temps(lat: float, lon: float) -> Tuple[Optional[float], Optional[float]]:
    """
    Возвращает (max_temp, min_temp) на завтра запросом к Open-Meteo.
    """
    date = TOMORROW.to_date_string()
    url = "https://api.open-meteo.com/v1/forecast"
    params = {
        "latitude":   lat,
        "longitude":  lon,
        "timezone":   TZ.name,
        "daily":      "temperature_2m_max,temperature_2m_min",
        "start_date": date,
        "end_date":   date,
    }
    r = requests.get(url, params=params, timeout=15)
    r.raise_for_status()
    j = r.json().get("daily", {})
    tmax = j.get("temperature_2m_max", [])
    tmin = j.get("temperature_2m_min", [])
    return (tmax[0] if tmax else None,
            tmin[0] if tmin else None)


def build_msg() -> str:
    P: list[str] = []

    # 1) Завтрашние макс/мин для Лимассола
    lat, lon = CITIES["Limassol"]
    day_max, night_min = fetch_tomorrow_temps(lat, lon)
    if day_max is None or night_min is None:
        raise RuntimeError("Не удалось получить температуру на завтра")

    # 2) Текущие условия
    w = get_weather(lat, lon) or {}
    strong = w.get("strong_wind", False)
    fog    = w.get("fog_alert",   False)

    cur = w.get("current") or w.get("current_weather", {})
    wind_kmh = cur.get("windspeed") or cur.get("wind_speed", 0.0)
    wind_deg = cur.get("winddirection") or cur.get("wind_deg", 0.0)

    press = cur.get("pressure") \
        or w.get("hourly", {}).get("surface_pressure", [1013])[0]

    clouds_pct = cur.get("clouds")
    if clouds_pct is None:
        clouds_pct = w.get("hourly", {}).get("cloud_cover", [0])[0]
    cloud_w = clouds_word(clouds_pct)

    avg_line   = f"🌡 Средняя темп.: {((day_max + night_min)/2):.0f} °C"
    press_line = f"🔽 Давление: {press:.0f} гПа {pressure_trend(w)}"

    icon = WEATHER_ICONS.get(cloud_w, "🌦️")
    P += [
        f"{icon} <b>Погода на завтра в Лимассоле {TOMORROW.format('DD.MM.YYYY')}</b>",
        avg_line,
        f"📈 Темп. днём/ночью: {day_max:.1f}/{night_min:.1f} °C",
        f"🌤 Облачность: {cloud_w}",
        f"💨 Ветер: {wind_phrase(wind_kmh)} ({wind_kmh:.1f} км/ч, {compass(wind_deg)})",
        press_line,
    ]
    if strong: P.append("⚠️ Ветер может усилиться")
    if fog:    P.append("🌁 Возможен туман, водите аккуратно")
    P.append("———")

    # 3) Рейтинг городов (дн./ночь)
    temps: Dict[str, Tuple[float, float]] = {}
    for city, (la, lo) in CITIES.items():
        d, n = fetch_tomorrow_temps(la, lo)
        if d is not None:
            temps[city] = (d, n or d)

    P.append("🎖️ <b>Рейтинг городов (дн./ночь)</b>")
    medals = ["🥇", "🥈", "🥉", "4️⃣"]
    for i, (city, (d, n)) in enumerate(
            sorted(temps.items(), key=lambda kv: kv[1][0], reverse=True)[:4]):
        P.append(f"{medals[i]} {city}: {d:.1f}/{n:.1f} °C")
    P.append("———")

    # 4) Качество воздуха + пыльца
    air = get_air() or {}
    P.append("🏙️ <b>Качество воздуха</b>")
    lvl = air.get("lvl", "н/д")
    P.append(
        f"{AIR_EMOJI.get(lvl,'⚪')} {lvl} (AQI {air.get('aqi','н/д')}) | "
        f"PM₂.₅: {pm_color(air.get('pm25'))} | PM₁₀: {pm_color(air.get('pm10'))}"
    )

    pollen = get_pollen() or {}
    if pollen:
        P += [
            "🌿 <b>Пыльца</b>",
            f"Деревья: {pollen['tree']} | Травы: {pollen['grass']} | "
            f"Сорняки: {pollen['weed']} — риск {pollen['risk']}"
        ]
    P.append("———")

    # 5) Геомагнитка, Шуман, вода, астрособытия
    kp, kp_state = get_kp()
    sch          = get_schumann()
    sst          = get_sst()
    astro        = astro_events()

    if kp is not None:
        P.append(f"{kp_emoji(kp)} Геомагнитка: Kp={kp:.1f} ({kp_state})")
    else:
        P.append("🧲 Геомагнитка: н/д")

    if "freq" in sch:
        trend = "↑" if sch.get("high") else "→"
        P.append(f"🎵 Шуман: {sch['freq']:.1f} Гц {trend}")
    else:
        P.append(f"🎵 Шуман: {sch.get('msg','н/д')}")

    if sst is not None:
        P.append(f"🌊 Темп. воды (Medit.): {sst:.1f} °C (Open-Meteo)")

    if astro:
        P.append("🌌 <b>Астрособытия</b> – " + " | ".join(astro))
    P.append("———")

    # 6) Вывод + советы
    if   fog:        culprit = "туман"
    elif kp_state=="буря": culprit = "магнитные бури"
    elif press <1007: culprit = "низкое давление"
    elif strong:     culprit = "шальной ветер"
    else:            culprit = "мини-парад планет"

    summary, tips = gpt_blurb(culprit)
    P.append(f"📜 <b>Вывод</b>\n{summary}")
    P.append("———")
    P.append("✅ <b>Рекомендации</b>")
    for t in tips:
        P.append(f"• {t}")
    P.append("———")
    P.append(f"📚 {get_fact(TOMORROW)}")

    return "\n".join(P)


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
    resp = requests.get(
        url,
        params={"query":"cyprus coast sunset","client_id":UNSPLASH_KEY},
        timeout=15
    )
    return resp.json().get("urls",{}).get("regular")


async def send_photo(bot: Bot, photo_url: str) -> None:
    try:
        await bot.send_photo(CHAT_ID, photo=photo_url, caption="Фото дня • Unsplash")
    except tg_err.TelegramError as e:
        logging.warning("Photo send error: %s", e)


async def main() -> None:
    bot = Bot(token=TOKEN)
    await send_main_post(bot)
    await send_poll_if_friday(bot)
    if UNSPLASH_KEY and (TODAY.day % 3 == 0):
        if photo := await fetch_unsplash_photo():
            await send_photo(bot, photo)
    logging.info("All tasks done ✓")


if __name__ == "__main__":
    asyncio.run(main())
