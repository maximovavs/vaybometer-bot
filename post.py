#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
post.py — вечерний пост VayboMeter-бота для Кипра.

В этой версии:
• Рейтинг городов (дн./ночь, погода по WMO-коду).
• Стрелка давления (↑/↓/→) по реальному суточному тренду (Open-Meteo hourly).
• Шуман: цветовой индикатор вместо «(кэш)».
• «Астрособытия»: фаза Луны + VoC + три совета без нумерации.
• Рекомендации (3 совета от GPT).
• Блок «факт дня» в конце.
"""

from __future__ import annotations
import os
import asyncio
import json
import logging
from pathlib import Path
from typing import Dict, Any, Tuple, Optional

import requests
import pendulum
from requests.exceptions import RequestException
from telegram import Bot, error as tg_err

from utils import (
    compass, clouds_word, get_fact,
    AIR_EMOJI, pm_color, kp_emoji
)
from weather import get_weather, fetch_tomorrow_temps
from air     import get_air, get_sst, get_kp
from pollen  import get_pollen
from schumann import get_schumann
from astro   import astro_events
from gpt     import gpt_blurb
from lunar   import get_day_lunar_info

# ─── Constants ───────────────────────────────────────────────────────
TZ       = pendulum.timezone("Asia/Nicosia")
TODAY    = pendulum.now(TZ).date()
TOMORROW = TODAY.add(days=1)

TOKEN   = os.getenv("TELEGRAM_TOKEN", "")
CHAT_ID = int(os.getenv("CHANNEL_ID", 0))

# Ключ Unsplash может использоваться для фонового фото, но здесь не актуально
UNSPLASH_KEY = os.getenv("UNSPLASH_KEY")

# Список городов для рейтинга
CITIES = {
    "Limassol": (34.707, 33.022),
    "Larnaca" : (34.916, 33.624),
    "Nicosia" : (35.170, 33.360),
    "Pafos"   : (34.776, 32.424),
    "Troodos" : (34.916, 32.823),
}

# Вопрос для ежедневного опроса по пятницам
POLL_QUESTION = "Как сегодня ваше самочувствие? 🤔"
POLL_OPTIONS  = ["🔥 Полон(а) энергии", "🙂 Нормально",
                 "😴 Слегка вялый(ая)", "🤒 Всё плохо"]

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

# ─── Перевод WMO-кодов в текстовые описания ───────────────────────────
WMO_DESC: Dict[int, str] = {
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
    # при необходимости добавьте другие коды
}

def code_desc(code: int) -> str:
    return WMO_DESC.get(code, "—")


# ─── Определение стрелки тренда давления ──────────────────────────────
def pressure_arrow(hourly: Dict[str, Any]) -> str:
    """
    Сравниваем давление на начало и конец суток (массив hourly['surface_pressure']).
    Если разница >1 → ↑, < -1 → ↓, иначе →.
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


# ─── Отображение Шуман-резонанса ───────────────────────────────────────
def schumann_line(sch: Dict[str, Any]) -> str:
    """
    Возвращает строку вида '🟢 Шуман: 7.83 Гц / 1.2 pT ↑' или '🔴 Шуман: 7.45 Гц / 0.8 pT →'.
    """
    if sch.get("freq") is None:
        return "🎵 Шуман: н/д"
    f   = sch["freq"]
    amp = sch["amp"]
    # если freq < 7.6 → красный, >8.1 → фиолетовый, иначе зелёный
    if f < 7.6:
        emoji = "🔴"
    elif f > 8.1:
        emoji = "🟣"
    else:
        emoji = "🟢"

    # тренд (строка "↑" или "↓" или "→")
    trend = sch.get("trend", "→")
    return f"{emoji} Шуман: {f:.2f} Гц / {amp:.1f} pT {trend}"


def get_schumann_with_fallback() -> Dict[str, Any]:
    """
    Сначала пытаемся получить текущие данные Schumann через get_schumann().
    Если нет данных (freq=None), читаем примерно последние 24 строки из schumann_hourly.json
    и вычисляем тренд.
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
                freqs = [p.get("freq", 0.0) for p in pts[:-1]]
                if len(freqs) >= 1:
                    avg = sum(freqs) / len(freqs)
                    delta = last.get("freq", 0.0) - avg
                    trend = "↑" if delta >= 0.1 else "↓" if delta <= -0.1 else "→"
                else:
                    trend = "→"
                return {
                    "freq": round(last.get("freq", 0.0), 2),
                    "amp":  round(last.get("amp", 0.0), 1),
                    "trend": trend,
                    "cached": True,
                    "high": False,
                }
        except Exception as e:
            logging.warning("Schumann fallback parse error: %s", e)

    return sch


# ─── Основной строитель сообщения ─────────────────────────────────────
def build_msg() -> str:
    P: list[str] = []

    # 1) Заголовок
    P.append(f"<b>🌅 Добрый вечер! Погода на завтра ({TOMORROW.format('DD.MM.YYYY')})</b>")

    # 2) Температура моря (Лимассол/Сервичное) — get_sst()
    if (sst := get_sst()) is not None:
        P.append(f"🌊 Темп. моря: {sst:.1f} °C")

    # —————————————————————————————————————————————————————————————
    # 3) Основная локация: Limassol (примерно центр Кипра)
    lat, lon = CITIES["Limassol"]
    day_max, night_min = fetch_tomorrow_temps(lat, lon)
    w = get_weather(lat, lon) or {}
    cur = w.get("current", {})

    # если Open-Meteo вернул и tmax, и tmin, усредняем; иначе берём текущую temp
    avg_temp = ((day_max + night_min) / 2) if (day_max is not None and night_min is not None) else cur.get("temperature", 0.0)

    wind_kmh = cur.get("windspeed", 0.0)
    wind_deg = cur.get("winddirection", 0.0)
    press    = cur.get("pressure", 1013)
    clouds   = cur.get("clouds", 0)

    P.append(
        f"🌡️ Ср. темп: {avg_temp:.0f} °C • {clouds_word(clouds)} "
        f"• 💨 {wind_kmh:.1f} км/ч ({compass(wind_deg)}) "
        f"• 💧 {press:.0f} гПа {pressure_arrow(w.get('hourly', {}))}"
    )
    P.append("———")

    # —————————————————————————————————————————————————————————————
    # 4) Рейтинг городов (дн./ночь, WMO-описание)
    # Собираем по всем городам: (дн.т, ночь.т, wmo_code)
    temps: Dict[str, Tuple[float, float, int]] = {}
    for city, (la, lo) in CITIES.items():
        d_t, n_t = fetch_tomorrow_temps(la, lo)
        if d_t is None:
            continue
        # Берём второй элемент массива daily.weathercode → погоду на завтра
        wcodes = get_weather(la, lo) or {}
        w_daily = wcodes.get("daily", {})
        codes   = w_daily.get("weathercode", [])
        code_tmr = codes[1] if len(codes) > 1 else codes[0] if codes else 0
        temps[city] = (d_t, n_t if n_t is not None else d_t, code_tmr)

    if temps:
        P.append("🎖️ <b>Рейтинг городов (дн./ночь, погода)</b>")
        # сортируем по убыванию дневной t и берём топ-5
        sorted_cities = sorted(temps.items(), key=lambda kv: kv[1][0], reverse=True)[:5]
        medals = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣"]
        for i, (city, (d, n, code)) in enumerate(sorted_cities):
            desc = code_desc(code)
            P.append(f"{medals[i]} {city}: {d:.1f}/{n:.1f} °C, {desc}")
        P.append("———")

    # —————————————————————————————————————————————————————————————
    # 5) Качество воздуха & пыльца
    air = get_air() or {}
    lvl = air.get("lvl", "н/д")
    P.append("🏙️ <b>Качество воздуха</b>")
    P.append(
        f"{AIR_EMOJI.get(lvl,'⚪')} {lvl} (AQI {air.get('aqi','н/д')}) | "
        f"PM₂.₅: {pm_color(air.get('pm25'))} | PM₁₀: {pm_color(air.get('pm10'))}"
    )

    if (pollen := get_pollen()):
        P.append("🌿 <b>Пыльца</b>")
        P.append(
            f"Деревья: {pollen['tree']} | Травы: {pollen['grass']} | "
            f"Сорняки: {pollen['weed']} — риск {pollen['risk']}"
        )
    P.append("———")

    # —————————————————————————————————————————————————————————————
    # 6) Геомагнитка + Шуман
    kp, kp_state = get_kp()
    if kp is not None:
        P.append(f"{kp_emoji(kp)} Геомагнитка: Kp={kp:.1f} ({kp_state})")
    else:
        P.append("🧲 Геомагнитка: н/д")

    P.append(schumann_line(get_schumann_with_fallback()))
    P.append("———")

    # —————————————————————————————————————————————————————————————
    # 7) Астрособытия
    P.append("🌌 <b>Астрособытия</b>")
    for line in astro_events():
        P.append(line)
    P.append("———")

    # —————————————————————————————————————————————————————————————
    # 8) Вывод от GPT
    summary, tips = gpt_blurb("погода")
    P.append(f"📜 <b>Вывод</b>\n{summary}")
    P.append("———")

    # 9) Рекомендации (3 совета от GPT)
    P.append("✅ <b>Рекомендации</b>")
    for t in tips:
        P.append(f"• {t}")
    P.append("———")

    # 10) Факт дня
    P.append(f"📚 {get_fact(TOMORROW)}")

    return "\n".join(P)


# ─── Отправка в Telegram ──────────────────────────────────────────────
async def send_main_post(bot: Bot) -> None:
    html = build_msg()
    logging.info("Preview: %s", html.replace("\n", " | ")[:250])
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
                question=POLL_QUESTION,
                options=POLL_OPTIONS,
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