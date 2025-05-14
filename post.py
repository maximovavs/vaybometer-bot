#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import asyncio
import logging

import pendulum
from telegram import Bot, error as tg_err

from utils import (
    _get, compass, clouds_word, wind_phrase, safe, get_fact,
    WEATHER_ICONS, AIR_EMOJI
)
from weather import get_weather
from air import get_air, get_pollen, get_sst, get_kp
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
UNSPLASH_KEY = os.getenv("UNSPLASH_KEY")

POLL_QUESTION = "Как сегодня ваше самочувствие? 🤔"
POLL_OPTIONS  = ["🔥 Полон(а) энергии", "🙂 Нормально", "😴 Слегка вялый(ая)", "🤒 Всё плохо"]

CITIES = {
    "Limassol": (34.707, 33.022),
    "Larnaca" : (34.916, 33.624),
    "Nicosia" : (35.170, 33.360),
    "Pafos"   : (34.776, 32.424),
}


def extract_tomorrow_temps(w: dict) -> tuple[float,float]:
    """
    Возвращает (day_max, night_min) для завтрашнего дня.
    Поддерживает два формата daily:
     - OpenWeather: list of { "temp": {"max":..,"min":..} }
     - Open-Meteo:  dict of arrays {"temperature_2m_max":[..,..], "temperature_2m_min":[..,..]}
    Если нет второго дня — берет первый. Если нет daily вообще — возвращает current.temp как оба.
    """
    if "daily" in w:
        d = w["daily"]
        # OpenWeather
        if isinstance(d, list) and d:
            rec = d[1] if len(d) > 1 else d[0]
            # у OWM: rec["temp"]["max"], rec["temp"]["min"]
            mx = rec["temp"]["max"]
            mn = rec["temp"]["min"]
            return mx, mn
        # Open-Meteo
        if isinstance(d, dict):
            arr_max = d.get("temperature_2m_max", [])
            arr_min = d.get("temperature_2m_min", [])
            if arr_max and arr_min:
                mx = arr_max[1] if len(arr_max) > 1 else arr_max[0]
                mn = arr_min[1] if len(arr_min) > 1 else arr_min[0]
                return mx, mn

    # fallback → current
    cur = w.get("current") or w.get("current_weather", {})
    # OpenWeather: cur["temp"] не всегда есть, но обычно есть cur["temperature"] у Meteo
    t = cur.get("temp") or cur.get("temperature") or 0.0
    return t, t


# ─────────── build_msg ────────────────────────────────────────────
def build_msg() -> str:
    P: list[str] = []

    # 1) Погода в Лимассоле
    w = get_weather(*CITIES["Limassol"])
    if not w:
        raise RuntimeError("Источники погоды недоступны")

    # универсально: берем tomorrow temps
    day_max, night_min = extract_tomorrow_temps(w)

    # остальные параметры
    if "current" in w:
        cur      = w["current"]
        wind_kmh = cur["wind_speed"] * 3.6
        wind_deg = cur["wind_deg"]
        press    = cur["pressure"]
        clouds   = cur.get("clouds", 0)
    else:
        cw       = w["current_weather"]
        wind_kmh = cw["windspeed"]
        wind_deg = cw["winddirection"]
        press    = w["pressure"] if "pressure" in cw else w["hourly"]["surface_pressure"][0]
        clouds   = w["hourly"]["cloud_cover"][0]

    strong = w.get("strong_wind", False)
    fog    = w.get("fog_alert", False)
    cloud_w = clouds_word(clouds)

    # заголовок
    icon = WEATHER_ICONS.get(cloud_w, "🌦️")
    P.append(f"{icon} <b>Погода на завтра в Лимассоле {TOMORROW.format('DD.MM.YYYY')}</b>")
    P.append(f"<b>Темп. днём/ночью:</b> {day_max:.1f}/{night_min:.1f} °C")
    P.append(f"<b>Облачность:</b> {cloud_w}")
    P.append(f"<b>Ветер:</b> {wind_phrase(wind_kmh)} ({wind_kmh:.1f} км/ч, {compass(wind_deg)})")
    if strong: P.append("⚠️ Ветер может усилиться")
    if fog:    P.append("🌁 Возможен туман, водите аккуратно")
    P.append(f"<b>Давление:</b> {press:.0f} гПа")
    P.append("———")

    # 2) Рейтинг городов (дн./ночь)
    temps: dict[str, tuple[float,float]] = {}
    for city, coords in CITIES.items():
        w2 = get_weather(*coords)
        if not w2: continue
        temps[city] = extract_tomorrow_temps(w2)

    P.append("🎖️ <b>Рейтинг городов (дн./ночь)</b>")
    medals = ["🥇","🥈","🥉","4️⃣"]
    for i,(city,(d_v,n_v)) in enumerate(
        sorted(temps.items(), key=lambda kv: kv[1][0], reverse=True)[:4]
    ):
        P.append(f"{medals[i]} {city}: {d_v:.1f}/{n_v:.1f} °C")
    P.append("———")

    # 3) Качество воздуха + пыльца
    air    = get_air() or {}
    pollen = get_pollen() or {}

    P.append("🏙️ <b>Качество воздуха</b>")
    if air:
        P.append(f"{air['lvl']} (AQI {air['aqi']}) | PM2.5: {safe(air['pm25'],' µg/м³')} | PM10: {safe(air['pm10'],' µg/м³')}")
    else:
        P.append("нет данных")

    P.append("🌿 <b>Пыльца</b>")
    if pollen:
        idx = lambda v: ["нет","низкий","умеренный","высокий","оч. высокий","экстрим"][int(round(v))]
        P.append(
            f"Деревья — {idx(pollen['treeIndex'])} | "
            f"Травы — {idx(pollen['grassIndex'])} | "
            f"Сорняки — {idx(pollen['weedIndex'])}"
        )
    else:
        P.append("нет данных")
    P.append("———")

    # 4) geomag, schumann, вода, astro
    kp, kp_state = get_kp()
    sch          = get_schumann()
    sst          = get_sst()
    astro        = astro_events()

    P.append(f"🧲 <b>Геомагнитка</b> K-index: {kp:.1f} ({kp_state})" if kp is not None else "🧲 <b>Геомагнитка</b> нет данных")
    if sch.get("high"):
        P.append("🎵 <b>Шуман:</b> ⚡️ вибрации повышены")
    elif "freq" in sch:
        P.append(f"🎵 <b>Шуман:</b> ≈{sch['freq']:.1f} Гц")
    else:
        P.append(f"🎵 <b>Шуман:</b> {sch.get('msg','нет данных')}")

    if sst is not None:
        P.append(f"🌊 <b>Темп. воды:</b> {sst:.1f} °C")

    if astro:
        # показываем только два самых значимых
        P.append("🌌 <b>Астрособытия</b>\n" + " | ".join(astro[:2]))
    P.append("———")

    # 5) Вывод и советы
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
    P.append(f"📜 <b>Вывод</b>\n{summary}")
    P.append("———")
    P.append("✅ <b>Рекомендации</b>")
    for t in tips:
        P.append(f"• {t}")
    P.append("———")
    P.append(f"📚 {get_fact(TOMORROW)}")

    return "\n".join(P)


# ─────────── SEND ────────────────────────────────────────────────
async def send_main_post(bot: Bot) -> None:
    html = build_msg()
    logging.info("Preview: %s", html.replace("\n"," | ")[:200])
    try:
        await bot.send_message(CHAT_ID, html, parse_mode="HTML", disable_web_page_preview=True)
        logging.info("Message sent ✓")
    except tg_err.TelegramError as e:
        logging.error("Telegram error: %s", e)
        raise

async def send_poll_if_friday(bot: Bot) -> None:
    if pendulum.now(TZ).weekday() == 4:
        try:
            await bot.send_poll(CHAT_ID, question=POLL_QUESTION, options=POLL_OPTIONS,
                                is_anonymous=False, allows_multiple_answers=False)
        except tg_err.TelegramError as e:
            logging.warning("Poll error: %s", e)

async def fetch_unsplash_photo() -> str | None:
    if not UNSPLASH_KEY:
        return None
    j = _get("https://api.unsplash.com/photos/random",
             query="cyprus coast sunset", client_id=UNSPLASH_KEY)
    return j.get("urls",{}).get("regular")

async def send_photo(bot: Bot, url: str) -> None:
    try:
        await bot.send_photo(CHAT_ID, photo=url, caption="Фото дня • Unsplash")
    except tg_err.TelegramError as e:
        logging.warning("Photo error: %s", e)

async def main() -> None:
    bot = Bot(token=TOKEN)
    await send_main_post(bot)
    await send_poll_if_friday(bot)
    if UNSPLASH_KEY and pendulum.now(TZ).day % 3 == 0:
        if photo := await fetch_unsplash_photo():
            await send_photo(bot, photo)
    logging.info("Done ✓")

if __name__ == "__main__":
    asyncio.run(main())
