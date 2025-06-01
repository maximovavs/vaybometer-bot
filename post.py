#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
post.py — вечерний пост VayboMeter-бота для Кипра.

В этой версии:
• Улучшены блоки рейтинга городов, Шумана, VoC в астрособытиях.
• Добавлен показатель тренда давления по данным Open-Meteo.
• В конце добавлен CTA для вовлечения читателей.
"""

from __future__ import annotations
import os
import asyncio
import json
import logging
from pathlib import Path
from typing import Dict, Any, Optional, Tuple, List

import requests
import pendulum
from requests.exceptions import RequestException
from telegram import Bot, error as tg_err

# ── внутренние модули ───────────────────────────────────────────────────
from utils import (
    compass,
    clouds_word,
    get_fact,
    AIR_EMOJI,
    pm_color,
    kp_emoji,
)
from weather import get_weather, fetch_tomorrow_temps, code_desc, pressure_arrow
from air     import get_air, get_sst, get_kp
from pollen  import get_pollen
from schumann import get_schumann
from astro   import astro_events
from gpt     import gpt_blurb
from lunar   import get_day_lunar_info

# ─────────────────────────────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

# │ Константы ──────────────────────────────────────────────────────────────
TZ       = pendulum.timezone("Asia/Nicosia")
TODAY    = pendulum.now(TZ).date()
TOMORROW = TODAY.add(days=1)

TOKEN   = os.getenv("TELEGRAM_TOKEN", "")
CHAT_ID = int(os.getenv("CHANNEL_ID", 0))

# Список городов с координатами (Limassol как основное место для прогноза)
CITIES = {
    "Limassol": (34.707, 33.022),
    "Larnaca" : (34.916, 33.624),
    "Nicosia" : (35.170, 33.360),
    "Pafos"   : (34.776, 32.424),
    "Troodos" : (34.916, 32.823),  # горный регион
}

# ── Schumann: отображаем цвет при помощи порогов ─────────────────────────
def schumann_line(sch: Dict[str, Any]) -> str:
    """
    Возвращает строку вида "🟢 Шуман: 7.83 Гц / 1.2 pT ↑",
    где цвет зависит от частоты: [<7.6: 🔴, 7.6–8.1: 🟢, >8.1: 🟣].
    """
    if sch.get("freq") is None:
        return "🎵 Шуман: н/д"
    f   = sch["freq"]
    amp = sch["amp"]
    # Выбор эмодзи по частоте
    if f < 7.6:
        emoji = "🔴"
    elif f > 8.1:
        emoji = "🟣"
    else:
        emoji = "🟢"
    trend = sch.get("trend", "")
    return f"{emoji} Шуман: {f:.2f} Гц / {amp:.1f} pT {trend}"

def get_schumann_with_fallback() -> Dict[str, Any]:
    """
    Пытаемся получить текущие данные Шумана. Если fetch не удался,
    используем кеш из schumann_hourly.json.
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
                if len(pts) >= 2:
                    freqs = [p["freq"] for p in pts[:-1]]
                    avg   = sum(freqs) / len(freqs)
                    delta = last["freq"] - avg
                    trend = "↑" if delta >= 0.1 else "↓" if delta <= -0.1 else "→"
                else:
                    trend = "→"
                return {"freq": round(last["freq"], 2),
                        "amp":  round(last["amp"], 1),
                        "trend": trend,
                        "cached": True,
                        "high": False}
        except Exception as e:
            logging.warning("Schumann cache parse error: %s", e)
    return sch

# ── Core builder ─────────────────────────────────────────────────────────
def build_msg() -> str:
    """
    Собирает текст сообщения о погоде, Шумане, астрособытиях и рекомендациях.
    """

    P: list[str] = []

    # 1) Заголовок
    P.append(f"<b>🌅 Добрый вечер! Погода на завтра ({TOMORROW.format('DD.MM.YYYY')})</b>")

    # 2) Температура моря (рядом с Limassol) — для Кипра это Средиземное море
    if (sst := get_sst()) is not None:
        P.append(f"🌊 Темп. моря: {sst:.1f} °C")

    # 3) Прогноз для Limassol: средняя дневная/ночная, облака, ветер, давление
    lat, lon = CITIES["Limassol"]
    day_max, night_min = fetch_tomorrow_temps(lat, lon, tz=TZ.name)
    w = get_weather(lat, lon) or {}
    cur = w.get("current", {})  # структура из Open-Meteo
    avg_temp = (day_max + night_min) / 2 if day_max is not None and night_min is not None else cur.get("temperature", 0)

    wind_kmh = cur.get("windspeed", 0)
    wind_deg = cur.get("winddirection", 0)
    press    = cur.get("pressure", 0)
    clouds   = cur.get("clouds", 0)
    # дополнительные hourly для тренда давления
    hourly = w.get("hourly", {})

    P.append(
        f"🌡️ Ср. темп: {avg_temp:.0f} °C • {clouds_word(clouds)}  "
        f"• 💨 {wind_kmh:.1f} км/ч ({compass(wind_deg)})  "
        f"• 💧 {press:.0f} гПа {pressure_arrow(hourly)}"
    )
    P.append("———")

    # 4) Рейтинг городов: топ-5 по дневной температуре + WMO код
    temps: Dict[str, Tuple[float, float, int]] = {}
    for city, (la, lo) in CITIES.items():
        d, n = fetch_tomorrow_temps(la, lo, tz=TZ.name)
        if d is None:
            continue
        wcodes = get_weather(la, lo) or {}
        # WMO-код берём из daily.weathercode: индекс 1 — завтрашний день
        daily_codes = wcodes.get("daily", {}).get("weathercode", [])
        code_tmr = daily_codes[1] if len(daily_codes) >= 2 else daily_codes[0] if daily_codes else 0
        temps[city] = (d, n or d, code_tmr)

    if temps:
        P.append("🎖️ <b>Рейтинг городов (дн./ночь, погода)</b>")
        medals = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣"]
        # Сортируем по дневной температуре (макс → мин) и берём первые 5
        sorted_cities = sorted(temps.items(), key=lambda kv: kv[1][0], reverse=True)[:5]
        for i, (city, (d, n, code)) in enumerate(sorted_cities):
            desc = code_desc(code)
            P.append(f"{medals[i]} {city}: {d:.1f}/{n:.1f} °C, {desc}")
        P.append("———")

    # 5) Качество воздуха и пыльца
    air = get_air() or {}
    lvl = air.get("lvl", "н/д")
    P.append("🏙️ <b>Качество воздуха</b>")
    P.append(
        f"{AIR_EMOJI.get(lvl, '⚪')} {lvl} (AQI {air.get('aqi','н/д')}) | "
        f"PM₂.₅: {pm_color(air.get('pm25'))} | PM₁₀: {pm_color(air.get('pm10'))}"
    )
    if (pollen := get_pollen()):
        P.append("🌿 <b>Пыльца</b>")
        P.append(
            f"Деревья: {pollen['tree']} | Травы: {pollen['grass']} | "
            f"Сорняки: {pollen['weed']} — риск {pollen['risk']}"
        )
    P.append("———")

    # 6) Геомагнитка и Шуман
    kp, kp_state = get_kp()
    if kp is not None:
        P.append(f"{kp_emoji(kp)} Геомагнитка: Kp={kp:.1f} ({kp_state})")
    else:
        P.append("🧲 Геомагнитка: н/д")

    sch = get_schumann_with_fallback()
    P.append(schumann_line(sch))
    P.append("———")

    # 7) Астрособытия
    P.append("🌌 <b>Астрособытия</b>")
    astro_lines = astro_events()
    if astro_lines:
        P.extend(astro_lines)
    else:
        P.append("Нет данных на сегодня.")
    P.append("———")

    # 8) GPT-вывод и рекомендации
    # Логика «винителя» выводится в gpt_blurb
    summary, tips = gpt_blurb("погода")
    P.append(f"📜 <b>Вывод</b>  \n{summary}")
    P.append("———")
    P.append("✅ <b>Рекомендации</b>")
    for t in tips:
        P.append(f"• {t}")
    P.append("———")

    # 9) Факт дня
    P.append(f"📚 {get_fact(TOMORROW)}")
    # 10) Призыв к обсуждению
    P.append("")
    P.append("<i>А вы уже решили, как проведёте вечер? 🌆</i>")

    return "\n".join(P)


# ── Telegram I/O ────────────────────────────────────────────────────────
async def send_main_post(bot: Bot) -> None:
    html = build_msg()
    logging.info("Preview (first 200 chars): %s", html.replace("\n", " | ")[:200])
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
    # Если сегодня пятница, отправляем опрос
    if pendulum.now(TZ).weekday() == 4:
        try:
            await bot.send_poll(
                CHAT_ID,
                question="Как сегодня ваше самочувствие? 🤔",
                options=["🔥 Полон(а) энергии", "🙂 Нормально",
                         "😴 Слегка вялый(ая)", "🤒 Всё плохо"],
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