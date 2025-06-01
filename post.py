#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
post.py — вечерний пост VayboMeter-бота.

Основная логика:
  1) Собирает погоду, море, воздух, пыльцу, Шумана, геомагнитку
  2) Формирует рейтинг городов (теперь 5 городов)
  3) Блок «Астрособытия» для ЗАВТРА (offset_days=1)
  4) GPT-вывод и Рекомендации (три совета)
  5) Факт «начала завтра» в конце
"""

import os
import asyncio
import logging
import json
from pathlib import Path
from typing import Any, Dict, Optional, Tuple, List

import requests
import pendulum
from requests.exceptions import RequestException
from telegram import Bot, error as tg_err

from utils    import compass, clouds_word, get_fact, AIR_EMOJI, pm_color, kp_emoji
from weather  import get_weather, fetch_tomorrow_temps
from air      import get_air, get_sst, get_kp
from pollen   import get_pollen
from schumann import get_schumann
from astro    import astro_events
from gpt      import gpt_blurb
from lunar    import get_day_lunar_info

# ─── Настройки ─────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

TZ = pendulum.timezone("Asia/Nicosia")
TODAY    = pendulum.now(TZ).date()
TOMORROW = TODAY.add(days=1)

TOKEN   = os.getenv("TELEGRAM_TOKEN", "")
CHAT_ID = int(os.getenv("CHANNEL_ID", 0))

# Города для рейтинга (теперь 5)
CITIES = {
    "Limassol": (34.707, 33.022),
    "Larnaca" : (34.916, 33.624),
    "Nicosia" : (35.170, 33.360),
    "Pafos"   : (34.776, 32.424),
    "Troodos" : (34.916, 32.823),
}

# ─── Helpers ────────────────────────────────────────────────────

def pressure_arrow(hourly: Dict[str, Any]) -> str:
    """
    Если давление (surface_pressure) растёт за сутки — ↑
    Если падает — ↓, иначе — →
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
    Форматирует строку с показателем Шумана:
    🟢 норма (≈7.8–8.1), 🟣 выше нормы (>8.1), 🔴 ниже нормы (<7.6)
    и тренд ↑/↓/→
    """
    if sch.get("freq") is None:
        return "🎵 Шуман: н/д"
    f = sch["freq"]
    amp = sch["amp"]
    # цветовой индикатор
    if f < 7.6:
        emoji = "🔴"
    elif f > 8.1:
        emoji = "🟣"
    else:
        emoji = "🟢"
    return f"{emoji} Шуман: {f:.2f} Гц / {amp:.1f} pT {sch['trend']}"


def get_schumann_with_fallback() -> Dict[str, Any]:
    """
    Если живые данные Шумана появились — возвращаем их.
    Иначе берём из schumann_hourly.json, вычисляем тренд, помечаем cached=True.
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
                if len(freqs) >= 2:
                    avg   = sum(freqs[:-1]) / (len(freqs) - 1)
                    delta = freqs[-1] - avg
                    trend = "↑" if delta >= 0.1 else "↓" if delta <= -0.1 else "→"
                else:
                    trend = "→"
                return {
                    "freq":  round(last["freq"], 2),
                    "amp":   round(last["amp"], 1),
                    "trend": trend,
                    "high":  False,
                    "cached": True,
                }
        except Exception as e:
            logging.warning("Schumann fallback parse error: %s", e)

    return sch


def fetch_tomorrow_temps(lat: float, lon: float, tz: str) -> Tuple[Optional[float], Optional[float]]:
    """
    Берёт из Open-Meteo прогнозную максимальную и минимальную температуру
    для указанной координаты на TOMORROW.
    """
    date = TOMORROW.to_date_string()
    url = "https://api.open-meteo.com/v1/forecast"
    params = {
        "latitude":   lat,
        "longitude":  lon,
        "timezone":   tz,
        "daily":      "temperature_2m_max,temperature_2m_min",
        "start_date": date,
        "end_date":   date,
    }
    try:
        r = requests.get(url, params=params, timeout=15)
        r.raise_for_status()
        daily = r.json().get("daily", {})
        tmax = daily.get("temperature_2m_max", [])
        tmin = daily.get("temperature_2m_min", [])
        return (tmax[0] if tmax else None,
                tmin[0] if tmin else None)
    except RequestException as e:
        logging.warning("fetch_tomorrow_temps error: %s", e)
        return None, None


# ─── Основной сборщик сообщения ─────────────────────────────────────
def build_msg() -> str:
    P: List[str] = []

    # 1) Заголовок
    P.append(f"<b>🌅 Добрый вечер! Погода на завтра ({TOMORROW.format('DD.MM.YYYY')})</b>")

    # 2) Температура моря (SST)
    if (sst := get_sst()) is not None:
        P.append(f"🌊 Темп моря: {sst:.1f} °C")

    # 3) Основной прогноз для Limassol
    lat, lon = CITIES["Limassol"]
    day_max, night_min = fetch_tomorrow_temps(lat, lon, tz=TZ.name)
    w = get_weather(lat, lon) or {}
    cur = w.get("current", {}) or {}
    avg_temp = (day_max + night_min) / 2 if day_max and night_min else cur.get("temperature", 0)

    wind_kmh = cur.get("windspeed", 0.0)
    wind_deg = cur.get("winddirection", 0.0)
    press    = cur.get("pressure", 1013)
    clouds   = cur.get("clouds", 0)

    arrow = pressure_arrow(w.get("hourly", {}))
    P.append(
        f"🌡️ Ср. темп: {avg_temp:.0f} °C • {clouds_word(clouds)} "
        f"• 💨 {wind_kmh:.1f} км/ч ({compass(wind_deg)}) "
        f"• 💧 {press:.0f} гПа {arrow}"
    )
    P.append("———")

    # 4) Рейтинг городов (дн./ночь, описание WMO-кода)
    temps: Dict[str, Tuple[float, float, int]] = {}
    for city, (la, lo) in CITIES.items():
        d, n = fetch_tomorrow_temps(la, lo, tz=TZ.name)
        if d is None:
            continue
        # вытаскиваем WMO код завтра
        wcodes = get_weather(la, lo) or {}
        code_daily = wcodes.get("daily", {}).get("weathercode", [])
        code_tmr = code_daily[1] if len(code_daily) > 1 else code_daily[0] if code_daily else 0
        temps[city] = (d, n or d, code_tmr)

    if temps:
        P.append("🎖️ <b>Рейтинг городов (дн./ночь, погода)</b>")
        medals = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣"]
        # сортируем по дневной температуре по убыванию
        sorted_cities = sorted(temps.items(), key=lambda kv: kv[1][0], reverse=True)
        for i, (city, (d, n, code)) in enumerate(sorted_cities[:5]):
            desc = clouds_word(code) if isinstance(code, int) else code
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

    # 7) Астрособытия (на завтра: offset_days=1)
    P.append("🌌 <b>Астрособытия</b>")
    astro_lines = astro_events(offset_days=1)
    if astro_lines:
        for line in astro_lines:
            P.append(line)
    else:
        P.append("—")  # если данных нет

    P.append("———")

    # 8) GPT-вывод + рекомендации
    summary, tips = gpt_blurb("погода")
    P.append(f"📜 <b>Вывод</b>\n{summary}")
    P.append("———")
    P.append("✅ <b>Рекомендации</b>")
    # выводим ровно три совета (если есть)
    for t in tips[:3]:
        P.append(f"• {t.strip()}")

    P.append("———")
    P.append(f"📚 {get_fact(TOMORROW)}")

    return "\n".join(P)


# ─── Telegram I/O ───────────────────────────────────────────────
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
    # если пятница, то опрос
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