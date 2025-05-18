#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
post.py
~~~~~~~~
Формирует и публикует ежедневный пост-дайджест в Telegram-канал.

Новые фичи
──────────
• стрелка тренда давления (utils.pressure_trend)
• «светофор» K-index  (utils.kp_emoji)
• полно-заполненный AQI-блок с резервным источником (air.py)
• пыльца из Open-Meteo (pollen.py)
• тренд резонанса Шумана (schumann.py)
• температура моря (air.get_sst)
"""

# ────────── импорты ──────────────────────────────────────────────
from __future__ import annotations
import os, asyncio, logging, requests
from typing import Dict, Tuple, Optional

import pendulum
from telegram import Bot, error as tg_err

from utils   import (
    compass, clouds_word, wind_phrase, safe, get_fact,
    WEATHER_ICONS, AIR_EMOJI, pressure_trend, kp_emoji
)
from weather import get_weather
from air     import get_air, get_sst, get_kp
from pollen  import get_pollen
from schumann import get_schumann, get_schumann_trend
from gpt     import gpt_blurb

# ────────── константы ────────────────────────────────────────────
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

TZ           = pendulum.timezone("Asia/Nicosia")
TODAY        = pendulum.now(TZ).date()
TOMORROW     = TODAY.add(days=1)
TOKEN        = os.environ["TELEGRAM_TOKEN"]
CHAT_ID      = int(os.environ["CHANNEL_ID"])
UNSPLASH_KEY = os.getenv("UNSPLASH_KEY")

# опрос
POLL_QUESTION = "Как сегодня ваше самочувствие? 🤔"
POLL_OPTIONS  = ["🔥 Полон(а) энергии", "🙂 Нормально",
                 "😴 Слегка вялый(ая)", "🤒 Всё плохо"]

# города Кипра
CITIES: Dict[str, Tuple[float,float]] = {
    "Limassol": (34.707, 33.022),
    "Larnaca" : (34.916, 33.624),
    "Nicosia" : (35.170, 33.360),
    "Pafos"   : (34.776, 32.424),
}

# ────────── быстрый запрос макс/мин Open-Meteo (1 дата) ──────────
def fetch_tomorrow_temps(lat: float, lon: float) -> Tuple[Optional[float], Optional[float]]:
    date = TOMORROW.to_date_string()
    url  = "https://api.open-meteo.com/v1/forecast"
    params = {
        "latitude":   lat,
        "longitude":  lon,
        "timezone":   "UTC",
        "daily":      "temperature_2m_max,temperature_2m_min",
        "start_date": date,
        "end_date":   date,
    }
    j = requests.get(url, params=params, timeout=15).json()
    d = j.get("daily", {})
    tmax = d.get("temperature_2m_max", [None])[0]
    tmin = d.get("temperature_2m_min", [None])[0]
    return tmax, tmin

# ────────── основная сборка сообщения ────────────────────────────
def build_msg() -> str:
    P: list[str] = []

    # 1) средняя температура по 4 городам
    day_vals, night_vals = [], []
    for la, lo in CITIES.values():
        d, n = fetch_tomorrow_temps(la, lo)
        if d is not None and n is not None:
            day_vals.append(d); night_vals.append(n)
    avg_day   = sum(day_vals)   / len(day_vals)
    avg_night = sum(night_vals) / len(night_vals)

    # прогноз для Лимассола
    lat, lon           = CITIES["Limassol"]
    day_max, night_min = fetch_tomorrow_temps(lat, lon)
    w                  = get_weather(lat, lon)
    if not w or day_max is None or night_min is None:
        raise RuntimeError("Погода: не удалось получить данные")

    cur        = w.get("current") or w["current_weather"]
    wind_kmh   = cur.get("windspeed") or cur.get("wind_speed", 0)
    wind_deg   = cur.get("winddirection") or cur.get("wind_deg", 0)
    press      = cur.get("pressure") or w["hourly"]["surface_pressure"][0]
    cloud_w    = clouds_word(cur.get("clouds") or w["hourly"]["cloud_cover"][0])
    trend      = pressure_trend(w)
    strong     = w.get("strong_wind", False)
    fog        = w.get("fog_alert" , False)

    icon = WEATHER_ICONS.get(cloud_w, "🌦️")
    P += [
        f"{icon} Добрый вечер! Погода на завтра на Кипре ({TOMORROW.format('DD.MM.YYYY')})",
        f"🌡 Средняя темп.: {avg_day:.0f} °C",
        f"📈 Темп. днём/ночью: {day_max:.1f} / {night_min:.1f} °C",
        f"🌤 Облачность: {cloud_w}",
        f"💨 Ветер: {wind_phrase(wind_kmh)} ({wind_kmh:.0f} км/ч, {compass(wind_deg)})",
        f"🔽 Давление: {press:.0f} гПа {trend}",
    ]
    if strong: P.append("⚠️ Возможны порывы ветра >30 км/ч")
    if fog:    P.append("🌁 Возможен туман – осторожно на дороге")
    P.append("———")

    # 2) рейтинг городов
    temps = {c: fetch_tomorrow_temps(*coords) for c,coords in CITIES.items()}
    temps = {c:(d,n if n else d) for c,(d,n) in temps.items() if d}
    P.append("🎖️ Рейтинг городов (дн./ночь)")
    medals = ["🥇","🥈","🥉","4️⃣"]
    for i,(c,(d,n)) in enumerate(sorted(temps.items(), key=lambda kv: kv[1][0], reverse=True)[:4]):
        P.append(f"{medals[i]} {c}: {d:.1f}/{n:.1f} °C")
    P.append("———")

    # 3) качество воздуха
    air = get_air()
    pm   = lambda v: f"{v:.0f}" if v not in (None,"н/д") else "н/д"
    P.append("🏙️ Качество воздуха")
    P.append(f"{AIR_EMOJI[air['lvl']]} {air['lvl'].title()} (AQI {air['aqi']}) | "
             f"PM₂.₅: {pm(air['pm25'])} | PM₁₀: {pm(air['pm10'])}")
    # 4) пыльца
    pol = get_pollen()
    if pol:
        risk = pol["risk"]
        P.append(f"🌿 Пыльца • риск: <b>{risk}</b> "
                 f"(деревья {pol['tree']} | травы {pol['grass']} | сорняки {pol['weed']})")
    P.append("———")

    # 5) геомагнитка
    kp_val, kp_state = get_kp()
    if kp_val is not None:
        P.append(f"{kp_emoji(kp_val)} Геомагнитка Kₚ={kp_val:.1f} – {kp_state}")
    else:
        P.append("🧲 Геомагнитка: нет данных")

    # 6) Шуман
    sch = get_schumann()
    if "freq" in sch:
        trend_s = get_schumann_trend() or "→"
        P.append(f"🎵 Шуман: {sch['freq']:.2f} Гц {trend_s} "
                 f"(ампл. {sch['amp']:.1f})")
    else:
        P.append(f"🎵 Шуман: {sch['msg']}")

    # 7) море
    if (sst := get_sst()) is not None:
        P.append(f"🌊 Температура моря: {sst:.1f} °C")
    P.append("———")

    # 8) астрособытия
    astro = astro_events()
    if astro:
        P.append("🌌 Астрособытия – " + " | ".join(astro))
        P.append("———")

    # 9) вывод + советы
    culprit = ("туман" if fog else
               "магнитные бури" if kp_state=="буря" else
               "низкое давление" if press < 1007 else
               "шальной ветер" if strong else
               "мини-парад планет")
    summary, tips = gpt_blurb(culprit)
    P += [f"📜 <b>Вывод</b>\n{summary}", "———", "✅ <b>Рекомендации</b>"]
    P += [f"• {t}" for t in tips]
    P.append("———")

    # 10) факт дня
    P.append(f"📚 {get_fact(TOMORROW)}")

    return "\n".join(P)

# ────────── Telegram helper-ы ─────────────────────────────────────
async def send_main_post(bot: Bot) -> None:
    html = build_msg()
    logging.info("Preview: %s", html.replace("\n", " | ")[:220])
    await bot.send_message(CHAT_ID, html, parse_mode="HTML",
                           disable_web_page_preview=True)

async def send_poll_if_friday(bot: Bot) -> None:
    if pendulum.now(TZ).weekday() == 4:
        await bot.send_poll(CHAT_ID, question=POLL_QUESTION,
                            options=POLL_OPTIONS,
                            is_anonymous=False)

async def fetch_unsplash_photo() -> Optional[str]:
    if not UNSPLASH_KEY:
        return None
    res = requests.get("https://api.unsplash.com/photos/random",
                       params={"query":"cyprus coast sunset",
                               "client_id":UNSPLASH_KEY},
                       timeout=15).json()
    return res.get("urls",{}).get("regular")

async def main() -> None:
    bot = Bot(token=TOKEN)
    await send_main_post(bot)
    await send_poll_if_friday(bot)
    if UNSPLASH_KEY and (TODAY.day % 3 == 0):
        if (url := await fetch_unsplash_photo()):
            await bot.send_photo(CHAT_ID, url, caption="Фото дня • Unsplash")
    logging.info("All tasks done ✓")

if __name__ == "__main__":
    asyncio.run(main())
