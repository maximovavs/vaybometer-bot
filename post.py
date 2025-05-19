#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import asyncio
import logging
from typing import Dict, Tuple, Optional, List

import requests
import pendulum
from telegram import Bot, error as tg_err

from utils import (
    compass, clouds_word, wind_phrase, safe,
    WEATHER_ICONS, AIR_EMOJI, get_fact,
    pressure_trend, kp_emoji, pm_color,
)
from weather  import get_weather
from air      import get_air, get_kp, get_sst
from pollen   import get_pollen
from schumann import get_schumann, get_schumann_trend
from astro    import astro_events
from gpt      import gpt_blurb

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

# ─────────── константы ──────────────────────────────────────────
TZ    = pendulum.timezone("Asia/Nicosia")
TODAY = pendulum.now(TZ).date()
TOM   = TODAY.add(days=1)

TOKEN   = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = int(os.getenv("CHANNEL_ID", "0"))
UNSPLASH_KEY = os.getenv("UNSPLASH_KEY")

CITIES: Dict[str, Tuple[float, float]] = {
    "Limassol": (34.707, 33.022),
    "Larnaca" : (34.916, 33.624),
    "Nicosia" : (35.170, 33.360),
    "Pafos"   : (34.776, 32.424),
}

POLL_QUESTION = "Как сегодня ваше самочувствие? 🤔"
POLL_OPTIONS  = ["🔥 Полон(а) энергии", "🙂 Нормально",
                 "😴 Немного вялый(ая)", "🤒 Всё плохо"]


# ─────────── helper: завтрашний max/min из Open-Meteo ────────────
def fetch_tomorrow_temps(lat: float, lon: float) -> Tuple[Optional[float], Optional[float]]:
    date = TOM.to_date_string()
    r = requests.get(
        "https://api.open-meteo.com/v1/forecast",
        params={
            "latitude": lat, "longitude": lon, "timezone": "UTC",
            "daily": "temperature_2m_max,temperature_2m_min",
            "start_date": date, "end_date": date,
        },
        timeout=15,
        headers={"User-Agent": "VayboMeter"},
    )
    r.raise_for_status()
    j = r.json().get("daily", {})
    t_max = j.get("temperature_2m_max", [None])[0]
    t_min = j.get("temperature_2m_min", [None])[0]
    return t_max, t_min


# ─────────── build_msg ──────────────────────────────────────────
def build_msg() -> str:
    P: List[str] = []

    # ——— средние температуры по острову ———
    temps: List[Tuple[float, float]] = []
    for la, lo in CITIES.values():
        d, n = fetch_tomorrow_temps(la, lo)
        if d is not None and n is not None:
            temps.append((d, n))
    if not temps:
        raise RuntimeError("Нет данных температур с Open-Meteo")

    avg_day   = sum(d for d, _ in temps) / len(temps)
    avg_night = sum(n for _, n in temps) / len(temps)

    # ——— подробности для Лимассола ———
    lim_lat, lim_lon = CITIES["Limassol"]
    w = get_weather(lim_lat, lim_lon)
    if not w:
        raise RuntimeError("Источники погоды недоступны")

    strong = w.get("strong_wind", False)
    fog    = w.get("fog_alert", False)

    cur   = w["current"]
    wind_kmh  = cur["windspeed"]
    wind_deg  = cur["winddirection"]
    press     = cur["pressure"]
    cloud_w   = clouds_word(cur.get("clouds", 0))

    day_max, night_min = fetch_tomorrow_temps(lim_lat, lim_lon)

    # ——— заголовок ———
    icon = WEATHER_ICONS.get(cloud_w, "🌦️")
    P.append(f"{icon} <b>Добрый вечер!</b> Погода на завтра на Кипре "
             f"({TOM.format('DD.MM.YYYY')})")
    P.append(f"🌡 Средняя темп.: {avg_day:.0f} °C")
    P.append(f"📈 Темп. днём/ночью: {safe(day_max,' °C')} / {safe(night_min,' °C')}")
    P.append(f"🌤 Облачность: {cloud_w}")
    P.append(f"💨 Ветер: {wind_phrase(wind_kmh)} ({wind_kmh:.1f} км/ч, {compass(wind_deg)})")
    P.append(f"🔽 Давление: {press:.0f} гПа {pressure_trend(w)}")
    if strong: P.append("⚠️ Порывы ветра могут усиливаться")
    if fog:    P.append("🌁 Возможен туман — будьте внимательны")
    P.append("———")

    # ——— рейтинг городов ———
    city_t: List[Tuple[str, float, float]] = []
    for c, (la, lo) in CITIES.items():
        d, n = fetch_tomorrow_temps(la, lo)
        if d is None or n is None: continue
        city_t.append((c, d, n))
    city_t.sort(key=lambda x: x[1], reverse=True)

    medals = "🥇🥈🥉4️⃣".split(" ")
    P.append("🎖️ <b>Рейтинг городов (дн./ночь)</b>")
    for i, (c, d, n) in enumerate(city_t[:4]):
        P.append(f"{medals[i]} {c}: {d:.1f}/{n:.1f} °C")
    P.append("———")

    # ——— AQI + пыльца ———
    air = get_air()
    P.append("🏙️ <b>Качество воздуха</b>")
    P.append(f"{AIR_EMOJI[air['lvl']]} {air['lvl'].capitalize()} (AQI {air['aqi']}) | "
             f"PM₂.₅: {pm_color(air['pm25'])} | PM₁₀: {pm_color(air['pm10'])}")

    pol = get_pollen()
    if pol:
        risk = max(pol["risk"].values())
        P.append(f"🌿 Пыльца – уровень {risk} "
                 f"(деревья {pol['tree']}, травы {pol['grass']}, сорняки {pol['weed']})")
    P.append("———")

    # ——— геомагнитка | Шуман | море | астрособытия ———
    kp, _ = get_kp()
    if kp is not None:
        P.append(f"{kp_emoji(kp)} Геомагнитка Kp ={kp:.1f}")
    else:
        P.append("🧲 Геомагнитка: нет данных")

    sch = get_schumann()
    trend = get_schumann_trend()
    if "freq" in sch:
        P.append(f"🎵 Шуман: {sch['freq']:.2f} Гц {trend} – "
                 f\"{'⚡️ повышенные вибрации' if sch.get('high') else 'фон в норме'}\"")
    else:
        P.append(f"🎵 Шуман: {sch['msg']}")

    sst = get_sst()
    if sst is not None:
        P.append(f"🌊 Температура воды: {sst:.1f} °C (Open-Meteo)")

    astro = astro_events()
    if astro:
        P.append("🌌 " + " | ".join(astro))
    P.append("———")

    # ——— вывод + советы GPT ———
    culprit = ("туман" if fog else
               "магнитные бури" if kp and kp >= 5 else
               "низкое давление" if press < 1007 else
               "шальной ветер" if strong else
               "мини-парад планет")
    summary, tips = gpt_blurb(culprit)
    P.append(f"📜 <b>Вывод</b>\n{summary}")
    P.append("✅ <b>Рекомендации</b>")
    for t in tips:
        P.append(f"• {t}")
    P.append("———")
    P.append(f"📚 {get_fact(TOM)}")

    return "\n".join(P)


# ─────────── отправка ───────────────────────────────────────────
async def send_main_post(bot: Bot) -> None:
    html = build_msg()
    logging.info("Preview: %s", html.replace('\n', ' | ')[:200])
    await bot.send_message(
        CHAT_ID, html,
        parse_mode="HTML",
        disable_web_page_preview=True,
    )

async def send_poll_if_friday(bot: Bot) -> None:
    if pendulum.now(TZ).weekday() == 4:
        await bot.send_poll(
            CHAT_ID, POLL_QUESTION, POLL_OPTIONS,
            is_anonymous=False, allows_multiple_answers=False,
        )

async def fetch_unsplash_photo() -> Optional[str]:
    if not UNSPLASH_KEY:
        return None
    r = requests.get(
        "https://api.unsplash.com/photos/random",
        params={"query": "cyprus coast sunset", "client_id": UNSPLASH_KEY},
        timeout=15,
    )
    try:
        return r.json()["urls"]["regular"]
    except Exception:
        return None

async def send_photo(bot: Bot, url: str) -> None:
    try:
        await bot.send_photo(CHAT_ID, url, caption="Фото дня • Unsplash")
    except tg_err.TelegramError as e:
        logging.warning("Photo send error: %s", e)

async def main() -> None:
    bot = Bot(TOKEN)
    await send_main_post(bot)
    await send_poll_if_friday(bot)
    if UNSPLASH_KEY and (TODAY.day % 3 == 0):
        if (url := await fetch_unsplash_photo()):
            await send_photo(bot, url)

if __name__ == "__main__":
    asyncio.run(main())
