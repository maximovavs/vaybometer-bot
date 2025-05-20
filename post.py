#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import asyncio
import logging
import requests
import json
from pathlib import Path
from typing import Optional, Tuple, Dict, Any

import pendulum
from requests.exceptions import RequestException
from telegram import Bot, error as tg_err

from utils import (
    compass, clouds_word, wind_phrase, get_fact,
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
TOKEN        = os.environ.get("TELEGRAM_TOKEN", "")
CHAT_ID      = int(os.environ.get("CHANNEL_ID", 0))
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

# ─────────── Schumann fallback ────────────────────────────────────
def get_schumann_with_fallback() -> Dict[str, Any]:
    """
    Сначала пробует get_schumann() — 
    если нет live-данных, читает последний часовой замер из schumann_hourly.json
    и рассчитывает тренд за последние 24 точки.
    """
    sch = get_schumann()
    if sch.get("freq") is not None:
        return sch

    # fallback на локальный часовой файл
    cache_path = Path(__file__).parent / "schumann_hourly.json"
    if cache_path.exists():
        try:
            arr = json.loads(cache_path.read_text())
            if arr:
                last = arr[-1]
                # строим тренд по последним 24 точкам (или меньше, если меньше строк)
                pts = arr[-24:]
                freqs = [p["freq"] for p in pts]
                if len(freqs) >= 2:
                    avg = sum(freqs[:-1]) / (len(freqs)-1)
                    delta = freqs[-1] - avg
                    if delta >= 0.1:
                        trend = "↑"
                    elif delta <= -0.1:
                        trend = "↓"
                    else:
                        trend = "→"
                else:
                    trend = "→"
                return {
                    "freq":  round(last["freq"], 2),
                    "amp":   round(last["amp"], 1),
                    "high":  last["freq"] > 8.0 or last["amp"] > 100.0,
                    "trend": trend,
                    "cached": True,
                }
        except Exception as e:
            logging.warning("Schumann fallback parse error: %s", e)

    # возвращаем оригинальный sch со «шуткой»
    return sch

def fetch_tomorrow_temps(lat: float, lon: float) -> Tuple[Optional[float], Optional[float]]:
    """
    Возвращает (max_temp, min_temp) на завтра или (None, None) при ошибке.
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
    try:
        r = requests.get(url, params=params, timeout=15)
        r.raise_for_status()
        daily = r.json().get("daily", {})
        tmax = daily.get("temperature_2m_max", [])
        tmin = daily.get("temperature_2m_min", [])
        return (tmax[0] if tmax else None,
                tmin[0] if tmin else None)
    except RequestException as e:
        logging.warning("fetch_tomorrow_temps error: %s", e)
        return None, None


def build_msg() -> str:
    P: list[str] = []

    # 1) Приветствие и заголовок
    P.append(f"<b> 🌅 Добрый вечер! Погода на завтра на Кипре ({TOMORROW.format('DD.MM.YYYY')})</b>")

    # 2) Температура воды
    sst = get_sst()
    if sst is not None:
        P.append(f"🌊 Темп. моря: {sst:.1f} °C")

    # 3) Погодный блок: средняя темп. и текущие условия (Limassol)
    lat, lon = CITIES["Limassol"]
    day_max, night_min = fetch_tomorrow_temps(lat, lon)
    w = get_weather(lat, lon) or {}
    cur = w.get("current") or w.get("current_weather", {})

    if day_max is not None and night_min is not None:
        avg_temp = (day_max + night_min) / 2
    else:
        avg_temp = cur.get("temperature") or cur.get("temp") or 0
        logging.warning("Используется текущая температура вместо прогноза")

    wind_kmh = cur.get("windspeed") or cur.get("wind_speed", 0.0)
    wind_deg = cur.get("winddirection") or cur.get("wind_deg", 0.0)
    press    = cur.get("pressure") or w.get("hourly", {}).get("surface_pressure", [0])[0]

    clouds_pct = cur.get("clouds")
    if clouds_pct is None:
        clouds_pct = w.get("hourly", {}).get("cloud_cover", [0])[0]
    cloud_w = clouds_word(clouds_pct)

    P.append(
        f"🌡️ Ср. темп: {avg_temp:.0f} °C • {cloud_w} • 💨 {wind_kmh:.1f} км/ч ({compass(wind_deg)})"
        f" • 💧 {press:.0f} гПа {pressure_trend(w)}"
    )
    P.append("———")

    # 4) Рейтинг городов по завтрашней температуре
    temps: Dict[str, Tuple[float, float]] = {}
    for city, (la, lo) in CITIES.items():
        d, n = fetch_tomorrow_temps(la, lo)
        if d is not None:
            temps[city] = (d, n or d)
    if temps:
        P.append("🎖️ <b>Рейтинг городов (дн./ночь)</b>")
        medals = ["🥇", "🥈", "🥉", "4️⃣"]
        for i, (city, (d, n)) in enumerate(
            sorted(temps.items(), key=lambda kv: kv[1][0], reverse=True)[:4]
        ):
            P.append(f"{medals[i]} {city}: {d:.1f}/{n:.1f} °C")
        P.append("———")

    # 5) Качество воздуха и пыльца
    air = get_air() or {}
    P.append("🏙️ <b>Качество воздуха</b>")
    lvl = air.get("lvl", "н/д")
    P.append(
        f"{AIR_EMOJI.get(lvl,'⚪')} {lvl} (AQI {air.get('aqi','н/д')}) | "
        f"PM₂.₅: {pm_color(air.get('pm25'))} | PM₁₀: {pm_color(air.get('pm10'))}"
    )
    pollen = get_pollen() or {}
    if pollen:
        P.append("🌿 <b>Пыльца</b>")
        P.append(
            f"Деревья: {pollen['tree']} | Травы: {pollen['grass']} | "
            f"Сорняки: {pollen['weed']} — риск {pollen['risk']}"
        )
    P.append("———")

    # 6) Геомагнитка и Шуман
    kp, kp_state = get_kp()
    if kp is not None:
        P.append(f"{kp_emoji(kp)} Геомагнитка: Kp={kp:.1f} ({kp_state})")
    else:
        P.append("🧲 Геомагнитка: н/д")

    sch = get_schumann_with_fallback()
    if sch.get("freq") is not None:
        emoji = '⚡' if sch["high"] else '🎵'
        cached = ' (из кеша)' if sch.get("cached") else ''
        P.append(
            f"{emoji} Шуман: {sch['freq']:.2f} Гц / {sch['amp']:.1f} пТ {sch['trend']}{cached}"
        )
    else:
        P.append(f"🎵 Шуман: {sch.get('msg','н/д')}")
    P.append("———")

    # 7) Астрособытия
    astro = astro_events()
    if astro:
        P.append("🌌 <b>Астрособытия</b> – " + " | ".join(astro))
    P.append("———")

    # 8) Вывод и рекомендации
    fog    = w.get("fog_alert", False)
    strong = w.get("strong_wind", False)
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
            disable_web_page_preview=True
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
                allows_multiple_answers=False
            )
        except tg_err.TelegramError as e:
            logging.warning("Poll send error: %s", e)


async def fetch_unsplash_photo() -> Optional[str]:
    if not UNSPLASH_KEY:
        return None
    url = "https://api.unsplash.com/photos/random"
    resp = requests.get(
        url,
        params={"query":"cyprus coast sunset","client_id": UNSPLASH_KEY},
        timeout=15
    )
    return resp.json().get("urls", {}).get("regular")


async def send_photo(bot: Bot, photo_url: str) -> None:
    try:
        await bot.send_photo(
            CHAT_ID,
            photo=photo_url,
            caption="Фото дня • Unsplash"
        )
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
