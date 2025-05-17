#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os, asyncio, logging
from typing import Any, Dict, Optional

import pendulum
from telegram import Bot, error as tg_err

from utils import (
    compass, clouds_word, wind_phrase, safe, get_fact,
    WEATHER_ICONS, AIR_EMOJI, WMO_DESCRIPTIONS
)
from weather import get_weather
from air import get_air, get_pollen, get_sst, get_kp      # ← добавили get_sst, get_kp
from schumann import get_schumann
from astro import astro_events
from gpt import gpt_blurb

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

# ─────────── Constants ────────────────────────────────────────────
TZ           = pendulum.timezone("Asia/Nicosia")
TODAY        = pendulum.now(TZ).date()
TOMORROW     = TODAY.add(days=1)
TOKEN        = os.environ["TELEGRAM_TOKEN"]
CHAT_ID      = int(os.environ["CHANNEL_ID"])

CITIES = {
    "Limassol": (34.707, 33.022),
    "Larnaca" : (34.916, 33.624),
    "Nicosia" : (35.170, 33.360),
    "Pafos"   : (34.776, 32.424),
}


# ────────── BUILD_MESSAGE ───────────────────────────────────────
def build_msg() -> str:
    P: list[str] = []

    # === 1) Погода на Кипре (среднее) ===
    temps: list[tuple[float, float]] = []
    for la, lo in CITIES.values():
        w = get_weather(la, lo)
        if not w:
            continue

        daily = w["daily"]
        if isinstance(daily, dict):               # open-meteo dict-arrays
            dmax = daily["temperature_2m_max"]
            dmin = daily["temperature_2m_min"]
            day  = dmax[1] if len(dmax) > 1 else dmax[0]
            night= dmin[1] if len(dmin) > 1 else dmin[0]
        else:                                     # list (open-meteo / openweather)
            blk  = daily[1] if len(daily) > 1 else daily[0]
            if "temp" in blk:                     # openweather
                day, night = blk["temp"]["max"], blk["temp"]["min"]
            else:                                 # open-meteo list-dict
                day   = blk["temperature_2m_max"][-1]
                night = blk["temperature_2m_min"][0]
        temps.append((day, night))

    avg_day   = sum(d for d, _ in temps) / len(temps)
    avg_night = sum(n for _, n in temps) / len(temps)

    # прогноз для Лимассола
    w0 = get_weather(*CITIES["Limassol"])
    if not w0:
        raise RuntimeError("Нет погоды для Лимассола")

    cur = w0.get("current") or w0["current_weather"]
    wind_kmh = cur.get("windspeed") or cur.get("wind_speed") or 0.0
    wind_deg = cur.get("winddirection") or cur.get("wind_deg") or 0.0

    # давление (из current или первого часа hourly)
    press = (
        cur.get("pressure") or
        w0.get("hourly", {}).get("surface_pressure", [1013])[0]
    )

    # облачность (из current или hourly)
    clouds_pct = cur.get("clouds")
    if clouds_pct is None:
        clouds_pct = w0.get("hourly", {}).get("cloud_cover", [0])[0]
    cloud_w = clouds_word(clouds_pct)

    icon = WEATHER_ICONS.get(cloud_w, "🌦️")
    P += [
        f"{icon} Добрый вечер! Погода на завтра на Кипре "
        f"({TOMORROW.format('DD.MM.YYYY')})",
        f"🌡️ Средняя темп.: {avg_day:.0f} °C",
        f"📈 Темп. днём/ночью: {avg_day:.1f} / {avg_night:.1f} °C",
        f"🌤 Облачность: {cloud_w}",
        f"💨 Ветер: {wind_phrase(wind_kmh)} "
        f"({wind_kmh:.1f} км/ч, {compass(wind_deg)})",
        f"🔽 Давление: {press:.0f} гПа",
        "———",
    ]

    # === 2) Рейтинг городов (дн./ночь) ===
    city_t: list[tuple[str, float, float]] = []
    for city, (la, lo) in CITIES.items():
        w = get_weather(la, lo)
        if not w:
            continue
        daily = w["daily"]
        if isinstance(daily, dict):
            dmax = daily["temperature_2m_max"]
            dmin = daily["temperature_2m_min"]
            day  = dmax[1] if len(dmax) > 1 else dmax[0]
            night= dmin[1] if len(dmin) > 1 else dmin[0]
        else:
            blk = daily[1] if len(daily) > 1 else daily[0]
            if "temp" in blk:
                day, night = blk["temp"]["max"], blk["temp"]["min"]
            else:
                day, night = blk["temperature_2m_max"][-1], blk["temperature_2m_min"][0]
        city_t.append((city, day, night))

    city_t.sort(key=lambda x: x[1], reverse=True)
    medals = ["🥇", "🥈", "🥉", "4️⃣"]
    P.append("🎖️ Рейтинг городов (дн./ночь)")
    for i, (c, d, n) in enumerate(city_t[:4]):
        P.append(f"{medals[i]} {c}: {d:.1f}/{n:.1f} °C")
    P.append("———")

    # === 3) Качество воздуха + пыльца ===
    air = get_air() or {}
    P.append("🏙️ Качество воздуха")
    if air:
        P.append(
            f"{AIR_EMOJI[air['lvl']]} {air['lvl']} (AQI {air['aqi']}) | "
            f"PM2.5: {safe(air['pm25'],' µg/м³')} | "
            f"PM10: {safe(air['pm10'],' µg/м³')}"
        )
    else:
        P.append("нет данных")

    pollen = get_pollen()
    if pollen:
        idx = lambda v: ["нет", "низкий", "умеренный", "высокий",
                         "оч. высокий", "экстрим"][int(round(v))]
        P += [
            "🌿 Пыльца",
            f"Деревья – {idx(pollen['treeIndex'])}, "
            f"Травы – {idx(pollen['grassIndex'])}, "
            f"Сорняки – {idx(pollen['weedIndex'])}"
        ]
    P.append("———")

    # === 4) Геомагнитка + Шуман + море + астрособытия ===
    kp, kp_state = get_kp()
    sch          = get_schumann()
    sst          = get_sst()
    astro        = astro_events()

    P.append(
        f"🧲 Геомагнитка: Kp {kp:.1f} ({kp_state})"
        if kp is not None else "🧲 Геомагнитка: нет данных"
    )

    if sch.get("high"):
        P.append("🎵 Шуман: ⚡️ вибрации повышены")
    elif "freq" in sch:
        P.append(f"🎵 Шуман: ≈{sch['freq']:.1f} Гц")
    else:
        P.append(f"🎵 Шуман: {sch.get('msg','нет данных')}")

    if sst is not None:
        P.append(f"🌊 Температура воды: {sst:.1f} °C")

    if astro:
        P.append("🌌 Астрособытия – " + " | ".join(astro))
    P.append("———")

    # === 5) Вывод + советы GPT ===
    culprit = "туман" if cloud_w == "туман" else (
        "магнитные бури" if kp_state == "буря" else
        "низкое давление" if press < 1007 else
        "шальной ветер" if wind_kmh > 30 else
        "мини-парад планет"
    )
    summary, tips = gpt_blurb(culprit)

    P.append(f"📜 Вывод\n{summary}")
    P.append("———")
    P.append("✅ Рекомендации")
    for t in tips:
        P.append(f"• {t}")
    P.append("———")
    P.append(f"📚 {get_fact(TOMORROW)}")

    return "\n".join(P)


# ─────────── SEND MESSAGE ────────────────────────────────────────
async def main() -> None:
    text = build_msg()
    logging.info("Preview: %s", text.replace('\n', ' | ')[:250])

    bot = Bot(TOKEN)
    try:
        await bot.send_message(
            CHAT_ID, text, parse_mode="HTML", disable_web_page_preview=True
        )
    except tg_err.TelegramError as e:
        logging.error("Telegram error: %s", e)

if __name__ == "__main__":
    asyncio.run(main())
