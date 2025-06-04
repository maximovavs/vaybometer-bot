#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
post.py — вечерний пост VayboMeter-бота для Кипра.

– Публикует прогноз на завтра (температура, ветер, давление и т. д.)
– Рейтинг городов
– Качество воздуха + пыльца
– Геомагнитка + Шуман
– Астрособытия на завтра (VoC, фаза Луны, советы, next_event)
– Короткий вывод
– Рекомендации (GPT-фоллбэк или health-coach)
– Факт дня
"""

from __future__ import annotations
import os
import asyncio
import json
import logging
from pathlib import Path
from typing import Dict, Any, Tuple, List

import pendulum
from telegram import Bot, error as tg_err

from utils     import compass, clouds_word, get_fact, AIR_EMOJI, pm_color, kp_emoji
from weather   import get_weather, fetch_tomorrow_temps
from air       import get_air, get_sst, get_kp
from pollen    import get_pollen
from schumann  import get_schumann
from astro     import astro_events
from gpt       import gpt_blurb
from lunar     import get_day_lunar_info

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

# ─────────────────────────────────────────────────────────────────────────────
# Часовой пояс Кипра
TZ = pendulum.timezone("Asia/Nicosia")

# Сегодня и Завтра (в часовом поясе TZ)
TODAY    = pendulum.now(TZ).date()
TOMORROW = TODAY.add(days=1)

# Telegram-параметры
TOKEN   = os.getenv("TELEGRAM_TOKEN", "")
CHAT_ID = int(os.getenv("CHANNEL_ID", "0"))

if not TOKEN or CHAT_ID == 0:
    logging.error("Не заданы TELEGRAM_TOKEN и/или CHANNEL_ID")
    exit(1)

# Список городов Кипра и их координаты
CITIES: Dict[str, Tuple[float, float]] = {
    "Nicosia":  (35.170, 33.360),
    "Larnaca":  (34.916, 33.624),
    "Limassol": (34.707, 33.022),
    "Pafos":    (34.776, 32.424),
    "Troodos":  (34.916, 32.823),
}

# WMO-коды → краткое описание
WMO_DESC: Dict[int, str] = {
    0:  "ясно",
    1:  "малооблачно",
    2:  "облачно",
    3:  "пасмурно",
    45: "туман",
    48: "изморозь",
    51: "морось",
    61: "дождь",
    71: "снег",
    95: "гроза",
}

def code_desc(code: int) -> str:
    """
    Преобразует WMO-код в русский текст.
    """
    return WMO_DESC.get(code, "—")

def pressure_arrow(hourly: Dict[str, Any]) -> str:
    """
    Сравнивает давление в начале и в конце суток (список hourly.surface_pressure).
    Если данных мало — возвращает «→».
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
    Форматирует строку «Шуман» с цветовой индикацией частоты и тренда:
      – 🔴 если freq < 7.6 Гц
      – 🟢 если 7.6 ≤ freq ≤ 8.1
      – 🟣 если freq > 8.1
    Добавляем амплитуду (amp) и стрелку тренда (trend).
    """
    if sch.get("freq") is None:
        return "🎵 Шуман: н/д"
    f   = sch["freq"]
    amp = sch["amp"]
    if   f < 7.6:
        emoji = "🔴"
    elif f > 8.1:
        emoji = "🟣"
    else:
        emoji = "🟢"
    return f"{emoji} Шуман: {f:.2f} Гц / {amp:.1f} pT {sch['trend']}"

def get_schumann_with_fallback() -> Dict[str, Any]:
    """
    Сначала пробуем получить «живые» данные из get_schumann().
    Если там freq == None, читаем из локального кеша schumann_hourly.json
    и рассчитываем тренд по последним 24 часам.
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
                freqs = [p["freq"] for p in pts if isinstance(p.get("freq"), (int, float))]
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
    Собирает всё сообщение «вечернего поста» для Telegram:
      1) Заголовок
      2) Температура моря
      3) Прогноз для Limassol (avg temp, облака, ветер, давление)
      4) Рейтинг городов (топ-5 по дневным температурам)
      5) Качество воздуха + Пыльца
      6) Геомагнитка + Шуман
      7) Астрособытия на завтра (VoC, фаза, советы, next_event)
      8) Короткий вывод
      9) Рекомендации (GPT-фоллбэк или health-coach)
     10) Факт дня
    Каждый крупный блок разделён строкой «———» для визуальной сегментации.
    """
    P: List[str] = []

    # 1) Заголовок
    P.append(f"<b>🌅 Добрый вечер! Погода на завтра ({TOMORROW.format('DD.MM.YYYY')})</b>")

    # 2) Температура моря (SST)
    if (sst := get_sst()) is not None:
        P.append(f"🌊 Темп. моря: {sst:.1f} °C")
    else:
        P.append("🌊 Темп. моря: н/д")

    # 3) Прогноз для Limassol
    lat, lon = CITIES["Limassol"]
    day_max, night_min = fetch_tomorrow_temps(lat, lon, tz=TZ.name)
    w = get_weather(lat, lon) or {}
    cur = w.get("current", {}) or {}

    if day_max is not None and night_min is not None:
        avg_temp = (day_max + night_min) / 2
    else:
        avg_temp = cur.get("temperature", 0.0)

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

    # 4) Рейтинг городов (топ-5 по дневным температурам)
    temps: Dict[str, Tuple[float, float, int]] = {}
    for city, (la, lo) in CITIES.items():
        d, n = fetch_tomorrow_temps(la, lo, tz=TZ.name)
        if d is None:
            continue

        wcod = get_weather(la, lo) or {}
        daily_codes = wcod.get("daily", {}).get("weathercode", [])
        code_tmr: int = daily_codes[1] if (isinstance(daily_codes, list) and len(daily_codes) > 1) else 0

        temps[city] = (d, n if n is not None else d, code_tmr)

    if temps:
        P.append("🎖️ <b>Рейтинг городов (дн./ночь, погода)</b>")
        medals = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣"]
        sorted_cities = sorted(temps.items(), key=lambda kv: kv[1][0], reverse=True)[:5]
        for i, (city, (d, n, code)) in enumerate(sorted_cities):
            desc = code_desc(code)
            P.append(f"{medals[i]} {city}: {d:.1f}/{n:.1f} °C, {desc}")
        P.append("———")

    # 5) Качество воздуха + Пыльца
    air = get_air() or {}
    lvl = air.get("lvl", "н/д")
    P.append("🏭 <b>Качество воздуха</b>")
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
    P.append("🌌 <b>Астрособытия</b>")
    astro_lines: List[str] = astro_events(offset_days=1, show_all_voc=True)
    if astro_lines:
        P.extend(astro_lines)
    else:
        P.append("— нет данных —")
    P.append("———")

    # 8) Вывод
    P.append("📜 <b>Вывод</b>")
    P.append("Если завтра что-то пойдёт не так, вините погоду! 😉")
    P.append("———")

    # 9) Рекомендации (GPT-фоллбэк или health-coach)
    P.append("✅ <b>Рекомендации</b>")
    summary, tips = gpt_blurb("погода")
    if tips:
        for advice in tips[:3]:
            P.append(f"• {advice.strip()}")
    P.append("———")

    # 10) Факт дня
    P.append(f"📚 {get_fact(TOMORROW)}")

    return "\n".join(P)


async def send_main_post(bot: Bot) -> None:
    """
    Отправляет сформированное сообщение в Telegram.
    """
    html = build_msg()
    logging.info("Preview: %s", html.replace("\n", " | ")[:200])
    try:
        await bot.send_message(
            chat_id=CHAT_ID,
            text=html,
            parse_mode="HTML",
            disable_web_page_preview=True
        )
        logging.info("Сообщение отправлено ✓")
    except tg_err.TelegramError as e:
        logging.error("Telegram error: %s", e)
        raise


async def send_poll_if_friday(bot: Bot) -> None:
    """
    Если сегодня пятница, дополнительно отправляем опрос.
    """
    if pendulum.now(TZ).weekday() == 4:
        try:
            await bot.send_poll(
                chat_id=CHAT_ID,
                question="Как сегодня ваше самочувствие? 🤔",
                options=[
                    "🔥 Полон(а) энергии",
                    "🙂 Нормально",
                    "😴 Слегка вялый(ая)",
                    "🤒 Всё плохо"
                ],
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