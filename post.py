#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
post.py  ▸ ежедневная карточка «VayboMeter»

Обновления (май-2025)
──────────────────────────────────────────────────────────────────
• Давление со стрелкой trend   – utils.pressure_trend()
• AQI-блок: цветные PM-значения (pm_color) + fallback-API
• Пыльца: новый pollen.py ⇒ цифры + риск
• Геомагнитка: kp_emoji() «светофор»
• Шуман: частота + тренд get_schumann_trend()
• Температура воды: 🌊 … °C (если есть данные)
• Расширенный get_fact() – конец сообщения разнообразен
"""

from __future__ import annotations

# ────────── std / pypi ───────────────────────────────────────────
import os, asyncio, logging, requests
from typing import Dict, Tuple, Optional

import pendulum
from telegram import Bot, error as tg_err

# ────────── наши модули ─────────────────────────────────────────
from utils     import (
    compass, clouds_word, wind_phrase, safe, get_fact,
    WEATHER_ICONS, AIR_EMOJI,
    pm_color, kp_emoji, pressure_trend
)
from weather   import get_weather, fetch_tomorrow_temps  # fetch_* вынесена туда
from air       import get_air, get_sst, get_kp
from pollen    import get_pollen
from schumann  import get_schumann, get_schumann_trend
from astro     import astro_events
from gpt       import gpt_blurb

# ────────── runtime / env ───────────────────────────────────────
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

TZ           = pendulum.timezone("Asia/Nicosia")
TODAY        = pendulum.now(TZ).date()
TOMORROW     = TODAY.add(days=1)

TOKEN        = os.environ["TELEGRAM_TOKEN"]
CHAT_ID      = int(os.environ["CHANNEL_ID"])
UNSPLASH_KEY = os.getenv("UNSPLASH_KEY")

POLL_QUESTION = "Как сегодня ваше самочувствие? 🤔"
POLL_OPTIONS  = ["🔥 Полон(а) энергии", "🙂 Нормально",
                 "😴 Слегка вялый(ая)", "🤒 Всё плохо"]

# координаты основных городов
CITIES: Dict[str, Tuple[float, float]] = {
    "Limassol": (34.707, 33.022),
    "Larnaca" : (34.916, 33.624),
    "Nicosia" : (35.170, 33.360),
    "Pafos"   : (34.776, 32.424),
}

# ─────────────────────────────────────────────────────────────────
def build_msg() -> str:
    P: list[str] = []

    # 1️⃣ Прогноз на завтра (Лимассол) ────────────────────────────
    lat, lon = CITIES["Limassol"]
    day_max, night_min = fetch_tomorrow_temps(lat, lon)
    if day_max is None or night_min is None:
        raise RuntimeError("Не удалось получить температуру на завтра")

    w0 = get_weather(lat, lon)
    if not w0:
        raise RuntimeError("Источники погоды недоступны")

    strong = w0.get("strong_wind", False)
    fog    = w0.get("fog_alert",   False)

    cur = w0.get("current") or w0["current_weather"]
    wind_kmh = cur.get("windspeed") or cur.get("wind_speed") or 0.0
    wind_deg = cur.get("winddirection") or cur.get("wind_deg") or 0.0
    press    = cur.get("pressure") or w0["hourly"]["surface_pressure"][0]
    clouds_pct = cur.get("clouds") or w0["hourly"]["cloud_cover"][0]
    cloud_w  = clouds_word(clouds_pct)

    press_arrow = pressure_trend(w0)

    icon = WEATHER_ICONS.get(cloud_w, "🌦️")
    P += [
        f"{icon} <b>Погода на завтра в Лимассоле {TOMORROW.format('DD.MM.YYYY')}</b>",
        f"<b>Темп. днём/ночью:</b> {day_max:.1f}/{night_min:.1f} °C",
        f"<b>Облачность:</b> {cloud_w}",
        f"<b>Ветер:</b> {wind_phrase(wind_kmh)} "
        f"({wind_kmh:.1f} км/ч, {compass(wind_deg)})",
        f"<b>Давление:</b> {press:.0f} гПа {press_arrow}",
    ]
    if strong: P.append("⚠️ Возможен усиление ветра")
    if fog:    P.append("🌁 Утром возможен туман – внимание на дорогах")
    P.append("———")

    # 2️⃣ Рейтинг городов ─────────────────────────────────────────
    city_t: Dict[str, Tuple[float,float]] = {}
    for city,(la,lo) in CITIES.items():
        d, n = fetch_tomorrow_temps(la, lo)
        if d is None: continue
        city_t[city] = (d, n if n is not None else d)

    P.append("🎖️ <b>Рейтинг городов (дн./ночь)</b>")
    medals = ["🥇","🥈","🥉","4️⃣"]
    for i,(c,(d,n)) in enumerate(sorted(city_t.items(),
                                       key=lambda kv: kv[1][0], reverse=True)[:4]):
        P.append(f"{medals[i]} {c}: {d:.1f}/{n:.1f} °C")
    P.append("———")

    # 3️⃣ Качество воздуха ───────────────────────────────────────
    air = get_air()
    P.append("🏙️ <b>Качество воздуха</b>")
    pm25_txt = f"{pm_color(air['pm25'])} µg/м³"
    pm10_txt = f"{pm_color(air['pm10'])} µg/м³"
    if air["aqi"] != "н/д":
        P.append(
            f"{AIR_EMOJI[air['lvl']]} {air['lvl']} (AQI {air['aqi']}) | "
            f"PM₂.₅: {pm25_txt} | PM₁₀: {pm10_txt}"
        )
    else:
        P.append("нет данных")
    # 4️⃣ Пыльца ----------------------------------------------------
    pol = get_pollen()
    if pol:
        risk = pol["risk"]
        P.append(
            f"🌿 Деревья {pol['tree']} / Травы {pol['grass']} / "
            f"Сорняки {pol['weed']} – {risk} риск"
        )
    P.append("———")

    # 5️⃣ Геомагнитка • Шуман • море • астрособытия ---------------
    kp, _ = get_kp()
    if kp is not None:
        P.append(f"🧲 Геомагнитка {kp_emoji(kp)} Kp {kp:.1f}")
    else:
        P.append("🧲 Геомагнитка – нет данных")

    sch = get_schumann()
    if "freq" in sch:
        trend = get_schumann_trend()
        arrow = "↑" if trend == "up" else "↓" if trend == "down" else "→"
        P.append(f"🎵 Шуман: {sch['freq']:.2f} Гц {arrow}")
    else:
        P.append(f"🎵 Шуман: {sch['msg']}")

    sst = get_sst()
    if sst is not None:
        P.append(f"🌊 Вода: {sst:.1f} °C (Open-Meteo)")

    astro = astro_events()
    if astro:
        P.append("🌌 Астрособытия – " + " | ".join(astro))
    P.append("———")

    # 6️⃣ Вывод и советы ------------------------------------------
    culprit = ("туман"            if fog            else
               "магнитные бури"   if kp and kp >= 5 else
               "низкое давление"  if press < 1007   else
               "шальной ветер"    if strong         else
               "лунное влияние")
    summary, tips = gpt_blurb(culprit)

    P.append(f"📜 <b>Вывод</b>\n{summary}")
    P.append("———")
    P.append("✅ <b>Рекомендации</b>")
    P.extend(f"• {t}" for t in tips)
    P.append("———")
    P.append(f"📚 {get_fact(TOMORROW)}")

    return "\n".join(P)

# ─────────────────────────────────────────────────────────────────
async def send_main_post(bot: Bot) -> None:
    html = build_msg()
    logging.info("Preview: %s", html.replace("\n", " | ")[:220])
    await bot.send_message(
        CHAT_ID, html, parse_mode="HTML",
        disable_web_page_preview=True
    )

async def send_poll_if_friday(bot: Bot) -> None:
    if pendulum.now(TZ).weekday() == 4:
        await bot.send_poll(
            CHAT_ID, question=POLL_QUESTION, options=POLL_OPTIONS,
            is_anonymous=False, allows_multiple_answers=False
        )

async def fetch_unsplash_photo() -> Optional[str]:
    if not UNSPLASH_KEY:
        return None
    j = requests.get(
        "https://api.unsplash.com/photos/random",
        params={"query": "cyprus coast sunset", "client_id": UNSPLASH_KEY},
        timeout=15
    ).json()
    return j.get("urls", {}).get("regular")

async def send_photo(bot: Bot, url: str) -> None:
    try:
        await bot.send_photo(CHAT_ID, photo=url, caption="Фото дня • Unsplash")
    except tg_err.TelegramError as e:
        logging.warning("Photo send error: %s", e)

# ─────────────────────────────────────────────────────────────────
async def main() -> None:
    bot = Bot(token=TOKEN)
    await send_main_post(bot)
    await send_poll_if_friday(bot)

    # каждые 3 дня – красивая фотография
    if UNSPLASH_KEY and TODAY.day % 3 == 0:
        if (photo := await fetch_unsplash_photo()):
            await send_photo(bot, photo)

    logging.info("All tasks done ✓")

if __name__ == "__main__":
    asyncio.run(main())
