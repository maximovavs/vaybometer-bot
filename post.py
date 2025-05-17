#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
post.py  •  nightly summary for Cyprus

✓ средняя температура по 4-м городам
✓ рейтинг городов (день / ночь)
✓ AQI + PM, пыльца, геомагнитка, Шуман, температура моря
✓ WMO-описание погоды
"""

import os, asyncio, logging
from typing import Any, Dict, List, Tuple, Optional

import pendulum
from telegram import Bot, error as tg_err

from utils import (
    compass, clouds_word, wind_phrase, safe, get_fact,
    WEATHER_ICONS, AIR_EMOJI, WMO_DESCRIPTIONS
)
from weather   import get_weather           # ваш патч с _auto_ уже в weather.py
from air       import get_air, get_pollen, get_sst, get_kp
from schumann  import get_schumann
from astro     import astro_events
from gpt       import gpt_blurb

# ─────────── базовая настройка логов ─────────────────────────────
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

# ─────────── константы ───────────────────────────────────────────
TZ        = pendulum.timezone("Asia/Nicosia")
TODAY     = pendulum.now(TZ).date()
TOMORROW  = TODAY.add(days=1)

TOKEN   = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = int(os.getenv("CHANNEL_ID"))

CITIES: dict[str, Tuple[float, float]] = {
    "Limassol": (34.707, 33.022),
    "Larnaca" : (34.916, 33.624),
    "Nicosia" : (35.170, 33.360),
    "Pafos"   : (34.776, 32.424),
}

# ─────────── helpers ─────────────────────────────────────────────
def extract_day_night(w: Dict[str, Any]) -> Tuple[float, float]:
    """
    Возвращает (day_max, night_min) на завтра
    из структуры Open-Meteo или OpenWeather.
    """
    d = w["daily"]

    # — Open-Meteо dict-формат —
    if isinstance(d, dict):
        ma = d["temperature_2m_max"]
        mi = d["temperature_2m_min"]
        day   = ma[1] if len(ma) > 1 else ma[0]
        night = mi[1] if len(mi) > 1 else mi[0]
        return day, night

    # — list форматы (Open-Meteо list | OpenWeather list) —
    blk = d[1] if len(d) > 1 else d[0]
    if "temp" in blk:                                 # OpenWeather
        return blk["temp"]["max"], blk["temp"]["min"]

    # Open-Meteо list-of-dicts
    return blk["temperature_2m_max"][-1], blk["temperature_2m_min"][0]


# ─────────── основная сборка сообщения ───────────────────────────
def build_msg() -> str:
    P: List[str] = []

    # ===== 1. средние температуры по Кипру =======================
    temp_pairs: List[Tuple[float, float]] = []
    for la, lo in CITIES.values():
        if (w := get_weather(la, lo)):
            temp_pairs.append(extract_day_night(w))

    if not temp_pairs:
        raise RuntimeError("Ни один источник погоды не ответил")

    avg_day   = sum(d for d, _ in temp_pairs) / len(temp_pairs)
    avg_night = sum(n for _, n in temp_pairs) / len(temp_pairs)

    # ===== 2. данные для Лимассола ===============================
    w_lim = get_weather(*CITIES["Limassol"])
    if not w_lim:
        raise RuntimeError("Нет прогноза для Лимассола")

    # текущий блок (унифицируем ключи)
    cur = w_lim.get("current") or w_lim["current_weather"]

    wind_kmh = cur.get("windspeed")     or cur.get("wind_speed") or 0.0
    wind_deg = cur.get("winddirection") or cur.get("wind_deg")  or 0.0

    press = (
        cur.get("pressure") or
        w_lim.get("hourly", {}).get("surface_pressure", [1013])[0]
    )

    clouds_pc = cur.get("clouds") or w_lim.get("hourly", {}).get("cloud_cover", [0])[0]
    cloud_word = clouds_word(clouds_pc)

    # WMO weather-code → текст (опционально)
    wcode = cur.get("weathercode")
    wmo_text = f" ({WMO_DESCRIPTIONS.get(wcode, '')})" if wcode is not None else ""

    # ===== 3. заголовок =========================================
    icon = WEATHER_ICONS.get(cloud_word, "🌦️")
    P += [
        f"{icon} Добрый вечер! Погода на завтра на Кипре "
        f"({TOMORROW.format('DD.MM.YYYY')})",
        f"🌡️ Средняя темп.: {avg_day:.0f} °C",
        f"📈 Темп. днём/ночью: {avg_day:.1f}/{avg_night:.1f} °C",
        f"🌤 Облачность: {cloud_word}{wmo_text}",
        f"💨 Ветер: {wind_phrase(wind_kmh)} "
        f"({wind_kmh:.1f} км/ч, {compass(wind_deg)})",
        f"🔽 Давление: {press:.0f} гПа",
        "———",
    ]

    # ===== 4. рейтинг городов ===================================
    city_rows: List[Tuple[str, float, float]] = []
    for city, (la, lo) in CITIES.items():
        if (w := get_weather(la, lo)):
            d, n = extract_day_night(w)
            city_rows.append((city, d, n))

    city_rows.sort(key=lambda x: x[1], reverse=True)
    medals = ["🥇", "🥈", "🥉", "4️⃣"]

    P.append("🎖️ Рейтинг городов (дн./ночь)")
    for i, (c, d, n) in enumerate(city_rows[:4]):
        P.append(f"{medals[i]} {c}: {d:.1f}/{n:.1f} °C")
    P.append("———")

    # ===== 5. воздух + пыльца ====================================
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

    if (p := get_pollen()):
        idx = lambda v: ["нет","низкий","умеренный","высокий",
                         "оч. высокий","экстрим"][int(round(v))]
        P += [
            "🌿 Пыльца",
            f"Деревья – {idx(p['treeIndex'])}, "
            f"Травы – {idx(p['grassIndex'])}, "
            f"Сорняки – {idx(p['weedIndex'])}",
        ]
    P.append("———")

    # ===== 6. геомагнитка + Шуман + море + астрособытия ==========
    kp, kp_state = get_kp()
    sch          = get_schumann()
    sst          = get_sst()
    astro        = astro_events()

    P.append(f"🧲 Геомагнитка Kp={kp:.1f} ({kp_state})" if kp is not None
             else "🧲 Геомагнитка – нет данных")

    if sch.get("high"):
        P.append("🎵 Шуман: ⚡️ вибрации повышены")
    elif "freq" in sch:
        P.append(f"🎵 Шуман: ≈{sch['freq']:.1f} Гц")
    else:
        P.append(f"🎵 Шуман: {sch.get('msg','нет данных')}")

    if sst is not None:
        P.append(f"🌊 Температура воды: {sst:.1f} °C")

    if astro:
        P.append("🌌 Астрособытия: " + " | ".join(astro))

    P.append("———")

    # ===== 7. вывод + советы GPT =================================
    culprit = (
        "туман"            if w_lim.get("fog_alert")          else
        "магнитные бури"   if kp_state == "буря"              else
        "низкое давление"  if press < 1007                    else
        "шальной ветер"    if w_lim.get("strong_wind")        else
        "мини-парад планет"
    )

    summary, tips = gpt_blurb(culprit)
    P += [
        f"📜 Вывод\n{summary}",
        "———",
        "✅ Рекомендации",
        *[f"• {t}" for t in tips],
        "———",
        f"📚 {get_fact(TOMORROW)}",
    ]

    return "\n".join(P)


# ─────────── отправка в Telegram ─────────────────────────────────
async def main() -> None:
    bot = Bot(token=TOKEN)

    try:
        text = build_msg()
    except Exception as e:
        logging.error("Сборка сообщения: %s", e)
        return

    logging.info("Preview: %s", text.replace('\n',' | ')[:200])

    try:
        await bot.send_message(CHAT_ID, text,
                               parse_mode="HTML",
                               disable_web_page_preview=True)
        logging.info("Message sent ✓")
    except tg_err.TelegramError as e:
        logging.error("Telegram: %s", e)


if __name__ == "__main__":
    asyncio.run(main())
