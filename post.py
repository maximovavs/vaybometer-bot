#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
post.py
~~~~~~~~
Формирует карточку «Погода + Здоровье» и публикует её в Telegram-канал.

▪ средняя погода по Кипру + подробности для Лимассола  
▪ рейтинг 4-х городов (дн./ночь)  
▪ воздух (AQ + PM) — два источника, всегда заполнен  
▪ пыльца (Open-Meteo Pollen)  
▪ геомагнитка со «светофором»  
▪ резонанс Шумана (частота + тренд ↑ ↓ →)  
▪ температура воды  
▪ астрособытия (фаза + ближайшее явление)  
▪ вывод GPT и «факт дня»
"""

from __future__ import annotations
import os, asyncio, logging, requests
from typing import Dict, Tuple, List, Optional

import pendulum
from telegram import Bot, error as tg_err

# — собственные модули —
from utils   import (
    WEATHER_ICONS, AIR_EMOJI,
    compass, clouds_word, wind_phrase, safe,
    pressure_trend, kp_emoji, pm_color, get_fact
)
from weather   import get_weather
from air       import get_air, get_kp, get_sst
from pollen    import get_pollen
from schumann  import get_schumann, get_schumann_trend
from astro     import astro_events
from gpt       import gpt_blurb

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

# ── Telegram / окружение ─────────────────────────────────────────
TZ           = pendulum.timezone("Asia/Nicosia")
TODAY        = pendulum.now(TZ).date()
TOMORROW     = TODAY.add(days=1)
TOKEN        = os.environ["TELEGRAM_TOKEN"]
CHAT_ID      = int(os.environ["CHANNEL_ID"])
UNSPLASH_KEY = os.getenv("UNSPLASH_KEY")

POLL_Q   = "Как сегодня ваше самочувствие?"
POLL_OPT = ["🔥 Полон(а) энергии", "🙂 Нормально",
            "😴 Немного вялый(ая)", "🤒 Плохо"]

# ── расположение городов ─────────────────────────────────────────
CITIES: Dict[str, Tuple[float, float]] = {
    "Limassol": (34.707, 33.022),
    "Larnaca" : (34.916, 33.624),
    "Nicosia" : (35.170, 33.360),
    "Pafos"   : (34.776, 32.424),
}

# ── быстрый запрос tmax / tmin ровно на завтра ───────────────────
def fetch_tomorrow_temps(lat: float, lon: float) -> Tuple[Optional[float], Optional[float]]:
    date = TOMORROW.to_date_string()               # YYYY-MM-DD
    try:
        j = requests.get(
            "https://api.open-meteo.com/v1/forecast",
            params={
                "latitude": lat, "longitude": lon,
                "timezone": "UTC",
                "start_date": date, "end_date": date,
                "daily": "temperature_2m_max,temperature_2m_min",
            },
            timeout=15
        ).json()
        tmax = float(j["daily"]["temperature_2m_max"][0])
        tmin = float(j["daily"]["temperature_2m_min"][0])
        return tmax, tmin
    except Exception as e:
        logging.warning("Tomorrow temps fetch (%.3f,%.3f) error: %s", lat, lon, e)
        return None, None

# ──────────────────────────────────────────────────────────────────
def build_msg() -> str:
    P: List[str] = []

    # 1️⃣ средняя температура по Кипру
    all_t: List[Tuple[float, float]] = []
    for la, lo in CITIES.values():
        d, n = fetch_tomorrow_temps(la, lo)
        if d is not None and n is not None:
            all_t.append((d, n))
    if not all_t:
        raise RuntimeError("Ни один город не вернул завтрашние t°")
    avg_day   = sum(d for d, _ in all_t) / len(all_t)
    avg_night = sum(n for _, n in all_t) / len(all_t)

    # 2️⃣ подробности для Лимассола
    la0, lo0  = CITIES["Limassol"]
    day_max, night_min = fetch_tomorrow_temps(la0, lo0)
    if day_max is None or night_min is None:
        raise RuntimeError("Open-Meteo не вернул t° для Лимассола")

    w0 = get_weather(la0, lo0)
    if not w0:
        raise RuntimeError("get_weather() не дал current_weather")

    cur        = w0["current"]
    wind_kmh   = cur["windspeed"]
    wind_deg   = cur["winddirection"]
    press      = cur["pressure"]
    clouds_pct = cur["clouds"]
    cloud_w    = clouds_word(clouds_pct)

    icon = WEATHER_ICONS.get(cloud_w, "🌦️")
    P += [
        f"{icon} <b>Добрый вечер! Погода на завтра на Кипре "
        f"({TOMORROW.format('DD.MM.YYYY')})</b>",
        f"🌡 Средняя темп.: {avg_day:.0f} °C",
        f"📈 Темп. днём/ночью: {day_max:.1f} / {night_min:.1f} °C",
        f"🌤 Облачность: {cloud_w}",
        f"💨 Ветер: {wind_phrase(wind_kmh)} "
        f"({wind_kmh:.0f} км/ч, {compass(wind_deg)})",
        f"🔽 Давление: {press:.0f} гПа {pressure_trend(w0)}",
        "———",
    ]

    # 3️⃣ рейтинг городов (дн./ночь)
    rating: List[Tuple[str, float, float]] = []
    for city, (la, lo) in CITIES.items():
        d, n = fetch_tomorrow_temps(la, lo)
        if d is None:
            continue
        rating.append((city, d, n if n is not None else d))
    rating.sort(key=lambda x: x[1], reverse=True)

    medals = ["🥇", "🥈", "🥉", "4️⃣"]
    P.append("🎖️ Рейтинг городов (дн./ночь)")
    for i, (c, d, n) in enumerate(rating[:4]):
        P.append(f"{medals[i]} {c}: {d:.1f}/{n:.1f} °C")
    P.append("———")

    # 4️⃣ воздух
    air = get_air()
    pm = lambda v: f"{v:.0f}" if v not in (None, "н/д") else "н/д"
    P.append("🏙️ Качество воздуха")
    P.append(
        f"{AIR_EMOJI.get(air['lvl'],'⚪')} {air['lvl']} "
        f"(AQI {air['aqi']}) | "
        f"PM₂.₅: {pm_color(pm(air['pm25']))} | "
        f"PM₁₀: {pm_color(pm(air['pm10']))}"
    )

    # 5️⃣ пыльца
    pol = get_pollen()
    if pol:
        P.append(f"🌿 Пыльца – риск: {pol['risk']}")
        P.append(f"Деревья: {pol['tree']}  |  Травы: {pol['grass']}  |  Сорняки: {pol['weed']}")
    P.append("———")

    # 6️⃣ геомагнитка
    kp_val, _ = get_kp()
    if kp_val is not None:
        P.append(f"{kp_emoji(kp_val)} Геомагнитка Kp={kp_val:.1f}")
    else:
        P.append("🧲 Геомагнитка – нет данных")

    # 7️⃣ резонанс Шумана
    sch = get_schumann()
    if "freq" in sch:
        trend = get_schumann_trend()
        arrow = "↑" if trend > 0 else "↓" if trend < 0 else "→"
        status = "⚡️ повышен" if sch.get("high") else "фон в норме"
        P.append(f"🎵 Шуман: {sch['freq']:.2f} Гц {arrow} – {status}")
    else:
        P.append(f"🎵 Шуман: {sch['msg']}")
    P.append("———")

    # 8️⃣ температура моря
    sst = get_sst()
    if sst is not None:
        P.append(f"🌊 Вода Средиземного моря: {sst:.1f} °C")
        P.append("———")

    # 9️⃣ астрособытия
    astro = astro_events()
    if astro:
        P.append("🌌 Астрособытия – " + " | ".join(astro))
        P.append("———")

    # 🔟 вывод + советы
    culprit = (
        "туман" if cloud_w == "туман"
        else "магнитные бури" if kp_val and kp_val >= 5
        else "пыльца"
    )
    summary, tips = gpt_blurb(culprit)
    P.append(f"📜 <b>Вывод</b>\n{summary}")
    P.append("✅ <b>Рекомендации</b>")
    for t in tips:
        P.append(f"• {t}")
    P.append("———")
    P.append(f"📚 {get_fact(TOMORROW)}")

    return "\n".join(P)

# ──────────────────── отправка в Telegram ─────────────────────────
async def send_main_post(bot: Bot) -> None:
    html = build_msg()
    logging.info("Preview: %s", html.replace("\n", " | ")[:220])
    await bot.send_message(
        CHAT_ID, html, parse_mode="HTML",
        disable_web_page_preview=True
    )

async def send_poll(bot: Bot) -> None:
    if pendulum.now(TZ).weekday() == 4:          # пятница
        await bot.send_poll(
            CHAT_ID, question=POLL_Q, options=POLL_OPT,
            is_anonymous=False, allows_multiple_answers=False
        )

# ──────────────────── точка входа ────────────────────────────────
async def main() -> None:
    bot = Bot(token=TOKEN)
    await send_main_post(bot)
    await send_poll(bot)
    logging.info("Done ✓")

if __name__ == "__main__":
    asyncio.run(main())
