#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import asyncio
import logging
import random
import pendulum

from telegram import Bot, error as tg_err

# утилиты
from utils import compass, clouds_word, wind_phrase, safe, get_fact
# источники
from weather import get_weather
from air import get_air, get_pollen, get_sst, get_kp
from schumann import get_schumann
from astro import astro_events
from gpt import gpt_blurb

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

# ─────────── 0.  CONST ────────────────────────────────────────────
TZ       = pendulum.timezone("Asia/Nicosia")
TODAY    = pendulum.now(TZ).date()
TOMORROW = TODAY.add(days=1)
TOKEN    = os.environ["TELEGRAM_TOKEN"]
CHAT     = os.environ["CHANNEL_ID"]

CITIES = {
    "Limassol": (34.707, 33.022),
    "Larnaca" : (34.916, 33.624),
    "Nicosia" : (35.170, 33.360),
    "Pafos"   : (34.776, 32.424),
}

WEATHER_ICONS = {
    "ясно":       "☀️",
    "переменная": "🌤️",
    "пасмурно":   "☁️",
    "дождь":      "🌧️",
    "туман":      "🌁",
}

AIR_EMOJI = {
    "хороший":                     "🟢",
    "умеренный":                   "🟡",
    "вредный для чувствительных":  "🟠",
    "вредный":                     "🔴",
    "оч. вредный":                 "🟣",
    "опасный":                     "⚫",
    "н/д":                         "⚪",
}

# ─────────── 6.  BUILD MESSAGE ────────────────────────────────────
def build_msg() -> str:
    # 1️⃣ Погода
    lat, lon = CITIES["Limassol"]
    w = get_weather(lat, lon)
    if not w:
        raise RuntimeError("Источники погоды недоступны")

    if "current" in w:
        # OpenWeather
        cur       = w["current"]
        day_block = w["daily"][0]["temp"]
        wind_kmh  = cur["wind_speed"] * 3.6
        wind_deg  = cur["wind_deg"]
        wcode     = cur.get("weather",[{"id":0}])[0]["id"]
        press     = cur.get("pressure")
        cloud_w   = clouds_word(cur.get("clouds",0))
        day_max   = day_block["max"]
        night_min = day_block["min"]
        strong    = w.get("strong_wind", False)
        fog       = w.get("fog_alert", False)
    else:
        # Open-Meteo
        cw        = w["current_weather"]
        wind_kmh  = cw["windspeed"]
        wind_deg  = cw["winddirection"]
        press     = cw.get("pressure")
        cloud_w   = clouds_word(w["hourly"]["cloud_cover"][0])
        strong    = w.get("strong_wind", False)
        fog       = w.get("fog_alert", False)

        # завтрашняя температура/код в едином формате
        daily = w["daily"]
        blk   = daily[0] if isinstance(daily,list) else daily
        tm, tn, codes = blk["temperature_2m_max"], blk["temperature_2m_min"], blk["weathercode"]
        day_max   = tm[1] if len(tm)>1 else tm[0]
        night_min = tn[1] if len(tn)>1 else tn[0]
        wcode     = codes[1] if len(codes)>1 else codes[0]

    # 2️⃣ Температурные лидеры
    temps = {}
    for city,(la,lo) in CITIES.items():
        w2 = get_weather(la, lo)
        if not w2: continue
        if "current" in w2:
            temps[city] = w2["daily"][0]["temp"]["max"]
        else:
            db = w2["daily"]
            blk = db[0] if isinstance(db,list) else db
            arr = blk["temperature_2m_max"]
            temps[city] = arr[1] if len(arr)>1 else arr[0]
    warm = max(temps, key=temps.get)
    cold = min(temps, key=temps.get)

    # 3️⃣ Прочие блоки
    air    = get_air() or {}
    kp_val, kp_state = get_kp()
    sst    = get_sst()
    pollen = get_pollen()
    sch    = get_schumann()
    astro  = astro_events()

    # 4️⃣ Виновник
    if fog:
        culprit = "туман"
    elif kp_state == "буря":
        culprit = "магнитные бури"
    elif press is not None and press < 1007:
        culprit = "низкое давление"
    elif strong:
        culprit = "шальной ветер"
    else:
        culprit = "мини-парад планет"
    summary, tips = gpt_blurb(culprit)

    icon = WEATHER_ICONS.get(cloud_w, "🌦️")

    # 5️⃣ Сборка HTML
    P = [
        f"{icon} <b>Погода на завтра в Лимассоле {TOMORROW.format('DD.MM.YYYY')}</b>",
        f"<b>Темп. днём:</b> до {day_max:.1f} °C",
        f"<b>Темп. ночью:</b> около {night_min:.1f} °C",
        f"<b>Облачность:</b> {cloud_w}",
        f"<b>Ветер:</b> {wind_phrase(wind_kmh)} ({wind_kmh:.1f} км/ч, {compass(wind_deg)})",
        *(["⚠️ Ветер может усилиться"] if strong else []),
        *(["🌁 Возможен туман, водите аккуратно"] if fog else []),
        f"<b>Давление:</b> {press:.0f} гПа" if press is not None else "<b>Давление:</b> —",
        f"<i>Самый тёплый город:</i> {warm} ({temps[warm]:.1f} °C)",
        f"<i>Самый прохладный город:</i> {cold} ({temps[cold]:.1f} °C)",
        "———",
        "🏙️ <b>Качество воздуха</b>",
        f"{AIR_EMOJI.get(air.get('lvl','н/д'))} "
        f"AQI {air.get('aqi','—')} | PM2.5: {safe(air.get('pm25'),' µg/м³')} | PM10: {safe(air.get('pm10'),' µg/м³')}",
    ]

    if pollen:
        idx = lambda v: ["нет","низкий","умеренный","высокий","оч. высокий","экстрим"][int(round(v))]
        P += [
            "🌿 <b>Пыльца</b>",
            f"Деревья — {idx(pollen['treeIndex'])} | "
            f"Травы — {idx(pollen['grassIndex'])} | "
            f"Сорняки — {idx(pollen['weedIndex'])}",
        ]

    P += [
        "🧲 <b>Геомагнитная активность</b>",
        f"K-index: {kp_val:.1f} ({kp_state})" if kp_val is not None else "нет данных",
    ]

    if sch.get("high"):
        P += ["🎵 <b>Шуман:</b> ⚡️ вибрации повышены (>8 Гц)"]
    elif "freq" in sch:
        P += [f"🎵 <b>Шуман:</b> ≈{sch['freq']:.1f} Гц, амплитуда стабильна"]
    else:
        P += [f"🎵 <b>Шуман:</b> {sch.get('msg','нет данных')}"]

    if sst is not None:
        P += [f"🌊 <b>Температура воды</b>\nСейчас: {sst:.1f} °C"]

    if astro:
        P += ["🌌 <b>Астрологические события</b>\n" + " | ".join(astro)]

    P += [
        "———",
        f"📜 <b>Вывод</b>\n{summary}",
        "———",
        "✅ <b>Рекомендации</b>",
        *[f"• {t}" for t in tips],
        "———",
        f"📚 {get_fact(TOMORROW)}",
    ]

    return "\n".join(P)

# ─────────── 7.  SEND ─────────────────────────────────────────────
async def main() -> None:
    bot  = Bot(TOKEN)
    html = build_msg()
    logging.info("Preview: %s", html.replace("\n"," | ")[:200])
    try:
        await bot.send_message(int(CHAT), html,
                               parse_mode="HTML",
                               disable_web_page_preview=True)
        logging.info("Message sent ✓")
    except tg_err.TelegramError as e:
        logging.error("Telegram error: %s", e)
        raise

if __name__ == "__main__":
    asyncio.run(main())
