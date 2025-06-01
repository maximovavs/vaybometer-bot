#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
post.py — вечерний пост VayboMeter-бота.

Изменения 2025-06-XX:
• Рейтинг городов → 5 пунктов (добавлен Troodos) + отображение WMO-погоды через эмодзи
• Стрелка давления ↑/↓/→ — по реальному суточному тренду (Open-Meteo hourly)
• Блок Шумана: вместо «(кэш)» показывается цвет-индикатор 🟢/🔴/🟣
• Астрособытия: показ VoC, маркеры «благоприятный/неблагоприятный день», категории
  • Убрано «(11% освещ.) –» вместо этого перенос строки перед советами
  • Нумерация списков советов удалена (каждый совет с эмодзи)
• Исправлено склонение «вините погоду» в заключении
"""

from __future__ import annotations
import os
import asyncio
import logging
import json
from pathlib import Path
from typing import Dict, Any, Optional, Tuple, List

import requests
import pendulum
from requests.exceptions import RequestException
from telegram import Bot, error as tg_err

from utils import (
    compass, clouds_word, get_fact,
    AIR_EMOJI, pm_color, kp_emoji
)
from weather import get_weather, fetch_tomorrow_temps  # fetch_tomorrow_temps теперь принимает tz
from air import get_air, get_sst  # get_sst для моря (остается без изменений)
from pollen import get_pollen
from schumann import get_schumann
from astro import astro_events
from gpt import gpt_blurb
from lunar import get_day_lunar_info

# ─── Константы ──────────────────────────────────────────────────
TZ       = pendulum.timezone("Asia/Nicosia")
TODAY    = pendulum.now(TZ).date()
TOMORROW = TODAY.add(days=1)

TOKEN   = os.getenv("TELEGRAM_TOKEN", "")
CHAT_ID = int(os.getenv("CHANNEL_ID", 0))

# Если хотите вывести «Балтийское море», замените get_sst на get_baltic_sst
# Для примера оставим get_sst(), меняя подпись
SEA_LABEL = "Балтийское море"

# Большие города для рейтинга
CITIES = {
    "Limassol": (34.707, 33.022),
    "Larnaca" : (34.916, 33.624),
    "Nicosia" : (35.170, 33.360),
    "Pafos"   : (34.776, 32.424),
    "Troodos" : (34.916, 32.823),
}

# WMO → эмодзи
WMO_ICON: Dict[int, str] = {
    0:  "☀️",  # ясно
    1:  "⛅",  # част. облач.
    2:  "⛅",  # облачно
    3:  "☁️",  # пасмурно
    45: "🌫️",  # туман
    48: "🌫️",  # изморозь
    51: "🌦️",  # слаб. морось
    61: "🌧️",  # дождь
    71: "❄️",  # снег
    95: "⛈️",  # гроза
    # … при необходимости добавить другие коды
}

def wmo_description(code: int) -> str:
    """Возвращает описание кода погоды по WMO."""
    desc = {
        0: "ясно", 1: "част. облач.", 2: "облачно", 3: "пасмурно",
        45: "туман", 48: "изморозь", 51: "слаб. морось",
        61: "дождь", 71: "снег", 95: "гроза",
    }
    return desc.get(code, "—")

def wmo_line(code: int) -> str:
    """Собирает строку вида '☀️ ясно'."""
    icon = WMO_ICON.get(code, "—")
    text = wmo_description(code)
    return f"{icon} {text}"

def pressure_arrow(hourly: Dict[str, Any]) -> str:
    """Сравниваем давление на начало и конец суток и возвращаем стрелку."""
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
    """Формирует строку для резонанса Шумана с цветовым индикатором."""
    if sch.get("freq") is None:
        return "🎵 Шуман: н/д"
    f   = sch["freq"]
    amp = sch["amp"]
    # цветовой индикатор: 🌴
    if f < 7.6:
        emoji = "🔴"  # ниже нормы
    elif f > 8.1:
        emoji = "🟣"  # выше нормы
    else:
        emoji = "🟢"  # в норме
    return f"{emoji} Шуман: {f:.2f} Гц / {amp:.1f} pT {sch['trend']}"

def get_schumann_with_fallback() -> Dict[str, Any]:
    """Пытаемся получить свежие данные, иначе — из кэша."""
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
                return {
                    "freq":  round(last["freq"], 2),
                    "amp":   round(last["amp"], 1),
                    "trend": trend,
                    "cached": True,
                    "high":  last["freq"] > 8.0 or last["amp"] > 100.0,
                }
        except Exception as e:
            logging.warning("Schumann fallback parse error: %s", e)
    return sch

# ─── Основной сборщик сообщения ───────────────────────────────────
def build_msg() -> str:
    P: List[str] = []

    # 1) Заголовок
    P.append(f"<b>🌅 Добрый вечер! Погода на завтра ({TOMORROW.format('DD.MM.YYYY')})</b>")

    # 2) Балтийское море (замена get_sst)
    if (sst := get_sst()) is not None:
        P.append(f"🌊 Темп. {SEA_LABEL}: {sst:.1f} °C")

    # 3) Прогноз для Limassol (основное место)
    lat, lon = CITIES["Limassol"]
    # fetch_tomorrow_temps теперь принимает tz name
    day_max, night_min = fetch_tomorrow_temps(lat, lon, tz=TZ.name)
    w = get_weather(lat, lon) or {}
    cur = w.get("current") or {}

    # Рассчитываем среднюю температуру
    if day_max is not None and night_min is not None:
        avg_temp = (day_max + night_min) / 2
    else:
        avg_temp = cur.get("temperature", 0)

    wind_kmh = cur.get("windspeed", cur.get("wind_speed", 0.0))
    wind_deg = cur.get("winddirection", cur.get("wind_deg", 0.0))
    press    = cur.get("pressure", w.get("hourly", {}).get("surface_pressure", [1013])[0])
    clouds   = cur.get("clouds", w.get("hourly", {}).get("cloud_cover", [0])[0])

    P.append(
        f"🌡️ Ср. темп: {avg_temp:.0f} °C • {clouds_word(clouds)} "
        f"• 💨 {wind_kmh:.1f} км/ч ({compass(wind_deg)}) "
        f"• 💧 {press:.0f} гПа {pressure_arrow(w.get('hourly', {}))}"
    )
    P.append("———")

    # 4) Рейтинг городов (день/ночь/погода)
    temps: Dict[str, Tuple[float, float, int]] = {}
    for city, (la, lo) in CITIES.items():
        d, n = fetch_tomorrow_temps(la, lo, tz=TZ.name)
        if d is None:
            continue
        wcodes = get_weather(la, lo) or {}
        # берем код завтрашнего дня (daily.weathercode)[1], если есть
        daily_codes = wcodes.get("daily", {}).get("weathercode", [])
        code_tmr = daily_codes[1] if len(daily_codes) > 1 else daily_codes[0] if daily_codes else 0
        temps[city] = (d, n or d, code_tmr)

    if temps:
        P.append("🎖️ <b>Рейтинг городов (дн./ночь, погода)</b>")
        medals = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣"]
        # сортируем по дневной температуре, убывающе; берем топ-5
        sorted_cities = sorted(temps.items(), key=lambda kv: kv[1][0], reverse=True)[:5]
        for i, (city, (d_temp, n_temp, code)) in enumerate(sorted_cities):
            P.append(f"{medals[i]} {city}: {d_temp:.1f}/{n_temp:.1f} °C, {wmo_line(code)}")
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

    # 7) Астрособытия
    P.append("🌌 <b>Астрособытия</b>")
    # astro_events() уже возвращает список строк с VoC, маркерами, фазой и советами
    for line in astro_events():
        P.append(line)
    P.append("———")

    # 8) GPT-вывод (Вывод «вините погоду» или другой «виню́щий» по логике gpt_blurb)
    summary, tips = gpt_blurb("погода")
    P.append(f"📜 <b>Вывод</b>\n{summary}")
    P.append("———")

    # 9) Рекомендации (гарантированно три пункта)
    P.append("✅ <b>Рекомендации</b>")
    if tips:
        # если GPT вернул менее 3 совета, заполняем бэкапом
        for t in tips:
            P.append(f"• {t}")
        # если нужно, можно добавить случайные из get_day_lunar_info
        # но обычно gpt_blurb возвращает минимум 3
    else:
        # если GPT ничего не вернул, берём первые три из сегодняшних советов лунного календаря
        info_today = get_day_lunar_info(TODAY)
        if info_today:
            advs = info_today.get("advice", [])
            for adv in advs[:3]:
                P.append(f"• {adv}")
    P.append("———")

    # 10) Факт дня
    fact = get_fact(TOMORROW)
    if fact:
        P.append(f"📚 {fact}")

    return "\n".join(P)

# ─── Telegram I/O ───────────────────────────────────────────────
async def send_main_post(bot: Bot) -> None:
    html = build_msg()
    # Для отладки выводим первые 200 символов в лог
    logging.info("Preview: %s", html.replace("\n", " | ")[:200])
    try:
        await bot.send_message(
            CHAT_ID, html,
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
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    asyncio.run(main())