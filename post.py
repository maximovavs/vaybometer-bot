#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
post.py — вечерний пост VayboMeter-бота.

Новое в этой версии:
• Рейтинг городов → 5 пунктов (добавлен Troodos) + расшифровка WMO-кода.
• Стрелка давления ↑/↓/→ — по реальному суточному тренду (Open-Meteo hourly).
• Блок Шумана: вместо «(кэш)» показывается цвет-индикатор
  🟢 норма ≈ 7.8 Hz 🔴 ниже нормы 🟣 выше нормы.
• В астроблоке:
    – выводятся три совета без нумерации,
    – вместо «(11 % освещ.) – » вставлен перенос строки,
    – убрано лишнее «–» после процента освещённости,
    – добавлен вывод VoC (если есть),
    – восстановлены категории (стрижки, поездки и т.д.) для «сегодня».
• Исправлено склонение «погода» в конце («времени» → «погоду»).
"""

from __future__ import annotations
import os, asyncio, json, logging
from pathlib import Path
from typing import Dict, Any, Tuple, Optional

import requests, pendulum
from requests.exceptions import RequestException
from telegram import Bot, error as tg_err

from utils import (
    compass, clouds_word, get_fact,
    AIR_EMOJI, pm_color, kp_emoji
)
from weather import get_weather, fetch_tomorrow_temps  # уже содержит базовые функции
from air     import get_air, get_sst, get_kp
from pollen  import get_pollen
from schumann import get_schumann
from astro   import astro_events
from gpt     import gpt_blurb
from lunar   import get_day_lunar_info

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

# ─── Константы ────────────────────────────────────────────────────
TZ       = pendulum.timezone("Asia/Nicosia")
TODAY    = pendulum.now(TZ).date()
TOMORROW = TODAY.add(days=1)

TOKEN    = os.getenv("TELEGRAM_TOKEN", "")
CHAT_ID  = int(os.getenv("CHANNEL_ID", 0))

# Cписок городов + координаты для прогноза
CITIES = {
    "Limassol": (34.707, 33.022),
    "Larnaca" : (34.916, 33.624),
    "Nicosia" : (35.170, 33.360),
    "Pafos"   : (34.776, 32.424),
    "Troodos" : (34.916, 32.823),
}

# Определение WMO-кодов и их описаний
WMO_DESC = {
    0:  "ясно",
    1:  "част. облач.",
    2:  "облачно",
    3:  "пасмурно",
    45: "туман",
    48: "изморозь",
    51: "слаб. морось",
    61: "дождь",
    71: "снег",
    95: "гроза",
}
def code_desc(code: int) -> str:
    return WMO_DESC.get(code, "—")

# Стрелка тренда давления (сравнивает давление с начала и конца суток)
def pressure_arrow(hourly: Dict[str, Any]) -> str:
    pr = hourly.get("surface_pressure", [])
    if len(pr) < 2:
        return "→"
    delta = pr[-1] - pr[0]
    if delta > 1.0:
        return "↑"
    if delta < -1.0:
        return "↓"
    return "→"

# Отображение строки Шумана с цветовой индикацией
def schumann_line(sch: Dict[str, Any]) -> str:
    if sch.get("freq") is None:
        return "🎵 Шуман: н/д"
    f   = sch["freq"]
    amp = sch["amp"]
    if f < 7.6:
        emoji = "🔴"
    elif f > 8.1:
        emoji = "🟣"
    else:
        emoji = "🟢"
    return f"{emoji} Шуман: {f:.2f} Гц / {amp:.1f} pT {sch['trend']}"

# Фолбэк для Шумана (если нет реальных данных, берём из schumann_hourly.json)
def get_schumann_with_fallback() -> Dict[str, Any]:
    sch = get_schumann()
    if sch.get("freq") is not None:
        sch["cached"] = False
        return sch

    cache_path = Path(__file__).parent / "schumann_hourly.json"
    if cache_path.exists():
        try:
            arr = json.loads(cache_path.read_text())
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
                return {
                    "freq":   round(last["freq"], 2),
                    "amp":    round(last["amp"], 1),
                    "trend":  trend,
                    "cached": True,
                }
        except Exception as e:
            logging.warning("Schumann cache parse error: %s", e)
    return sch

# ─── Основная функция формирования сообщения ─────────────────────────
def build_msg() -> str:
    P: list[str] = []

    # 1) Заголовок
    P.append(f"<b>🌅 Добрый вечер! Погода на завтра ({TOMORROW.format('DD.MM.YYYY')})</b>")

    # 2) Температура моря
    if (sst := get_sst()) is not None:
        P.append(f"🌊 Темп. моря: {sst:.1f} °C")

    # 3) Прогноз для Limassol (основной региональный показатель)
    lat, lon = CITIES["Limassol"]
    day_max, night_min = fetch_tomorrow_temps(la=lat, lo=lon, tz=TZ.name)
    w_full = get_weather(lat, lon) or {}
    cur    = w_full.get("current", {})
    avg_temp = (day_max + night_min) / 2 if day_max is not None and night_min is not None else cur.get("temperature", 0)

    wind_kmh = cur.get("windspeed", 0)
    wind_deg = cur.get("winddirection", 0)
    press    = cur.get("pressure", 1013)
    clouds   = cur.get("clouds", 0)

    P.append(
        f"🌡️ Ср. темп: {avg_temp:.0f} °C • {clouds_word(clouds)} "
        f"• 💨 {wind_kmh:.1f} км/ч ({compass(wind_deg)}) "
        f"• 💧 {press:.0f} гПа {pressure_arrow(w_full.get('hourly', {}))}"
    )
    P.append("———")

    # 4) Рейтинг городов: топ-5 по дневной температуре
    temps: Dict[str, Tuple[float, float, int]] = {}
    for city, (la, lo) in CITIES.items():
        d, n = fetch_tomorrow_temps(la, lo, tz=TZ.name)
        if d is None:
            continue
        wcity = get_weather(la, lo) or {}
        code_daily = None
        # Берём погоду по WMO-коду для следующего дня из daily.weathercode
        try:
            code_list = wcity.get("daily", {}).get("weathercode", [])
            if len(code_list) >= 2:
                code_daily = code_list[1]
        except Exception:
            code_daily = None
        temps[city] = (d, n or d, code_daily or 0)

    if temps:
        P.append("🎖️ <b>Рейтинг городов (дн./ночь, погода)</b>")
        medals = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣"]
        # Сортируем по убыванию дневной температуры и берём топ-5
        top5 = sorted(temps.items(), key=lambda kv: kv[1][0], reverse=True)[:5]
        for i, (city, (d, n, code)) in enumerate(top5):
            desc = code_desc(code)
            P.append(f"{medals[i]} {city}: {d:.1f}/{n:.1f} °C, {desc}")
        P.append("———")

    # 5) Качество воздуха и пыльца
    air = get_air() or {}
    lvl = air.get("lvl", "н/д")
    P.append("🏙️ <b>Качество воздуха</b>")
    P.append(
        f"{AIR_EMOJI.get(lvl, '⚪')} {lvl} (AQI {air.get('aqi', 'н/д')}) | "
        f"PM₂.₅: {pm_color(air.get('pm25'))} | PM₁₀: {pm_color(air.get('pm10'))}"
    )
    if pollen := get_pollen():
        P.append("🌿 <b>Пыльца</b>")
        P.append(
            f"Деревья: {pollen['tree']} | Травы: {pollen['grass']} | "
            f"Сорняки: {pollen['weed']} — риск {pollen['risk']}"
        )
    P.append("———")

    # 6) Космическая погода: геомагнитка и Шуман
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
        for line in astro_lines:
            P.append(line)
    else:
        P.append("—")  # если нет данных, просто рисуем черту
    P.append("———")

    # 8) GPT-вывод (её «виновник» — условие внутри gpt_blurb)
    summary, tips = gpt_blurb("погода")
    P.append(f"📜 <b>Вывод</b>\n{summary}")
    P.append("———")
    P.append("✅ <b>Рекомендации</b>")
    # тут выводим ровно три пункта
    for t in tips[:3]:
        P.append(f"• {t}")
    P.append("———")
    P.append(f"📚 {get_fact(TOMORROW)}")

    return "\n".join(P)

# ─── Функции отправки в Telegram ───────────────────────────────────────────
async def send_main_post(bot: Bot) -> None:
    html = build_msg()
    logging.info("Preview (первые 200 символов): %s", html.replace("\n", " | ")[:200])
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
    if pendulum.now(TZ).weekday() == 4:
        try:
            await bot.send_poll(
                CHAT_ID,
                question="Как сегодня ваше самочувствие? 🤔",
                options=["🔥 Полон(а) энергии", "🙂 Нормально", "😴 Слегка вялый(ая)", "🤒 Всё плохо"],
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