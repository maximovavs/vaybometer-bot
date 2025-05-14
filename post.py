#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import asyncio
import logging

import pendulum
from telegram import Bot, error as tg_err

from utils import (
    compass, clouds_word, wind_phrase, safe, get_fact,
    WEATHER_ICONS, AIR_EMOJI
)
from weather import get_weather
from air import get_air, get_pollen, get_sst, get_kp
from schumann import get_schumann
from astro import astro_events
from gpt import gpt_blurb

# ─────────── Logger ───────────────────────────────────────────────
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

# ─────────── Constants ────────────────────────────────────────────
TZ       = pendulum.timezone("Asia/Nicosia")
TODAY    = pendulum.now(TZ).date()
TOMORROW = TODAY.add(days=1)

TOKEN    = os.environ["TELEGRAM_TOKEN"]
CHAT_ID  = int(os.environ["CHANNEL_ID"])
UNSPLASH_KEY = os.getenv("UNSPLASH_KEY")

POLL_QUESTION = "Как сегодня ваше самочувствие? 🤔"
POLL_OPTIONS  = ["🔥 Полон(а) энергии", "🙂 Нормально", "😴 Слегка вялый(ая)", "🤒 Всё плохо"]


def build_msg() -> str:
    """Собирает HTML-сообщение для Telegram."""
    # 1) Погода
    w = get_weather(34.707, 33.022)
    if not w:
        raise RuntimeError("Источники погоды недоступны")

    # Унифицированный current
    if "current" in w:
        cur = w["current"]
    else:
        cur = w["current_weather"]

    # Давление, облака, ветер
    press     = cur["pressure"]
    cloud_pc  = cur["clouds"]
    cloud_w   = clouds_word(cloud_pc)
    wind_kmh  = (cur.get("wind_speed") or cur.get("windspeed")) * (3.6 if "wind_speed" in cur else 1)
    wind_deg  = cur.get("wind_deg") or cur.get("winddirection")

    # Температуры и код погоды
    if "current" in w:
        d = w["daily"][0]["temp"]
        day_max, night_min = d["max"], d["min"]
        wcode = cur.get("weather",[{"id":0}])[0]["id"]
    else:
        dblock = w["daily"]
        blk = dblock[0] if isinstance(dblock,list) else dblock
        temps = blk["temperature_2m_max"]; mins = blk["temperature_2m_min"]; codes = blk["weathercode"]
        day_max   = temps[1]  if len(temps)>1  else temps[0]
        night_min = mins[1]   if len(mins)>1   else mins[0]
        wcode     = codes[1]  if len(codes)>1  else codes[0]

    strong = w.get("strong_wind", False)
    fog    = w.get("fog_alert",   False)

    # 2) Рейтинг городов по температуре
    CITIES = {
        "Limassol": (34.707,33.022),
        "Larnaca" : (34.916,33.624),
        "Nicosia" : (35.170,33.360),
        "Pafos"   : (34.776,32.424),
    }
    temps = {}
    for city,(la,lo) in CITIES.items():
        w2 = get_weather(la, lo)
        if not w2: continue
        if "current" in w2:
            mx = w2["daily"][0]["temp"]["max"]
            mn = w2["daily"][0]["temp"]["min"]
        else:
            blk2 = w2["daily"][0] if isinstance(w2["daily"],list) else w2["daily"]
            arr_mx = blk2["temperature_2m_max"]
            arr_mn = blk2["temperature_2m_min"]
            mx = arr_mx[1] if len(arr_mx)>1 else arr_mx[0]
            mn = arr_mn[1] if len(arr_mn)>1 else arr_mn[0]
        temps[city] = (mx, mn)
    # Самый тёплый и прохладный (по дню)
    warm = max(temps, key=lambda c: temps[c][0])
    cold = min(temps, key=lambda c: temps[c][0])

    # 3) Воздух, пыльца, KP, SST, Шуман, Astro
    air   = get_air() or {}
    pollen= get_pollen() or {}
    kp, kp_state = get_kp()
    sst   = get_sst()
    sch   = get_schumann()
    astro = astro_events()

    # 4) Виновник для GPT
    if fog:
        culprit = "туман"
    elif kp_state=="буря":
        culprit = "магнитные бури"
    elif press<1007:
        culprit = "низкое давление"
    elif strong:
        culprit = "шальной ветер"
    else:
        culprit = "мини-парад планет"
    summary, tips = gpt_blurb(culprit)

    # 5) Сборка HTML
    icon = WEATHER_ICONS.get(cloud_w, "🌦️")
    lines = [
        f"{icon} <b>Погода на {TOMORROW.format('DD.MM.YYYY')} в Лимассоле</b>",
        f"<b>Темп.:</b> {day_max:.1f}/{night_min:.1f} °C",
        f"<b>Облачность:</b> {cloud_w}",
        f"<b>Ветер:</b> {wind_phrase(wind_kmh)} ({wind_kmh:.1f} км/ч, {compass(wind_deg)})",
        *(["⚠️ Ветер может усилиться"] if strong else []),
        *(["🌁 Возможен туман"] if fog else []),
        f"<b>Давление:</b> {press:.0f} гПа",
        "———",
        "<b>🌡️ Рейтинг городов (днём/ночью)</b>",
        *[f"{c}: {mx:.1f}/{mn:.1f} °C" for c,(mx,mn) in temps.items()],
        f"• Самый тёплый: {warm} | прохладный: {cold}",
        "———",
        "🏙️ <b>Качество воздуха</b>",
        f"{AIR_EMOJI.get(air.get('lvl'),'⚪')} AQI {air.get('aqi','—')} | "
        f"PM2.5: {safe(air.get('pm25'),' µg/м³')} | PM10: {safe(air.get('pm10'),' µg/м³')}",
    ]
    if pollen:
        idx = lambda v: ["нет","низкий","ум","высокий","оч. высокий","экстрим"][int(round(v))]
        lines += [
            "🌿 <b>Пыльца</b>",
            f"Деревья {idx(pollen['treeIndex'])}, Травы {idx(pollen['grassIndex'])}, Сорняки {idx(pollen['weedIndex'])}"
        ]
    lines += [
        f"🧲 <b>Геомагн. активность:</b> {kp:.1f} ({kp_state})" if kp is not None else "🧲 нет данных",
    ]
    if sch.get("high"):
        lines.append("🎵 <b>Шуман:</b> ⚡️ вибрации повышены")
    elif "freq" in sch:
        lines.append(f"🎵 <b>Шуман:</b> ≈{sch['freq']:.1f} Гц")
    else:
        lines.append(f"🎵 <b>Шуман:</b> {sch.get('msg','—')}")
    if sst is not None:
        lines.append(f"🌊 <b>Темп. воды:</b> {sst:.1f} °C")
    if astro:
        lines.append("🌌 <b>Астрособытия:</b> " + " | ".join(astro))
    lines += [
        "———",
        f"📜 <b>Вывод:</b> {summary}",
        "———",
        "✅ <b>Рекомендации:</b>",
        *[f"• {t}" for t in tips],
        "———",
        f"📚 {get_fact(TOMORROW)}",
    ]

    return "\n".join(lines)


async def send_main_post(bot: Bot, text: str) -> None:
    try:
        await bot.send_message(CHAT_ID, text, parse_mode="HTML", disable_web_page_preview=True)
    except tg_err.TelegramError as e:
        logging.error("Telegram send error: %s", e)
        raise

async def send_friday_poll(bot: Bot) -> None:
    try:
        await bot.send_poll(CHAT_ID, POLL_QUESTION, POLL_OPTIONS,
                            is_anonymous=False, allows_multiple_answers=False)
    except tg_err.TelegramError as e:
        logging.warning("Poll error: %s", e)

async def fetch_unsplash_photo() -> str | None:
    if not UNSPLASH_KEY:
        return None
    j = await asyncio.get_event_loop().run_in_executor(
        None,
        lambda: __import__("utils")._get("https://api.unsplash.com/photos/random",
                                         query="cyprus sunset", client_id=UNSPLASH_KEY)
    )
    return j.get("urls",{}).get("regular")

async def send_photo(bot: Bot, url: str) -> None:
    try:
        await bot.send_photo(CHAT_ID, photo=url, caption="Фото дня • Unsplash")
    except tg_err.TelegramError as e:
        logging.warning("Photo error: %s", e)


async def main() -> None:
    bot = Bot(TOKEN)
    html = build_msg()
    logging.info("Preview: %s", html.replace("\n"," | ")[:200])

    await send_main_post(bot, html)

    # опрос по пятницам
    if pendulum.now(TZ).is_friday():
        await send_friday_poll(bot)

    # фото раз в 3 дня (UTC)
    if UNSPLASH_KEY and (_dt := os.getenv("GITHUB_RUN_ID")) and (pendulum.now("UTC").day % 3 == 0):
        if photo_url := await fetch_unsplash_photo():
            await send_photo(bot, photo_url)

    logging.info("All done ✓")


if __name__ == "__main__":
    asyncio.run(main())
