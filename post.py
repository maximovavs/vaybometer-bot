#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
post.py

Сборка и отправка прогноза на завтра для Кипра (Лимассол + сравнение городов).
"""

import os
import asyncio
import logging
import pendulum
from telegram import Bot, error as tg_err

from utils import (
    compass, clouds_word, wind_phrase, safe,
    get_fact, WEATHER_ICONS, AIR_EMOJI
)
from weather import get_weather
from air import get_air, get_pollen, get_sst, get_kp
from schumann import get_schumann
from astro import astro_events
from gpt import gpt_blurb

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

# ─────────── 0. CONST ────────────────────────────────────────────
TZ        = pendulum.timezone("Asia/Nicosia")
TODAY     = pendulum.now(TZ).date()
TOMORROW  = TODAY + pendulum.duration(days=1)
TOKEN     = os.environ["TELEGRAM_TOKEN"]
CHAT      = os.environ["CHANNEL_ID"]

# Города для рейтинга температур
CITIES = {
    "Limassol": (34.707, 33.022),
    "Larnaca" : (34.916, 33.624),
    "Nicosia" : (35.170, 33.360),
    "Pafos"   : (34.776, 32.424),
}


def build_msg() -> str:
    # 1️⃣ Получаем погоду
    w = get_weather(*CITIES["Limassol"])
    if not w:
        raise RuntimeError("Источники погоды недоступны")

    # Распознаём OpenWeather vs Open-Meteo
    if "current" in w:
        cur        = w["current"]
        day_block  = w["daily"][0]["temp"]
        wind_kmh   = cur["wind_speed"] * 3.6
        wind_deg   = cur["wind_deg"]
        press      = cur["pressure"]
        cloud_word = clouds_word(cur.get("clouds", 0))
        day_max    = day_block["max"]
        night_min  = day_block["min"]
    else:
        cw         = w["current_weather"]
        wind_kmh   = cw["windspeed"]
        wind_deg   = cw["winddirection"]
        press      = cw["pressure"]
        cloud_word = clouds_word(w["hourly"]["cloud_cover"][0])
        # завтра — первый элемент daily
        block      = w["daily"][0] if isinstance(w["daily"], list) else w["daily"]
        tm, tn     = block["temperature_2m_max"], block["temperature_2m_min"]
        day_max    = tm[0]
        night_min  = tn[0]

    strong = w.get("strong_wind", False)
    fog    = w.get("fog_alert",   False)

    # 2️⃣ Рейтинг городов по темп. (дн/ночь)
    temps: dict[str, tuple[float, float]] = {}
    for city, (la, lo) in CITIES.items():
        w2 = get_weather(la, lo)
        if not w2:
            continue
        if "current" in w2:
            db = w2["daily"][0]["temp"]
            tmax, tmin = db["max"], db["min"]
        else:
            blk = w2["daily"][0] if isinstance(w2["daily"], list) else w2["daily"]
            arr_max, arr_min = blk["temperature_2m_max"], blk["temperature_2m_min"]
            tmax, tmin = arr_max[0], arr_min[0]
        temps[city] = (tmax, tmin)

    warm = max(temps, key=lambda c: temps[c][0])
    cold = min(temps, key=lambda c: temps[c][1])

    # 3️⃣ Остальные блоки
    air    = get_air() or {}
    idx_p  = lambda v: ["нет","низкий","умеренный","высокий","оч. высокий","экстрим"][int(round(v))]
    pollen = get_pollen()
    kp, kp_state   = get_kp()
    sch            = get_schumann()
    sst            = get_sst()
    astro_list     = astro_events()

    # 4️⃣ «Виновник» и советы
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

    # 5️⃣ Сборка HTML
    icon = WEATHER_ICONS.get(cloud_word, "🌦️")
    lines = [
        f"{icon} <b>Погода на завтра в Лимассоле {TOMORROW.format('DD.MM.YYYY')}</b>",
        f"<b>Темп. (дн/ночь):</b> {day_max:.1f}/{night_min:.1f} °C",
        f"<b>Давление:</b> {press:.0f} гПа",
        f"<b>Рейтинг городов (дн/ночь):</b> " + " | ".join(
            f"{c}: {temps[c][0]:.1f}/{temps[c][1]:.1f} °C" for c in (warm, cold)
        ),
        "———",
        f"<b>Облачность:</b> {cloud_word}",
        f"<b>Ветер:</b> {wind_phrase(wind_kmh)} ({wind_kmh:.1f} км/ч, {compass(wind_deg)})",
        *(["⚠️ Ветер будет усиливаться"] if strong else []),
        *(["🌁 Возможен туман, водите аккуратно"] if fog else []),
        "———",
        "🏙️ <b>Качество воздуха</b>",
        f"{AIR_EMOJI.get(air.get('lvl'),'⚪')} AQI {air.get('aqi','—')} | PM2.5: {safe(air.get('pm25'),' µg/м³')} | PM10: {safe(air.get('pm10'),' µg/м³')}",
    ]

    if pollen:
        lines += [
            "🌿 <b>Пыльца</b>",
            f"Деревья — {idx_p(pollen['treeIndex'])} | Травы — {idx_p(pollen['grassIndex'])} | Сорняки — {idx_p(pollen['weedIndex'])}"
        ]

    lines += [
        "———",
        "🧲 <b>Геомагнитная активность</b>",
        f"K-index: {kp:.1f} ({kp_state})" if kp is not None else "нет данных",
    ]

    if sch.get("high"):
        lines += ["🎵 <b>Шуман:</b> ⚡️ вибрации повышены (>8 Гц)"]
    elif "freq" in sch:
        lines += [f"🎵 <b>Шуман:</b> ≈{sch['freq']:.1f} Гц, амплитуда стабильна"]
    else:
        lines += [f"🎵 <b>Шуман:</b> {sch.get('msg','нет данных')}"]

    if sst is not None:
        lines += [f"🌊 <b>Температура воды:</b> {sst:.1f} °C"]

    if astro_list:
        lines += ["🔮 <b>Астрособытия</b>", " | ".join(astro_list)]

    lines += [
        "———",
        f"📜 <b>Вывод</b>\n{summary}",
        "———",
        "✅ <b>Рекомендации</b>",
        *[f"• {t}" for t in tips],
        "———",
        f"📚 {get_fact(TOMORROW)}",
    ]

    return "\n".join(lines)


async def main() -> None:
    bot = Bot(TOKEN)
    msg = build_msg()
    logging.info("Preview: %s", msg.replace("\n", " | ")[:200])
    try:
        await bot.send_message(int(CHAT), msg, parse_mode="HTML", disable_web_page_preview=True)
        logging.info("Message sent ✓")
    except tg_err.TelegramError as e:
        logging.error("Telegram error: %s", e)
        raise


if __name__ == "__main__":
    asyncio.run(main())
