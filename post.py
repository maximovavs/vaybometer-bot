#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
post.py — вечерний пост VayboMeter-бота.

– Погода, рейтинг городов, качество воздуха, пыльца, геомагнитка, Шуман
– Короткий астроблок: фаза Луны, 3 совета, VoC
– Вывод: “Если завтра что-то пойдёт не так, вините погоду!”
– Три рекомендаций из GPT (или фолбек)
"""

from __future__ import annotations
import os
import asyncio
import json
import logging
from pathlib import Path
from typing import Dict, Any, Optional, Tuple

import requests
import pendulum
from requests.exceptions import RequestException
from telegram import Bot, error as tg_err

# ─────────────────────────────────────────────────────────────────────────────
# Утилиты и внутренние модули (в вашем проекте уже есть эти файлы)
from utils     import compass, clouds_word, get_fact, AIR_EMOJI, pm_color, kp_emoji
from weather   import get_weather, fetch_tomorrow_temps       # fetch_tomorrow_temps – для Open-Meteo
from air       import get_air, get_sst, get_kp
from pollen    import get_pollen
from schumann  import get_schumann
from astro     import astro_events
from gpt       import gpt_blurb
from lunar     import get_day_lunar_info

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

# ─────────── Константы ────────────────────────────────────────────────────────
TZ       = pendulum.timezone("Asia/Nicosia")
TODAY    = pendulum.now(TZ).date()
TOMORROW = TODAY.add(days=1)

TOKEN   = os.getenv("TELEGRAM_TOKEN", "")
CHAT_ID = int(os.getenv("CHANNEL_ID", 0))

# 5 городов (с добавлением «Troodos»)
CITIES = {
    "Nicosia":  (35.170, 33.360),
    "Larnaca":  (34.916, 33.624),
    "Limassol": (34.707, 33.022),
    "Pafos":    (34.776, 32.424),
    "Troodos":  (34.916, 32.823),
}

# WMO-коды → расшифровка для погоды
WMO_DESC: Dict[int, str] = {
    0:  "ясно",           1:  "малооблачно",   2:  "облачно",      3:  "пасмурно",
    45: "туман",          48: "изморозь",      51: "морось",      61:  "дождь",
    71: "снег",           95: "гроза",
    # …можно расширить под ваши нужды
}
def code_desc(code: int) -> str:
    return WMO_DESC.get(code, "—")

def pressure_arrow(hourly: Dict[str, Any]) -> str:
    """
    Сравниваем давление на начало и конец суток (Open-Meteo hourly).
    Если нет данных или мало данных — возвращаем “→”.
    """
    pr = hourly.get("surface_pressure", [])
    if len(pr) < 2:
        return "→"
    delta = pr[-1] - pr[0]
    if delta > 1.0:
        return "↑"
    if delta < -1.0:
        return "↓"
    return "→"


def schumann_line(sch: Dict[str, Any]) -> str:
    """
    Форматируем строку Шумана:
      – низкие (<7.6 Гц) → 🔴
      – нормальные (7.6–8.1) → 🟢
      – высокие (>8.1) → 🟣
    Тренд от sch["trend"], “cached” – не показываем.
    """
    if sch.get("freq") is None:
        return "🎵 Шуман: н/д"
    f   = sch["freq"]
    amp = sch["amp"]
    if   f < 7.6:     emoji = "🔴"
    elif f > 8.1:     emoji = "🟣"
    else:             emoji = "🟢"
    return f"{emoji} Шуман: {f:.2f} Гц / {amp:.1f} pT {sch['trend']}"


def get_schumann_with_fallback() -> Dict[str, Any]:
    """
    Пытаемся получить свежие данные Schumann.
    Если не удалось, берём из cache (schumann_hourly.json) и считаем тренд.
    """
    sch = get_schumann()
    if sch.get("freq") is not None:
        sch["cached"] = False
        return sch

    cache_path = Path(__file__).parent / "schumann_hourly.json"
    if cache_path.exists():
        try:
            arr = json.loads(cache_path.read_text(encoding="utf-8"))
            if arr:
                last = arr[-1]
                pts  = arr[-24:]
                freqs = [p["freq"] for p in pts if "freq" in p]
                if len(freqs) > 1:
                    avg   = sum(freqs[:-1]) / (len(freqs) - 1)
                    delta = freqs[-1] - avg
                    trend = "↑" if delta >= 0.1 else "↓" if delta <= -0.1 else "→"
                else:
                    trend = "→"
                return {
                    "freq":  round(last["freq"], 2),
                    "amp":   round(last["amp"], 1),
                    "trend": trend,
                    "cached": True,
                }
        except Exception as e:
            logging.warning("Schumann fallback parse error: %s", e)

    return sch


def build_msg() -> str:
    """
    Собираем основное сообщение для Telegram:
    1) Заголовок
    2) Температура моря
    3) Основной прогноз для Limassol (средняя температура, облачность, ветер, давление)
    4) Рейтинг городов (5 самых теплых по дню), с WMO
    5) Качество воздуха + пыльца
    6) Геомагнитка + Шуман
    7) Астрособытия: VoC, фаза + советы, next_event
    8) Вывод “Если завтра что-то пойдёт не так, вините погоду!”
    9) Рекомендации (3 совета через GPT / фолбек)
    10) Факт дня (get_fact)
    """
    P: list[str] = []

    # 1) Заголовок
    P.append(f"<b>🌅 Добрый вечер! Погода на завтра ({TOMORROW.format('DD.MM.YYYY')})</b>")

    # 2) Температура моря (SST)
    if (sst := get_sst()) is not None:
        P.append(f"🌊 Темп. моря: {sst:.1f} °C")

    # 3) Прогноз для Limassol
    lat, lon = CITIES["Limassol"]
    day_max, night_min = fetch_tomorrow_temps(lat, lon, tz=TZ.name)
    w = get_weather(lat, lon) or {}
    cur = w.get("current", {}) or {}

    # Если нет day_max/night_min, берём “cur[temperature]” (хотя иногда cur тоже пусто)
    avg_temp = ((day_max + night_min) / 2) if (day_max is not None and night_min is not None) else cur.get("temperature", 0.0)
    wind_kmh  = cur.get("windspeed", 0.0)
    wind_deg  = cur.get("winddirection", 0.0)
    press     = cur.get("pressure", 1013)
    clouds    = cur.get("clouds", 0)

    arrow = pressure_arrow(w.get("hourly", {}))
    P.append(
        f"🌡️ Ср. темп: {avg_temp:.0f} °C • {clouds_word(clouds)} "
        f"• 💨 {wind_kmh:.1f} км/ч ({compass(wind_deg)}) "
        f"• 💧 {press:.0f} гПа {arrow}"
    )
    P.append("———")

    # 4) Рейтинг городов (день/ночь/погода по WMO)
    temps: Dict[str, Tuple[float, float, int]] = {}
    for city, (la, lo) in CITIES.items():
        d, n = fetch_tomorrow_temps(la, lo, tz=TZ.name)
        if d is None:
            # если нет данных – просто пропускаем город
            continue

        wcod = get_weather(la, lo) or {}
        code_tmr = None
        if "daily" in wcod and isinstance(wcod["daily"].get("weathercode", []), list) and len(wcod["daily"]["weathercode"]) > 1:
            code_tmr = wcod["daily"]["weathercode"][1]
        code_tmr = code_tmr or 0

        temps[city] = (d, n if n is not None else d, code_tmr)

    if temps:
        P.append("🎖️ <b>Рейтинг городов (дн./ночь, погода)</b>")
        medals = ["🥇","🥈","🥉","4️⃣","5️⃣"]
        # сортируем по самой высокой дневной (d), берём топ-5
        sorted_cities = sorted(temps.items(), key=lambda kv: kv[1][0], reverse=True)[:5]
        for i, (city, (d, n, code)) in enumerate(sorted_cities):
            desc = code_desc(code)
            P.append(f"{medals[i]} {city}: {d:.1f}/{n:.1f} °C, {desc}")
        P.append("———")

    # 5) Качество воздуха и пыльца
    air = get_air() or {}
    lvl = air.get("lvl", "н/д")
    P.append("🏙️ <b>Качество воздуха</b>")
    P.append(f"{AIR_EMOJI.get(lvl,'⚪')} {lvl} (AQI {air.get('aqi','н/д')}) | PM₂.₅: {pm_color(air.get('pm25'))} | PM₁₀: {pm_color(air.get('pm10'))}")
    if (pollen := get_pollen()):
        P.append("🌿 <b>Пыльца</b>")
        P.append(f"Деревья: {pollen['tree']} | Травы: {pollen['grass']} | Сорняки: {pollen['weed']} — риск {pollen['risk']}")
    P.append("———")

    # 6) Геомагнитка + Шуман
    kp, kp_state = get_kp()
    if kp is not None:
        P.append(f"{kp_emoji(kp)} Геомагнитка: Kp={kp:.1f} ({kp_state})")
    else:
        P.append("🧲 Геомагнитка: н/д")

    P.append(schumann_line(get_schumann_with_fallback()))
    P.append("———")

    # 7) Астрособытия
    P.append("🌌 <b>Астрособытия</b>")
    astro = astro_events()  # список строк от astro_events()

    if astro:
        for line in astro:
            P.append(line)

    # Место для явного вывода VoC (если в astro_events его нет или вы хотите дубль)
    info_today = get_day_lunar_info(TODAY)
    if info_today:
        voc = info_today.get("void_of_course", {})
        if voc.get("start") and voc.get("end"):
            P.append(f"🕑 VoC: {voc['start']} → {voc['end']}")
    P.append("———")

    # 8) Вывод
    P.append("📜 <b>Вывод</b>")
    P.append("Если завтра что-то пойдёт не так, вините погоду! 😉")
    P.append("———")

    # 9) Рекомендации (GPT или фолбек)
    P.append("✅ <b>Рекомендации</b>")
    summary, tips = gpt_blurb("погода")
    for advice in tips[:3]:
        P.append(f"• {advice.strip()}")
    P.append("———")

    # 10) Факт дня
    P.append(f"📚 {get_fact(TOMORROW)}")

    return "\n".join(P)


async def send_main_post(bot: Bot) -> None:
    html = build_msg()
    logging.info("Preview: %s", html.replace("\n"," | ")[:250])
    try:
        await bot.send_message(
            CHAT_ID,
            html,
            parse_mode="HTML",
            disable_web_page_preview=True
        )
        logging.info("Message sent ✓")
    except tg_err.TelegramError as e:
        logging.error("Telegram error: %s", e)
        raise


async def send_poll_if_friday(bot: Bot) -> None:
    """Еженедельный опрос по пятницам."""
    if pendulum.now(TZ).weekday() == 4:
        try:
            await bot.send_poll(
                CHAT_ID,
                question="Как сегодня ваше самочувствие? 🤔",
                options=["🔥 Полон(а) энергии","🙂 Нормально","😴 Слегка вялый(ая)","🤒 Всё плохо"],
                is_anonymous=False,
                allows_multiple_answers=False
            )
        except tg_err.TelegramError as e:
            logging.warning("Poll send error: %s", e)


async def main() -> None:
    bot = Bot(token=TOKEN)
    await send_main_post(bot)
    await send_poll_if_friday(bot)


if __name__ == "__main__":
    asyncio.run(main())