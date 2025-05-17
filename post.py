#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import asyncio
import logging
from datetime import date, timedelta

import requests
from telegram import Bot, error as tg_err

# ─────────── Настройки ────────────────────────────────────────────
LAT, LON = 34.707, 33.022  # Лимассол
TOKEN   = os.environ["TELEGRAM_TOKEN"]
CHAT_ID = int(os.environ["CHANNEL_ID"])

# ─────────── Логирование ──────────────────────────────────────────
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

# ─────────── Функция запроса ───────────────────────────────────────
def fetch_open_meteo(lat: float, lon: float) -> dict:
    """
    Берёт forecast_days=2, чтобы в daily.temperature_2m_max/min
    всегда был [сегодня, завтра].
    """
    url = "https://api.open-meteo.com/v1/forecast"
    params = {
        "latitude":        lat,
        "longitude":       lon,
        "timezone":        "UTC",
        "daily":           "temperature_2m_max,temperature_2m_min,weathercode",
        "forecast_days":   2,
        "hourly":          "surface_pressure,cloud_cover,wind_speed,wind_direction",
        "current_weather": "true",
    }
    resp = requests.get(url, params=params, timeout=15)
    resp.raise_for_status()
    return resp.json()

# ─────────── Построение сообщения ─────────────────────────────────
def build_msg() -> str:
    data = fetch_open_meteo(LAT, LON)

    # завтра как второй элемент массива
    tmax = data["daily"]["temperature_2m_max"][1]
    tmin = data["daily"]["temperature_2m_min"][1]

    # текущие условия (для ветра)
    cw = data.get("current_weather", {})
    wind_kmh = cw.get("windspeed", 0)
    wind_dir = cw.get("winddirection", 0)

    # давление и облачность из часовых
    press = data["hourly"]["surface_pressure"][0]
    clouds = data["hourly"]["cloud_cover"][0]

    # помощь в словах и компас
    def clouds_word(p): return "ясно" if p<20 else "переменная" if p<70 else "пасмурно"
    COMPASS = ["N","NNE","NE","ENE","E","ESE","SE","SSE",
               "S","SSW","SW","WSW","W","WNW","NW","NNW"]
    def compass(d): return COMPASS[int((d/22.5)+.5)%16]

    # заголовок
    tomorrow = (date.today() + timedelta(days=1)).strftime("%d.%m.%Y")
    lines = [
        f"☀️ <b>Погода на завтра {tomorrow} в Лимассоле</b>",
        f"🌡 <b>Темп. днём/ночью:</b> {tmax:.1f}/{tmin:.1f} °C",
        f"☁️ <b>Облачность:</b> {clouds_word(clouds)}",
        f"🌬 <b>Ветер:</b> {wind_kmh:.1f} км/ч, {compass(wind_dir)}",
        f"⏲ <b>Давление:</b> {press:.0f} гПа",
    ]
    return "\n".join(lines)

# ─────────── Отправка в Telegram ─────────────────────────────────
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
