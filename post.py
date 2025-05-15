#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import asyncio
import logging
from datetime import date, timedelta

import requests
import pendulum
from telegram import Bot, error as tg_err

# ─────────── Настройки ────────────────────────────────────────────
LAT, LON = 34.707, 33.022  # Limassol, CY
TZ = pendulum.timezone("Europe/Nicosia")
TOKEN   = os.environ["TELEGRAM_TOKEN"]
CHAT_ID = int(os.environ["CHANNEL_ID"])

# ─────────── Логирование ──────────────────────────────────────────
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

# ─────────── Функции ───────────────────────────────────────────────
def fetch_open_meteo(lat: float, lon: float, target: date) -> dict:
    """Запрос к Open-Meteo на конкретную дату."""
    start = target.isoformat()
    end   = (target + timedelta(days=1)).isoformat()
    url = "https://api.open-meteo.com/v1/forecast"
    params = {
        "latitude":       lat,
        "longitude":      lon,
        "timezone":       "Europe/Nicosia",
        "daily":          "temperature_2m_max,temperature_2m_min,weathercode",
        "current_weather":"true",
        "start_date":     start,
        "end_date":       end,
        "hourly":         "surface_pressure,cloud_cover,wind_speed,wind_direction",
    }
    r = requests.get(url, params=params, timeout=15)
    r.raise_for_status()
    return r.json()

def build_msg() -> str:
    # определяем дату завтра
    tomorrow = date.today() + timedelta(days=1)

    # делаем запрос
    data = fetch_open_meteo(LAT, LON, tomorrow)

    # получаем дневную и ночную температуры
    tmax_list = data["daily"]["temperature_2m_max"]
    tmin_list = data["daily"]["temperature_2m_min"]
    # в ответе два элемента: [на сегодня, на завтра]
    day_max   = tmax_list[1]
    night_min = tmin_list[1]

    # текущие данные
    cw = data.get("current_weather", {})
    wind_kmh = cw.get("windspeed", 0)
    wind_dir = cw.get("winddirection", 0)
    press    = data["hourly"]["surface_pressure"][0]
    clouds   = data["hourly"]["cloud_cover"][0]

    # облачность и компас
    def clouds_word(p): return "ясно" if p<20 else "переменная" if p<70 else "пасмурно"
    COMPASS = ["N","NNE","NE","ENE","E","ESE","SE","SSE",
               "S","SSW","SW","WSW","W","WNW","NW","NNW"]
    def compass(d): return COMPASS[int((d/22.5)+.5)%16]

    # собираем текст
    P = []
    P.append(f"☀️ <b>Погода на {tomorrow.strftime('%d.%m.%Y')} в Лимассоле</b>")
    P.append(f"🌡 <b>Темп. днём/ночью:</b> {day_max:.1f}/{night_min:.1f} °C")
    P.append(f"☁️ <b>Облачность:</b> {clouds_word(clouds)}")
    P.append(f"🌬 <b>Ветер:</b> {wind_kmh:.1f} км/ч, {compass(wind_dir)}")
    P.append(f"⏲ <b>Давление:</b> {press:.0f} гПа")

    return "\n".join(P)

# ─────────── Отправка ──────────────────────────────────────────────
async def main():
    bot = Bot(TOKEN)
    text = build_msg()
    logging.info("Preview: %s", text.replace("\n"," | "))
    try:
        await bot.send_message(
            CHAT_ID,
            text,
            parse_mode="HTML",
            disable_web_page_preview=True
        )
        logging.info("Message sent ✓")
    except tg_err.TelegramError as e:
        logging.error("Telegram error: %s", e)
        raise

if __name__ == "__main__":
    asyncio.run(main())
