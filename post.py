#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
post.py  – ежедневная карточка Telegram-канала «VayboMeter»

Основные отличия:
• «Погода на завтра **на Кипре** …», а не только Лимассол.
• Под облачностью выводится текст WMO-описания.
• Давление показывает стрелку тренда (utils.pressure_trend).
• Цветная шкала K-index (utils.kp_emoji).
• PM-показатели окрашиваются (utils.pm_color), без «—».
• Пыльца берётся из нового pollen.py.
• Шуман: частота + стрелка get_schumann_trend().
• Вода: пометка «🥶 прохладно» / «🌡 комфортно».
"""

from __future__ import annotations

# ────────── std / pypi ──────────────────────────────────────────
import os, asyncio, logging, statistics, requests
from typing import Dict, Tuple, List, Optional

import pendulum
from telegram import Bot, error as tg_err

# ────────── наши утилиты и модули ───────────────────────────────
from utils import (
    compass,          # англ. румбы (оставим для fallback)
    clouds_word, wind_phrase, safe, get_fact,
    WEATHER_ICONS, AIR_EMOJI,
    pm_color, kp_emoji, pressure_trend          # новые помощники
)
from weather   import get_weather, fetch_tomorrow_temps
from air       import get_air, get_sst, get_kp
from pollen    import get_pollen
from schumann  import get_schumann, get_schumann_trend
from astro     import astro_events
from gpt       import gpt_blurb

# ────────── конфигурация / окружение ────────────────────────────
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

TZ            = pendulum.timezone("Asia/Nicosia")
TODAY         = pendulum.now(TZ).date()
TOMORROW      = TODAY.add(days=1)

TOKEN         = os.environ["TELEGRAM_TOKEN"]
CHAT_ID       = int(os.environ["CHANNEL_ID"])
UNSPLASH_KEY  = os.getenv("UNSPLASH_KEY")

POLL_QUESTION = "Как сегодня ваше самочувствие? 🤔"
POLL_OPTIONS  = ["🔥 Полон(а) энергии", "🙂 Нормально",
                 "😴 Слегка вялый(ая)", "🤒 Всё плохо"]

# основные города
CITIES: Dict[str, Tuple[float, float]] = {
    "Limassol": (34.707, 33.022),
    "Larnaca" : (34.916, 33.624),
    "Nicosia" : (35.170, 33.360),
    "Pafos"   : (34.776, 32.424),
}

# краткий словарь WMO-код → описание (допол­няйте по необходимости)
WMO_DESC = {
    0:  "Ясно", 1: "Преим. ясно", 2: "Перем. облачность", 3: "Пасмурно",
    45: "Туман", 48: "Изморозь", 51: "Мелкий морось", 53: "Морось",
    55: "Сильная морось", 61: "Небольшой дождь", 63: "Дождь",
    65: "Ливень", 71: "Снег", 95: "Гроза"
}

# русифицируем 16 румб компаса (для красоты)
COMPASS_RU = [
    "C", "СCВ", "СВ", "ВCВ", "В", "ВЮВ", "ЮВ", "ЮЮВ",
    "Ю", "ЮЮЗ", "ЮЗ", "ЗЮЗ", "З", "ЗCЗ", "СЗ", "СCЗ"
]
def compass_ru(deg: float) -> str:
    return COMPASS_RU[int((deg / 22.5) + .5) % 16]

# ────────────────────────────────────────────────────────────────
def build_msg() -> str:
    parts: List[str] = []

    # 1. максимальная/минимальная температура завтра (Лимассол)
    day_max, night_min = fetch_tomorrow_temps(*CITIES["Limassol"])
    if day_max is None or night_min is None:
        raise RuntimeError("Не удалось получить max/min на завтра")

    # текущие данные (для ветра, давления…)
    w_lim = get_weather(*CITIES["Limassol"])
    if not w_lim:
        raise RuntimeError("Источники погоды недоступны")

    cur          = w_lim.get("current") or w_lim["current_weather"]
    wind_kmh     = cur.get("windspeed")     or cur.get("wind_speed") or 0.0
    wind_deg     = cur.get("winddirection") or cur.get("wind_deg")   or 0.0
    press        = cur.get("pressure")      or w_lim["hourly"]["surface_pressure"][0]
    clouds_pct   = cur.get("clouds")        or w_lim["hourly"]["cloud_cover"][0]
    cloud_w      = clouds_word(clouds_pct)
    press_arrow  = pressure_trend(w_lim)

    # WMO-описание
    wcode        = cur.get("weathercode", 0)
    w_desc       = WMO_DESC.get(wcode, "")

    # средние температуры по Кипру
    all_day, all_night = [], []
    for la, lo in CITIES.values():
        d, n = fetch_tomorrow_temps(la, lo)
        if d is not None:
            all_day.append(d)
            all_night.append(n if n is not None else d)
    avg_day   = statistics.mean(all_day)   if all_day   else day_max
    avg_night = statistics.mean(all_night) if all_night else night_min

    icon = WEATHER_ICONS.get(cloud_w, "🌦️")
    parts += [
        f"{icon} <b>Погода на завтра на Кипре {TOMORROW.format('DD.MM.YYYY')}</b>",
        f"🌡 Средняя темп.: {avg_day:.0f} °C",
        f"<b>Темп. днём/ночью:</b> {day_max:.1f}/{night_min:.1f} °C",
        #  f"<b>Облачность:</b> {cloud_w}",
        f"🌡️ {w_desc}",
        f"<b>Ветер:</b> {wind_phrase(wind_kmh)} "
        f"({wind_kmh:.1f} км/ч, {compass_ru(wind_deg)})",
        f"<b>Давление:</b> {press:.0f} гПа {press_arrow}",
    ]
    if w_lim.get("strong_wind"):
        parts.append("⚠️ Возможен порывистый ветер")
    if w_lim.get("fog_alert"):
        parts.append("🌁 Утром возможен туман – внимание на дорогах")
    parts.append("———")

    # 2. рейтинг городов
    temps: Dict[str, Tuple[float, float]] = {}
    for city, (la, lo) in CITIES.items():
        d, n = fetch_tomorrow_temps(la, lo)
        if d is not None:
            temps[city] = (d, n if n is not None else d)

    parts.append("🎖️ <b>Рейтинг городов (дн./ночь)</b>")
    medals = ["🥇", "🥈", "🥉", "4️⃣"]
    for i, (city, (d, n)) in enumerate(sorted(temps.items(),
                                              key=lambda kv: kv[1][0],
                                              reverse=True)[:4]):
        parts.append(f"{medals[i]} {city}: {d:.1f}/{n:.1f} °C")
    parts.append("———")

    # 3. качество воздуха
    air = get_air()
    parts.append("🏙️ <b>Качество воздуха</b>")
    parts.append(
        f"{AIR_EMOJI[air['lvl']]} {air['lvl']} "
        f"(AQI {air['aqi']}) | "
        f"PM₂.₅: {pm_color(air['pm25'])} | "
        f"PM₁₀: {pm_color(air['pm10'])}"
    )

    # 4. пыльца
    pol = get_pollen()
    if pol:
        parts.append(
            f"🌿 Пыльца • деревья {pol['tree']} | "
            f"травы {pol['grass']} | сорняки {pol['weed']} — риск {pol['risk']}"
        )
    parts.append("———")

    # 5. геомагнитка / Шуман / вода / астрособытия
    kp_val, kp_state = get_kp()
    if kp_val is not None:
        parts.append(f"{kp_emoji(kp_val)} Геомагнитка Kp {kp_val:.1f} ({kp_state})")
    else:
        parts.append("🧲 Геомагнитка – нет данных")

    sch = get_schumann()
    if "freq" in sch:
        trend = get_schumann_trend()
        arrow = "↑" if trend == "up" else "↓" if trend == "down" else "→"
        parts.append(f"🎵 Шуман: {sch['freq']:.2f} Гц {arrow}")
    else:
        parts.append(f"🎵 Шуман: {sch['msg']}")

    sst = get_sst()
    if sst is not None:
        label = "🌡 комфортно" if sst >= 18 else "🥶 прохладно"
        parts.append(f"🌊 Вода: {sst:.1f} °C {label} (Open-Meteo)")
    astro = astro_events()
    if astro:
        parts.append("🌌 Астрособытия – " + " | ".join(astro))
    parts.append("———")

    # 6. вывод и советы
    culprit = ("туман"            if w_lim.get("fog_alert")        else
               "магнитные бури"   if kp_val and kp_val >= 5        else
               "низкое давление"  if press < 1007                  else
               "шальной ветер"    if w_lim.get("strong_wind")      else
               "лунное влияние")
    summary, tips = gpt_blurb(culprit)

    parts.append(f"📜 <b>Вывод</b>\n{summary}")
    parts.append("———")
    parts.append("✅ <b>Рекомендации</b>")
    parts.extend(f"• {t}" for t in tips)
    parts.append("———")
    parts.append(f"📚 {get_fact(TOMORROW)}")

    return "\n".join(parts)

# ────────── Telegram helpers ────────────────────────────────────
async def send_main_post(bot: Bot) -> None:
    html = build_msg()
    logging.info("Preview: %s", html.replace("\n", " | ")[:250])
    await bot.send_message(
        CHAT_ID, html,
        parse_mode="HTML", disable_web_page_preview=True
    )

async def send_poll_if_friday(bot: Bot) -> None:
    if pendulum.now(TZ).weekday() == 4:  # Friday
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

# ────────── main entrypoint ─────────────────────────────────────
async def main() -> None:
    bot = Bot(token=TOKEN)

    await send_main_post(bot)
    await send_poll_if_friday(bot)

    if UNSPLASH_KEY and TODAY.day % 3 == 0:
        if (photo := await fetch_unsplash_photo()):
            await send_photo(bot, photo)

    logging.info("All tasks done ✓")

if __name__ == "__main__":
    asyncio.run(main())
