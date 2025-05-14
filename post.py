#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import asyncio
import logging

import pendulum
from telegram import Bot, error as tg_err

from utils import (
    _get,
    compass, clouds_word, wind_phrase, safe, get_fact,
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


def build_msg() -> str:
    P: list[str] = []

    # 1) Погода в Лимассоле
    lat, lon = CITIES["Limassol"]
    w = get_weather(lat, lon)
    if not w:
        raise RuntimeError("Источники погоды недоступны")

    # общая обработка current + daily → day_max, night_min
    if "current" in w:
        cur      = w["current"]
        wind_kmh = cur["wind_speed"] * 3.6
        wind_deg = cur["wind_deg"]
        press    = cur["pressure"]
        cloud_w  = clouds_word(cur.get("clouds", 0))
        strong   = w.get("strong_wind", False)
        fog      = w.get("fog_alert", False)
        # завтра: daily[1] если есть
        daily    = w["daily"]
        blk      = daily[1]["temp"] if len(daily) > 1 else daily[0]["temp"]
        day_max  = blk["max"]
        night_min= blk["min"]
    else:
        cw       = w["current_weather"]
        wind_kmh = cw["windspeed"]
        wind_deg = cw["winddirection"]
        press    = w["hourly"]["surface_pressure"][0]
        cloud_w  = clouds_word(w["hourly"]["cloud_cover"][0])
        strong   = w.get("strong_wind", False)
        fog      = w.get("fog_alert", False)
        daily    = w["daily"]
        blk      = daily[1] if (isinstance(daily, list) and len(daily) > 1) else (daily[0] if isinstance(daily, list) else daily)
        tm       = blk["temperature_2m_max"]
        tn       = blk["temperature_2m_min"]
        day_max  = tm[1] if len(tm) > 1 else tm[0]
        night_min= tn[1] if len(tn) > 1 else tn[0]

    # заголовок
    icon = WEATHER_ICONS.get(cloud_w, "🌦️")
    P.append(f"{icon} <b>Погода на завтра в Лимассоле {TOMORROW.format('DD.MM.YYYY')}</b>")
    P.append(f"<b>Темп. днём/ночью:</b> {day_max:.1f}/{night_min:.1f} °C")
    P.append(f"<b>Облачность:</b> {cloud_w}")
    P.append(f"<b>Ветер:</b> {wind_phrase(wind_kmh)} ({wind_kmh:.1f} км/ч, {compass(wind_deg)})")
    if strong:  P.append("⚠️ Ветер может усилиться")
    if fog:     P.append("🌁 Возможен туман, водите аккуратно")
    P.append(f"<b>Давление:</b> {press:.0f} гПа")
    P.append("———")

    # 2) Рейтинг городов (дн./ночь)
    temps: dict[str, tuple[float, float]] = {}
    for city, (la, lo) in CITIES.items():
        w2 = get_weather(la, lo)
        if not w2:
            continue
        if "current" in w2:
            tblk = w2["daily"][0]["temp"]
            d_val, n_val = tblk["max"], tblk["min"]
        else:
            dblk = w2["daily"][0] if isinstance(w2["daily"], list) else w2["daily"]
            ma = dblk["temperature_2m_max"]
            na = dblk["temperature_2m_min"]
            d_val = ma[1] if len(ma) > 1 else ma[0]
            n_val = na[1] if len(na) > 1 else na[0]
        temps[city] = (d_val, n_val)

    P.append("🎖️ <b>Рейтинг городов (дн./ночь)</b>")
    sorted_c = sorted(temps.items(), key=lambda kv: kv[1][0], reverse=True)
    medals   = ["🥇","🥈","🥉"]
    for idx, (city, (d_v, n_v)) in enumerate(sorted_c[:3]):
        P.append(f"{medals[idx]} {city}: {d_v:.1f}/{n_v:.1f} °C")
    # четвёртое место без медали
    if len(sorted_c) >= 4:
        city4, (d4, n4) = sorted_c[3]
        P.append(f"4️⃣ {city4}: {d4:.1f}/{n4:.1f} °C")
    P.append("———")

    # 3) Качество воздуха + пыльца
    air = get_air() or {}
    P.append("🏙️ <b>Качество воздуха</b>")
    if air:
        lvl = air["lvl"]
        em  = AIR_EMOJI.get(lvl, "⚪")
        P.append(f"{em} {lvl.capitalize()} (AQI {air['aqi']}) | PM2.5: {safe(air['pm25'],' µg/м³')} | PM10: {safe(air['pm10'],' µg/м³')}")
    else:
        P.append("нет данных")

    pollen = get_pollen()
    if pollen:
        idxf = lambda v: ["нет","низкий","умеренный","высокий","оч. высокий","экстрим"][int(round(v))]
        P.append("🌿 <b>Пыльца</b>")
        P.append(f"Деревья — {idxf(pollen['treeIndex'])} | Травы — {idxf(pollen['grassIndex'])} | Сорняки — {idxf(pollen['weedIndex'])}")
    P.append("———")

    # 4) Геомагнитка, Шуман, вода, астрособытия
    kp, kp_state = get_kp()
    sch          = get_schumann()
    sst          = get_sst()
    raw_ast      = astro_events()
    # упрощаем: фаза Луна + главное явление
    moon_phase   = raw_ast[0]
    extra_ev     = next((s for s in raw_ast[1:] if "затм" in s.lower() or "метеор" in s.lower()), None)
    astros       = [moon_phase] + ([extra_ev] if extra_ev else [])

    P.append(f"🧲 <b>Геомагнитка</b> K-index: {kp:.1f} ({kp_state})" if kp is not None else "🧲 <b>Геомагнитка</b> нет данных")
    if sch.get("high"):
        P.append("🎵 <b>Шуман:</b> ⚡️ вибрации повышены")
    elif "freq" in sch:
        P.append(f"🎵 <b>Шуман:</b> ≈{sch['freq']:.1f} Гц")
    else:
        P.append(f"🎵 <b>Шуман:</b> {sch.get('msg','нет данных')}")
    if sst is not None:
        P.append(f"🌊 <b>Температура воды</b>: {sst:.1f} °C")
    P.append("🌌 <b>Астрособытия</b>")
    P.append(" | ".join(astros))
    P.append("💡 <i>Влияние:</i> эмоции ⚡️ отношения 🤝 интуиция 🧠")
    P.append("———")

    # 5) Виновник дня + советы
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
    msg = build_msg()
    logging.info("Preview: %s", msg.replace("\n"," | ")[:200])
    try:
        await bot.send_message(
            CHAT_ID, msg, parse_mode="HTML", disable_web_page_preview=True
        )
        logging.info("Message sent ✓")
    except tg_err.TelegramError as e:
        logging.error("Telegram error: %s", e)
        raise

async def send_poll_if_friday(bot: Bot) -> None:
    # pendulum.weekday(): Monday=0 … Friday=4
    if pendulum.now(TZ).weekday() == 4:
        try:
            await bot.send_poll(
                CHAT_ID,
                question=POLL_QUESTION,
                options=POLL_OPTIONS,
                is_anonymous=False,
                allows_multiple_answers=False,
            )
        except tg_err.TelegramError as e:
            logging.warning("Poll send error: %s", e)

async def fetch_unsplash_photo() -> str | None:
    if not UNSPLASH_KEY:
        return None
    j = _get(
        "https://api.unsplash.com/photos/random",
        query="cyprus coast sunset",
        client_id=UNSPLASH_KEY,
    )
    return j.get("urls", {}).get("regular")

async def send_photo(bot: Bot, url: str) -> None:
    try:
        await bot.send_photo(CHAT_ID, photo=url, caption="Фото дня • Unsplash")
    except tg_err.TelegramError as e:
        logging.warning("Photo send error: %s", e)

async def main() -> None:
    bot = Bot(token=TOKEN)
    await send_main_post(bot)
    await send_poll_if_friday(bot)
    # каждые 3 дня (по дате UTC)
    if UNSPLASH_KEY and (pendulum.now("UTC").day % 3 == 0):
        if photo := await fetch_unsplash_photo():
            await send_photo(bot, photo)
    logging.info("All tasks done ✓")

if __name__ == "__main__":
    asyncio.run(main())
