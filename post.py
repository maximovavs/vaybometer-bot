#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
post.py
~~~~~~~~
• Формирует текстовую карточку погоды / здоровья и публикует её в Telegram-канал.
• Поддерживает: Open-Meteo, OpenWeather fallback, AQI + PM-параметры из двух источников,
  Pollen (Open-Meteo), резонанс Шумана с трендом, K-index со «светофором», факт дня и т. д.
"""

from __future__ import annotations
import os, asyncio, logging, requests
from typing import Dict, Tuple, Optional, List

import pendulum
from telegram import Bot, error as tg_err

from utils   import (
    compass, clouds_word, wind_phrase, safe,
    WEATHER_ICONS, AIR_EMOJI,
    pressure_trend, kp_emoji, pm_color, get_fact
)
from weather   import get_weather
from air       import get_air
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

# мини-опрос
POLL_Q   = "Как сегодня ваше самочувствие?"
POLL_OPT = ["🔥 Полон(а) энергии", "🙂 Нормально", "😴 Немного вялый(ая)", "🤒 Плохо"]

# География
CITIES: Dict[str, Tuple[float, float]] = {
    "Limassol": (34.707, 33.022),
    "Larnaca" : (34.916, 33.624),
    "Nicosia" : (35.170, 33.360),
    "Pafos"   : (34.776, 32.424),
}

# ──────────────────────────────────────────────────────────────────
def fetch_tomorrow_temps(lat: float, lon: float) -> Tuple[Optional[float], Optional[float]]:
    """
    Быстрый запрос к Open-Meteo (pollen / air quality тоже так работают):
    возвращает (tmax, tmin) ровно на завтрашний день.
    """
    date = TOMORROW.to_date_string()
    j = requests.get(
        "https://api.open-meteo.com/v1/forecast",
        params={
            "latitude":  lat, "longitude": lon,
            "timezone":  "UTC",
            "start_date": date, "end_date": date,
            "daily": "temperature_2m_max,temperature_2m_min",
        },
        timeout=15
    ).json()
    try:
        tmax = j["daily"]["temperature_2m_max"][0]
        tmin = j["daily"]["temperature_2m_min"][0]
        return float(tmax), float(tmin)
    except Exception:
        return None, None

# ──────────────────────────────────────────────────────────────────
def build_msg() -> str:
    P: List[str] = []

    # 1️⃣ Средняя температура по Кипру
    all_t: List[Tuple[float,float]] = []
    for la,lo in CITIES.values():
        d,n = fetch_tomorrow_temps(la, lo)
        if d is not None and n is not None:
            all_t.append((d,n))
    avg_day   = sum(d for d,_ in all_t)/len(all_t)
    avg_night = sum(n for _,n in all_t)/len(all_t)

    # 2️⃣ Детальный прогноз для Лимассола
    la0,lo0 = CITIES["Limassol"]
    d0,n0   = fetch_tomorrow_temps(la0, lo0)
    if d0 is None or n0 is None:
        raise RuntimeError("Open-Meteo не вернул завтрашние t°")

    w0 = get_weather(la0, lo0)
    if not w0:
        raise RuntimeError("Не получили current_weather")

    cur  = w0["current"]
    wind = cur["windspeed"]
    wdeg = cur["winddirection"]
    press= cur["pressure"]
    clouds_pct = cur["clouds"]
    cloud_w = clouds_word(clouds_pct)

    icon = WEATHER_ICONS.get(cloud_w, "🌦️")
    P += [
        f"{icon} <b>Добрый вечер! Погода на завтра на Кипре ({TOMORROW.format('DD.MM.YYYY')})</b>",
        f"🌡 Средняя темп.: {avg_day:.0f} °C",
        f"📈 Темп. днём/ночью: {d0:.1f} / {n0:.1f} °C",
        f"🌤 Облачность: {cloud_w}",
        f"💨 Ветер: {wind_phrase(wind)} ({wind:.0f} км/ч, {compass(wdeg)})",
        f"🔽 Давление: {press:.0f} гПа {pressure_trend(w0)}",
        "———",
    ]

    # 3️⃣ Рейтинг городов
    cities_sorted = sorted(
        ((c,)+fetch_tomorrow_temps(*CITIES[c])) for c in CITIES
        if fetch_tomorrow_temps(*CITIES[c])[0] is not None
    )
    cities_sorted.sort(key=lambda x: x[1], reverse=True)
    medals = ["🥇","🥈","🥉","4️⃣"]
    P.append("🎖️ Рейтинг городов (дн./ночь)")
    for i,(c,d,n) in enumerate(cities_sorted[:4]):
        P.append(f"{medals[i]} {c}: {d:.1f}/{n:.1f} °C")
    P.append("———")

    # 4️⃣ Качество воздуха
    air = get_air()
    pm = lambda v: f"{v:.0f}" if v not in (None,"н/д") else "н/д"
    P.append("🏙️ Качество воздуха")
    P.append(
        f"{AIR_EMOJI.get(air['lvl'],'⚪')} {air['lvl']} "
        f"(AQI {air['aqi']}) | PM₂.₅: {pm_color(pm(air['pm25']))} | "
        f"PM₁₀: {pm_color(pm(air['pm10']))}"
    )

    # 5️⃣ Пыльца
    pol = get_pollen()
    if pol:
        risk = pol["risk"]
        P.append(f"🌿 Пыльца – риск: {risk}")
        P.append(f"Деревья: {pol['tree']} | Травы: {pol['grass']} | Сорняки: {pol['weed']}")
    P.append("———")

    # 6️⃣ Геомагнитка
    kp, _state = get_kp()
    if kp is not None:
        P.append(f"{kp_emoji(kp)} Геомагнитка Kp={kp:.1f}")
    else:
        P.append("🧲 Геомагнитка – нет данных")

    # 7️⃣ Шуман
    sch = get_schumann()
    if "freq" in sch:
        trend = get_schumann_trend()
        arrow = "↑" if trend>0 else "↓" if trend<0 else "→"
        status = "⚡️ повышен" if sch.get("high") else "фон в норме"
        P.append(f"🎵 Шуман: {sch['freq']:.2f} Гц {arrow} – {status}")
    else:
        P.append(f"🎵 Шуман: {sch['msg']}")

    # 8️⃣ Температура воды
    sst = get_sst()
    if sst is not None:
        P.append(f"🌊 Вода Средиземного моря: {sst:.1f} °C")
    P.append("———")

    # 9️⃣ Астрособытия
    astro = astro_events()
    if astro:
        P.append("🌌 Астрособытия – " + " | ".join(astro))
        P.append("———")

    # 🔟 Финальный вывод + GPT-советы
    culprit = "туман" if cloud_w=="туман" else "магнитные бури" if kp and kp>=5 else "пыльца"
    summary, tips = gpt_blurb(culprit)
    P.append(f"📜 <b>Вывод</b>\n{summary}")
    P.append("✅ <b>Рекомендации</b>")
    for t in tips:
        P.append(f"• {t}")
    P.append("———")
    P.append(f"📚 {get_fact(TOMORROW)}")

    return "\n".join(P)

# ──────────────────────────────────────────────────────────────────
async def send_main_post(bot: Bot):
    txt = build_msg()
    logging.info("Preview: %s", txt.replace("\n"," | ")[:200])
    await bot.send_message(CHAT_ID, txt, parse_mode="HTML",
                           disable_web_page_preview=True)

async def send_poll(bot: Bot):
    if pendulum.now(TZ).weekday()==4:   # пятница
        await bot.send_poll(
            CHAT_ID, question=POLL_Q, options=POLL_OPT,
            is_anonymous=False, allows_multiple_answers=False
        )

async def main():
    bot = Bot(TOKEN)
    await send_main_post(bot)
    await send_poll(bot)
    logging.info("Done ✓")

if __name__ == "__main__":
    asyncio.run(main())
