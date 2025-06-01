#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
post.py — вечерний пост VayboMeter-бота.

Формирует и отправляет вечерний прогноз:
1) Погода, море, воздух, пыльца, Шуман, геомагнитка
2) Рейтинг городов (дн./ночь, погода)
3) Астрособытия на завтра (фаза Луны + 3 совета + VoC)
4) GPT-вывод и рекомендации из gpt_blurb
5) Интересный факт о месте / дате
"""

from __future__ import annotations
import os
import asyncio
import logging
import json
from pathlib import Path
from typing import Dict, Any, Optional, Tuple

import pendulum
import requests
from requests.exceptions import RequestException
from telegram import Bot, error as tg_err

from utils import (
    compass, clouds_word, get_fact,
    AIR_EMOJI, pm_color, kp_emoji, pressure_trend
)
from weather import get_weather, fetch_tomorrow_temps
from air     import get_air, get_sst, get_kp
from pollen  import get_pollen
from schumann import get_schumann
from astro    import astro_events
from gpt      import gpt_blurb
from lunar    import get_day_lunar_info

# ─── Настройки и константы ──────────────────────────────────────
TZ       = pendulum.timezone("Asia/Nicosia")
TODAY    = pendulum.now(TZ).date()
TOMORROW = TODAY.add(days=1)

TOKEN   = os.getenv("TELEGRAM_TOKEN", "")
CHAT_ID = int(os.getenv("CHANNEL_ID", 0))

# Список городов для рейтинга (дн./ночь)
CITIES = {
    "Limassol": (34.707, 33.022),
    "Larnaca" : (34.916, 33.624),
    "Nicosia" : (35.170, 33.360),
    "Pafos"   : (34.776, 32.424),
    "Troodos" : (34.916, 32.823),
}

# Превращаем WMO-коды в эмоджи/строку
WMO_DESC = {
    0:   "☀️ ясно",
    1:   "🌤 мал. облаков",
    2:   "⛅️ переменная облачность",
    3:   "☁️ пасмурно",
    45:  "🌫 туман",
    48:  "🌫 изморозь",
    51:  "🌧 морось",
    61:  "🌧 дождь",
    71:  "❄️ снег",
    95:  "⛈ гроза",
}
def code_desc(code: int) -> str:
    return WMO_DESC.get(code, "—")


# ─── Schumann helper ────────────────────────────────────────────
def get_schumann_with_fallback() -> Dict[str, Any]:
    """
    Получаем текущие Schumann-данные. Если нет «свежих» — читаем из cached файла schumann_hourly.json,
    считаем тренд и возвращаем последнее значение как «кэш».
    """
    sch = get_schumann()
    if sch.get("freq") is not None:
        # свежие данные есть — помечаем cached=False
        sch["cached"] = False
        return sch

    # fallback: берем последние 24 пункта из schumann_hourly.json
    cache_path = Path(__file__).parent / "schumann_hourly.json"
    if cache_path.exists():
        try:
            arr = json.loads(cache_path.read_text(encoding="utf-8"))
            if arr:
                last = arr[-1]
                pts = arr[-24:]
                freqs = [p["freq"] for p in pts if p.get("freq") is not None]
                if len(freqs) >= 2:
                    avg   = sum(freqs[:-1]) / (len(freqs) - 1)
                    delta = freqs[-1] - avg
                    trend = "↑" if delta >= 0.1 else "↓" if delta <= -0.1 else "→"
                else:
                    trend = "→"
                return {
                    "freq":   round(last["freq"], 2),
                    "amp":    round(last["amp"], 1),
                    "trend":  trend,
                    "cached": True,
                }
        except Exception as e:
            logging.warning("Schumann fallback parse error: %s", e)

    return sch

def schumann_line(sch: Dict[str, Any]) -> str:
    """
    Форматируем строку «Шуман»: цветовая индикация по частоте
    🔴 если freq < 7.6, 🟣 если > 8.1, 🟢 в норме (7.6–8.1).
    Плюс амплитуда и тренд.
    """
    if sch.get("freq") is None:
        return "🎵 Шуман: н/д"
    f = sch["freq"]
    amp = sch["amp"]
    if f < 7.6:
        emoji = "🔴"
    elif f > 8.1:
        emoji = "🟣"
    else:
        emoji = "🟢"
    return f"{emoji} Шуман: {f:.2f} Гц / {amp:.1f} pT {sch['trend']}"


# ─── Основной билдер текста ──────────────────────────────────────
def build_msg() -> str:
    """
    Собираем весь блок «вечернего поста»:
    1) Заголовок
    2) Темп моря (для Limassol по дефолту)
    3) Погода на завтра (Limassol)
    4) Рейтинг городов (Limassol, Larnaca, Nicosia, Pafos, Troodos)
    5) Качество воздуха + пыльца
    6) Геомагнитка (Kp) + Шуман
    7) Астрособытия на завтра
    8) GPT-вывод («Вывод» + «Рекомендации»)
    9) Интересный факт
    """
    P: list[str] = []

    # 1) Заголовок
    P.append(f"<b>🌅 Добрый вечер! Погода на завтра ({TOMORROW.format('DD.MM.YYYY')})</b>")

    # 2) Температура моря — для нашей основной локации Limassol
    if (sst := get_sst()) is not None:
        P.append(f"🌊 Темп. моря: {sst:.1f} °C")

    # 3) Прогноз погоды для Limassol на завтра
    lat, lon = CITIES["Limassol"]
    day_max, night_min = fetch_tomorrow_temps(lat, lon, tz=TZ.name)
    w = get_weather(lat, lon) or {}
    cur = w.get("current_weather", {}) or w.get("current", {}) or {}

    avg_temp = (day_max + night_min)/2 if (day_max is not None and night_min is not None) else cur.get("temperature", 0.0)
    wind_kmh = cur.get("windspeed") or cur.get("wind_speed", 0.0)
    wind_deg = cur.get("winddirection") or cur.get("wind_deg", 0.0)
    clouds   = cur.get("clouds") or cur.get("weathercode", 0)  # если вернулся WMO-код
    # Получаем именно проценты облачности, если они есть:
    try:
        clouds_pct = cur.get("clouds", 0)
    except:
        clouds_pct = 0

    # Давление и тренд по Open-Meteo hourly
    press     = cur.get("pressure") or w.get("hourly", {}).get("surface_pressure", [1013])[0]
    press_arr = w.get("hourly", {})
    arrow = pressure_trend(press_arr)

    P.append(
        f"🌡️ Ср. темп: {avg_temp:.0f} °C • {clouds_word(clouds_pct)} "
        f"• 💨 {wind_kmh:.1f} км/ч ({compass(wind_deg)}) "
        f"• 💧 {press:.0f} гПа {arrow}"
    )
    P.append("———")

    # 4) Рейтинг городов (дн./ночь, погода) — топ-5 по дню
    temps: Dict[str, Tuple[float,float,int]] = {}
    for city, (la, lo) in CITIES.items():
        d, n = fetch_tomorrow_temps(la, lo, tz=TZ.name)
        if d is None:
            continue
        wcodes = get_weather(la, lo) or {}
        # WMO-код погоды на завтра:
        code_arr = wcodes.get("daily", {}).get("weathercode", [])
        code_tmr = code_arr[1] if len(code_arr) > 1 else (code_arr[0] if code_arr else 0)
        temps[city] = (d, n or d, code_tmr)

    if temps:
        P.append("🎖️ <b>Рейтинг городов (дн./ночь, погода)</b>")
        medals = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣"]
        # сортируем по дн. температуре по убыванию, берём первые 5
        sorted_cities = sorted(temps.items(), key=lambda kv: kv[1][0], reverse=True)[:5]
        for i, (city, (d, n, code)) in enumerate(sorted_cities):
            P.append(f"{medals[i]} {city}: {d:.1f}/{n:.1f} °C, {code_desc(code)}")
        P.append("———")

    # 5) Качество воздуха + пыльца
    air = get_air() or {}
    lvl = air.get("lvl", "н/д")
    P.append("🏙️ <b>Качество воздуха</b>")
    P.append(
        f"{AIR_EMOJI.get(lvl, '⚪')} {lvl} (AQI {air.get('aqi', 'н/д')}) | "
        f"PM₂.₅: {pm_color(air.get('pm25'))} | PM₁₀: {pm_color(air.get('pm10'))}"
    )
    if (pollen := get_pollen()):
        P.append("🌿 <b>Пыльца</b>")
        P.append(
            f"Деревья: {pollen['tree']} | Травы: {pollen['grass']} | "
            f"Сорняки: {pollen['weed']} — риск {pollen['risk']}"
        )
    P.append("———")

    # 6) Геомагнитка + Шуман
    kp, kp_state = get_kp()
    if kp is not None:
        P.append(f"{kp_emoji(kp)} Геомагнитка: Kp={kp:.1f} ({kp_state})")
    else:
        P.append("🧲 Геомагнитка: н/д")

    P.append(schumann_line(get_schumann_with_fallback()))
    P.append("———")

    # 7) Астрособытия на завтра
    P.append("🌌 <b>Астрособытия (на завтра)</b>")
    astro_lines = astro_events(offset_days=1)  # <-- теперь передаём offset_days=1
    for line in astro_lines:
        P.append(line)
    P.append("———")

    # 8) GPT-вывод: «Вывод» + «Рекомендации»
    summary, tips = gpt_blurb("погода")
    P.append(f"📜 <b>Вывод</b>\n{summary}")
    P.append("———")
    P.append("✅ <b>Рекомендации</b>")
    for t in tips:
        P.append(f"• {t}")
    P.append("———")

    # 9) Интересный факт
    P.append(f"📚 {get_fact(TOMORROW)}")

    return "\n".join(P)


# ─── Telegram I/O ───────────────────────────────────────────────
async def send_main_post(bot: Bot) -> None:
    html = build_msg()
    logging.info("Preview: %s", html.replace("\n", " | ")[:200])
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


async def main() -> None:
    bot = Bot(token=TOKEN)
    await send_main_post(bot)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    asyncio.run(main())