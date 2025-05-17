#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
post.py – формирует и отправляет ежедневный анонс «Вайбометра».
"""

from __future__ import annotations
import os, asyncio, logging, requests
from typing import Optional, Tuple, Dict

import pendulum
from telegram import Bot, error as tg_err

# ── наши утилиты / API-обёртки ────────────────────────────────────
from utils import (
    compass, clouds_word, wind_phrase, safe, get_fact, pressure_trend,
    WEATHER_ICONS, AIR_EMOJI, K_COLOR
)
from weather  import get_weather
from air      import get_air, get_pollen, get_sst, get_kp
from schumann import get_schumann
from astro    import astro_events
from gpt      import gpt_blurb

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

# ── конфигурация / константы ──────────────────────────────────────
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

# ── прямой запрос max/min Open-Meteo только на завтра ─────────────
def fetch_tomorrow_temps(lat: float, lon: float) -> Tuple[Optional[float], Optional[float]]:
    """Возвращает (t_max, t_min) завтрашнего дня через start_date / end_date."""
    date = TOMORROW.to_date_string()
    j = requests.get(
        "https://api.open-meteo.com/v1/forecast",
        params={
            "latitude":   lat,
            "longitude":  lon,
            "timezone":   "UTC",
            "daily":      "temperature_2m_max,temperature_2m_min",
            "start_date": date,
            "end_date":   date,
        },
        timeout=15,
        headers={"User-Agent": "VayboMeter"}
    ).json()
    d = j.get("daily", {})
    try:
        return d["temperature_2m_max"][0], d["temperature_2m_min"][0]
    except Exception:
        return None, None


# ── сборка сообщения ─────────────────────────────────────────────
def build_msg() -> str:
    P: list[str] = []

    # средняя температура по 4 городам
    sum_d, sum_n, cnt = 0.0, 0.0, 0
    for la, lo in CITIES.values():
        d, n = fetch_tomorrow_temps(la, lo)
        if d is None:
            continue
        sum_d += d
        sum_n += (n if n is not None else d)
        cnt   += 1
    avg_day   = sum_d / (cnt or 1)
    avg_night = sum_n / (cnt or 1)

    # погода Limassol
    lim_lat, lim_lon = CITIES["Limassol"]
    day_max, night_min = fetch_tomorrow_temps(lim_lat, lim_lon)
    if day_max is None or night_min is None:
        raise RuntimeError("Нет прогноза температур на завтра")

    w_lim = get_weather(lim_lat, lim_lon)
    if not w_lim:
        raise RuntimeError("Источники погоды недоступны")

    strong = w_lim.get("strong_wind", False)
    fog    = w_lim.get("fog_alert",   False)

    cur = w_lim.get("current") or w_lim["current_weather"]
    wind_kmh  = cur.get("windspeed") or cur.get("wind_speed", 0)
    wind_deg  = cur.get("winddirection") or cur.get("wind_deg", 0)
    press     = cur.get("pressure") or w_lim["hourly"]["surface_pressure"][0]
    cloud_w   = clouds_word(cur.get("clouds") or w_lim["hourly"]["cloud_cover"][0])

    icon = WEATHER_ICONS.get(cloud_w, "🌦️")
    P.append(f"{icon} Добрый вечер! Погода на завтра на Кипре ({TOMORROW.format('DD.MM.YYYY')})")
    P.append(f"🌡 Средняя темп.: {avg_day:.0f} °C")
    P.append(f"🔽 Давление: {press:.0f} гПа {pressure_trend(w_lim)}")
    P.append(f"📈 Темп. днём/ночью: {day_max:.1f}/{night_min:.1f} °C")
    P.append(f"🌤 Облачность: {cloud_w}")
    P.append(f"💨 Ветер: {wind_phrase(wind_kmh)} ({wind_kmh:.1f} км/ч, {compass(wind_deg)})")
    if strong: P.append("⚠️ Ветер может усилиться")
    if fog:    P.append("🌁 Возможен туман, водите аккуратно")
    P.append("———")

    # рейтинг городов
    temps: Dict[str, Tuple[float, float]] = {}
    for city, (la, lo) in CITIES.items():
        d, n = fetch_tomorrow_temps(la, lo)
        if d is None:
            continue
        temps[city] = (d, n if n is not None else d)

    P.append("🎖️ Рейтинг городов (дн./ночь)")
    medals = ["🥇", "🥈", "🥉", "4️⃣"]
    for i, (city, (d_t, n_t)) in enumerate(sorted(temps.items(),
                                                  key=lambda kv: kv[1][0],
                                                  reverse=True)[:4]):
        P.append(f"{medals[i]} {city}: {d_t:.1f}/{n_t:.1f} °C")
    P.append("———")

    # AQI + пыльца
    air = get_air() or {}
    pm = lambda v: f"{v:.0f}" if v not in (None, "н/д") else "н/д"

    P.append("🏙️ Качество воздуха")
    if air:
        lvl = air["lvl"]
        P.append(f"{AIR_EMOJI.get(lvl,'⚪')} {lvl} (AQI {air['aqi']}) | "
                 f"PM2.5: {pm(air['pm25'])} µg/м³ | "
                 f"PM10: {pm(air['pm10'])} µg/м³")
    else:
        P.append("нет данных")

    pol = get_pollen()
    if pol:
        idx = lambda v: ["нет","низкий","умеренный","высокий",
                         "оч. высокий","экстрим"][int(round(v))]
        P.extend([
            "🌿 Пыльца",
            f"Деревья – {idx(pol['treeIndex'])} | "
            f"Травы – {idx(pol['grassIndex'])} | "
            f"Сорняки – {idx(pol['weedIndex'])}"
        ])
    P.append("———")

    # геомагнитка / Шуман / море / астрособытия
    kp, _ = get_kp()
    sch   = get_schumann()
    sst   = get_sst()
    astro = astro_events()

    if kp is not None:
        color = K_COLOR["low"] if kp < 4 else K_COLOR["mid"] if kp < 6 else K_COLOR["high"]
        P.append(f"{color} Геомагнитка Kp={kp:.1f}")
    else:
        P.append("🧲 Геомагнитка — нет данных")

    if "freq" in sch:
        P.append(f"🎵 Шуман: {sch['freq']:.1f} Гц – фон в норме")
    else:
        P.append(f"🎵 Шуман: {sch['msg']}")

    if sst is not None:
        P.append(f"🌊 Температура воды: {sst:.1f} °C")

    if astro:
        P.append("🌌 Астрособытия – " + " | ".join(astro))
    P.append("———")

    # вывод + GPT-советы
    culprit = ("туман"           if fog else
               "магнитные бури"  if kp and kp >= 5 else
               "низкое давление" if press < 1007 else
               "шальной ветер"   if strong else
               "мини-парад планет")
    summary, tips = gpt_blurb(culprit)
    P.extend([
        f"📜 <b>Вывод</b>\n{summary}",
        "———",
        "✅ <b>Рекомендации</b>",
        *(f"• {t}" for t in tips),
        "———",
        f"📚 {get_fact(TOMORROW)}"
    ])

    return "\n".join(P)


# ── Telegram helpers ──────────────────────────────────────────────
async def send_main_post(bot: Bot) -> None:
    html = build_msg()
    logging.info("Preview: %s", html.replace("\n", " | ")[:200])
    await bot.send_message(CHAT_ID, html, parse_mode="HTML",
                           disable_web_page_preview=True)

async def send_poll_if_friday(bot: Bot) -> None:
    if pendulum.now(TZ).weekday() == 4:  # Friday
        try:
            await bot.send_poll(CHAT_ID, question=POLL_QUESTION,
                                options=POLL_OPTIONS,
                                is_anonymous=False,
                                allows_multiple_answers=False)
        except tg_err.TelegramError as e:
            logging.warning("Poll send error: %s", e)

async def fetch_unsplash_photo() -> Optional[str]:
    if not UNSPLASH_KEY:
        return None
    j = requests.get("https://api.unsplash.com/photos/random",
                     params={"query": "cyprus coast sunset",
                             "client_id": UNSPLASH_KEY},
                     timeout=15).json()
    return j.get("urls", {}).get("regular")

async def send_photo(bot: Bot, photo: str) -> None:
    try:
        await bot.send_photo(CHAT_ID, photo=photo, caption="Фото дня • Unsplash")
    except tg_err.TelegramError as e:
        logging.warning("Photo send error: %s", e)

# ── main entrypoint ───────────────────────────────────────────────
async def main() -> None:
    bot = Bot(token=TOKEN)
    await send_main_post(bot)
    await send_poll_if_friday(bot)
    if UNSPLASH_KEY and TODAY.day % 3 == 0:
        if (url := await fetch_unsplash_photo()):
            await send_photo(bot, url)

if __name__ == "__main__":
    asyncio.run(main())
