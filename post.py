#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
post.py
~~~~~~~~
Формирует и отправляет вечерний прогноз «VayboMeter»-бота:
- Погода, море, воздух, пыльца, Шуман, геомагнитка
- Короткий астро-блок с фазой Луны, 3-мя советами и Void-of-Course

Изменения 2025-05-XX
▪︎ в раздел «🌌 Астрособытия» добавлена строка Void-of-Course
  (берётся из поля «void_of_course» календаря).
"""

import os
import asyncio
import logging
from pathlib import Path
from typing import Optional, Tuple, Dict, Any

import json
import pendulum
import requests
from requests.exceptions import RequestException
from telegram import Bot, error as tg_err

# ── внутренние модули ──────────────────────────────────────────
from utils import (
    compass, clouds_word, wind_phrase, get_fact,
    WEATHER_ICONS, AIR_EMOJI, pressure_trend, kp_emoji, pm_color
)
from weather  import get_weather
from air      import get_air, get_sst, get_kp
from pollen   import get_pollen
from schumann import get_schumann
from astro    import astro_events
from gpt      import gpt_blurb
from lunar    import get_day_lunar_info     # ◀︎ понадобится для VOC

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

# ─────────── Constants ─────────────────────────────────────────
TZ           = pendulum.timezone("Asia/Nicosia")
TODAY        = pendulum.now(TZ).date()
TOMORROW     = TODAY.add(days=1)

TOKEN        = os.getenv("TELEGRAM_TOKEN", "")
CHAT_ID      = int(os.getenv("CHANNEL_ID", 0))
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

# ─────────── Schumann fallback (без изменений) ─────────────────
def get_schumann_with_fallback() -> Dict[str, Any]:
    sch = get_schumann()
    if sch.get("freq") is not None:
        return sch

    cache_path = Path(__file__).parent / "schumann_hourly.json"
    if cache_path.exists():
        try:
            arr = json.loads(cache_path.read_text())
            if arr:
                last = arr[-1]
                pts = arr[-24:]
                freqs = [p["freq"] for p in pts]
                if len(freqs) >= 2:
                    avg   = sum(freqs[:-1]) / (len(freqs)-1)
                    delta = freqs[-1] - avg
                    trend = "↑" if delta >= 0.1 else "↓" if delta <= -0.1 else "→"
                else:
                    trend = "→"
                return {
                    "freq":   round(last["freq"], 2),
                    "amp":    round(last["amp"], 1),
                    "high":   last["freq"] > 8.0 or last["amp"] > 100.0,
                    "trend":  trend,
                    "cached": True,
                }
        except Exception as e:
            logging.warning("Schumann fallback parse error: %s", e)

    return sch

# ─────────── helpers ───────────────────────────────────────────
def fetch_tomorrow_temps(lat: float, lon: float) -> Tuple[Optional[float], Optional[float]]:
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

# ─────────── Main message builder ─────────────────────────────
def build_msg() -> str:
    P: list[str] = []

    # 1) Заголовок
    P.append(f"<b>🌅 Добрый вечер! Погода на завтра ({TOMORROW.format('DD.MM.YYYY')})</b>")

    # 2) Температура моря
    if (sst := get_sst()) is not None:
        P.append(f"🌊 Темп. моря: {sst:.1f} °C")

    # 3) Прогноз для Limassol
    lat, lon = CITIES["Limassol"]
    day_max, night_min = fetch_tomorrow_temps(lat, lon)
    w = get_weather(lat, lon) or {}
    cur = w.get("current") or w.get("current_weather", {})

    avg_temp = (day_max + night_min) / 2 if day_max and night_min else cur.get("temperature", 0)
    wind_kmh = cur.get("windspeed") or cur.get("wind_speed", 0.0)
    wind_deg = cur.get("winddirection") or cur.get("wind_deg", 0.0)
    press    = cur.get("pressure") or w.get("hourly", {}).get("surface_pressure", [0])[0]

    clouds_pct = cur.get("clouds") or w.get("hourly", {}).get("cloud_cover", [0])[0]
    P.append(
        f"🌡️ Ср. темп: {avg_temp:.0f} °C • {clouds_word(clouds_pct)} "
        f"• 💨 {wind_kmh:.1f} км/ч ({compass(wind_deg)}) "
        f"• 💧 {press:.0f} гПа {pressure_trend(w)}"
    )
    P.append("———")

    # 4) Качество воздуха
    air = get_air() or {}
    P.append("🏙️ <b>Качество воздуха</b>")
    lvl = air.get("lvl", "н/д")
    P.append(
        f"{AIR_EMOJI.get(lvl,'⚪')} {lvl} (AQI {air.get('aqi','н/д')}) | "
        f"PM₂.₅: {pm_color(air.get('pm25'))} | PM₁₀: {pm_color(air.get('pm10'))}"
    )
    if (pollen := get_pollen()):
        P.append("🌿 <b>Пыльца</b>")
        P.append(
            f"Деревья: {pollen['tree']} | Травы: {pollen['grass']} | "
            f"Сорняки: {pollen['weed']} — риск {pollen['risk']}"
        )
    P.append("———")

    # 5) Геомагнитка + Шуман
    kp, kp_state = get_kp()
    P.append(f"{kp_emoji(kp)} Геомагнитка: Kp={kp:.1f} ({kp_state})" if kp else "🧲 Геомагнитка: н/д")

    sch = get_schumann_with_fallback()
    if sch.get("freq") is not None:
        emoji = "⚡" if sch["high"] else "🎵"
        cached = " (кэш)" if sch.get("cached") else ""
        P.append(f"{emoji} Шуман: {sch['freq']:.2f} Гц / {sch['amp']:.1f} pT {sch['trend']}{cached}")
    else:
        P.append("🎵 Шуман: н/д")
    P.append("———")

    # 6) Астрособытия
    P.append("🌌 <b>Астрособытия</b>")
    for line in astro_events():
        P.append(line)

    # добавляем строку Void-of-Course (для текущего дня в TZ)
    info_today = get_day_lunar_info(TODAY)
    if info_today:
        voc = info_today.get("void_of_course", {})
        if voc.get("start") and voc.get("end"):
            P.append(f"🕑 Void-of-Course: {voc['start']} → {voc['end']}")
    P.append("———")

    # 7) GPT-вывод
    culprit = "погода"
    summary, tips = gpt_blurb(culprit)
    P.append(f"📜 <b>Вывод</b>\n{summary}")
    P.append("———")
    P.append("✅ <b>Рекомендации</b>")
    for t in tips:
        P.append(f"• {t}")
    P.append("———")
    P.append(f"📚 {get_fact(TOMORROW)}")

    return "\n".join(P)

# ─────────── Telegram I/O ─────────────────────────────────────
async def send_main_post(bot: Bot) -> None:
    html = build_msg()
    try:
        await bot.send_message(
            CHAT_ID, html,
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

async def main() -> None:
    bot = Bot(token=TOKEN)
    await send_main_post(bot)
    await send_poll_if_friday(bot)

if __name__ == "__main__":
    asyncio.run(main())
