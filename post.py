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

# ─────────── build_msg ────────────────────────────────────────────
def build_msg() -> str:
    P: list[str] = []

    # 1) Погода в Лимассоле
    w = get_weather(*CITIES["Limassol"])
    if not w:
        raise RuntimeError("Источники погоды недоступны")

    # — извлекаем прогноз именно на завтра (index=1 в daily и hourly)
    if "current" in w:
        cur       = w["current"]
        wind_kmh  = cur["wind_speed"] * 3.6
        wind_deg  = cur["wind_deg"]
        press     = cur["pressure"]
        cloud_w   = clouds_word(cur.get("clouds", 0))
        tomorrow  = w["daily"][1]      # 🔥 вот тут — именно завтра
        day_max   = tomorrow["temp"]["max"]
        night_min = tomorrow["temp"]["min"]
        strong    = w["strong_wind"]
        fog       = w["fog_alert"]
    else:
        cw        = w["current_weather"]
        wind_kmh  = cw["windspeed"]
        wind_deg  = cw["winddirection"]
        # давление на завтра, если hourly больше одного
        hp        = w["hourly"]["surface_pressure"]
        press     = hp[1] if len(hp) > 1 else hp[0]
        # облачность на завтра
        hc        = w["hourly"]["cloud_cover"]
        cloud_w   = clouds_word(hc[1] if len(hc) > 1 else hc[0])
        strong    = w["strong_wind"]
        fog       = w["fog_alert"]
        # завтрашний блок daily
        d = w["daily"]
        if isinstance(d, dict):
            # Open-Meteo даёт dict из списков
            tm = d["temperature_2m_max"]
            tn = d["temperature_2m_min"]
            day_max   = tm[1] if len(tm) > 1 else tm[0]
            night_min = tn[1] if len(tn) > 1 else tn[0]
        else:
            # list из блоков
            blk = d[1] if len(d) > 1 else d[0]
            tm  = blk["temperature_2m_max"]
            tn  = blk["temperature_2m_min"]
            day_max   = tm[1] if len(tm) > 1 else tm[0]
            night_min = tn[1] if len(tn) > 1 else tn[0]

    # Заголовок и базовые данные
    icon = WEATHER_ICONS.get(cloud_w, "🌦️")
    P.append(f"{icon} <b>Погода на завтра в Лимассоле {TOMORROW.format('DD.MM.YYYY')}</b>")
    P.append(f"<b>Темп. днём/ночью:</b> {day_max:.1f}/{night_min:.1f} °C")
    P.append(f"<b>Облачность:</b> {cloud_w}")
    P.append(f"<b>Ветер:</b> {wind_phrase(wind_kmh)} ({wind_kmh:.1f} км/ч, {compass(wind_deg)})")
    if strong:
        P.append("⚠️ Ветер может усилиться")
    if fog:
        P.append("🌁 Возможен туман, водите аккуратно")
    P.append(f"<b>Давление:</b> {press:.0f} гПа")
    P.append("———")

    # 2) Рейтинг городов по дн./ночн. темп.
    temps: dict[str, tuple[float, float]] = {}
    for city, coords in CITIES.items():
        w2 = get_weather(*coords)
        if not w2:
            continue
        if "current" in w2:
            tblk = w2["daily"][1]  # внимание: тоже index=1
            temps[city] = (tblk["temp"]["max"], tblk["temp"]["min"])
        else:
            d2 = w2["daily"]
            if isinstance(d2, dict):
                tm2 = d2["temperature_2m_max"]
                tn2 = d2["temperature_2m_min"]
                d_val = tm2[1] if len(tm2) > 1 else tm2[0]
                n_val = tn2[1] if len(tn2) > 1 else tn2[0]
            else:
                blk2 = d2[1] if len(d2) > 1 else d2[0]
                m_arr = blk2["temperature_2m_max"]
                n_arr = blk2["temperature_2m_min"]
                d_val = m_arr[1] if len(m_arr) > 1 else m_arr[0]
                n_val = n_arr[1] if len(n_arr) > 1 else n_arr[0]
            temps[city] = (d_val, n_val)

    P.append("🎖️ <b>Рейтинг городов (дн./ночь)</b>")
    sorted_c = sorted(temps.items(), key=lambda kv: kv[1][0], reverse=True)
    medals   = ["🥇","🥈","🥉","4️⃣"]
    for i, (city, (d_v, n_v)) in enumerate(sorted_c[:4]):
        P.append(f"{medals[i]} {city}: {d_v:.1f}/{n_v:.1f} °C")
    P.append("———")

    # 3) Качество воздуха и пыльца
    air    = get_air() or {}
    pollen = get_pollen() or {}

    P.append("🏙️ <b>Качество воздуха</b>")
    if air:
        status = f"{air['lvl']} (AQI {air['aqi']})"
        P.append(f"{status} | PM2.5: {safe(air['pm25'],' µg/м³')} | PM10: {safe(air['pm10'],' µg/м³')}")
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

    # 4) Геомагнитка, Шуман, вода, астрособытия
    kp, kp_state = get_kp()
    sch          = get_schumann()
    sst          = get_sst()
    astro        = astro_events()

    if kp is not None:
        P.append(f"🧲 <b>Геомагнитка</b> K-index: {kp:.1f} ({kp_state})")
    else:
        P.append("🧲 <b>Геомагнитка</b> нет данных")

    if sch.get("high"):
        P.append("🎵 <b>Шуман:</b> ⚡️ вибрации повышены")
    elif "freq" in sch:
        P.append(f"🎵 <b>Шуман:</b> ≈{sch['freq']:.1f} Гц")
    else:
        P.append(f"🎵 <b>Шуман:</b> {sch.get('msg','нет данных')}")

    if sst is not None:
        P.append(f"🌊 <b>Темп. воды:</b> {sst:.1f} °C")

    if astro:
        core = astro[0:2]
        P.append("🌌 <b>Астрособытия</b>\n" + " | ".join(core))

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


# ─────────── SEND ────────────────────────────────────────────────
async def send_main_post(bot: Bot) -> None:
    html = build_msg()
    logging.info("Preview: %s", html.replace("\n"," | ")[:200])
    try:
        await bot.send_message(
            CHAT_ID,
            html,
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
        logging.info("Message sent ✓")
    except tg_err.TelegramError as e:
        logging.error("Telegram error: %s", e)
        raise


async def send_poll_if_friday(bot: Bot) -> None:
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


async def send_photo(bot: Bot, photo_url: str) -> None:
    try:
        await bot.send_photo(
            CHAT_ID,
            photo=photo_url,
            caption="Фото дня • Unsplash"
        )
    except tg_err.TelegramError as e:
        logging.warning("Photo send error: %s", e)


async def main() -> None:
    bot = Bot(token=TOKEN)
    await send_main_post(bot)
    await send_poll_if_friday(bot)
    if UNSPLASH_KEY and (pendulum.now(TZ).day % 3 == 0):
        if photo := await fetch_unsplash_photo():
            await send_photo(bot, photo)
    logging.info("All tasks done ✓")


if __name__ == "__main__":
    asyncio.run(main())
