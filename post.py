#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
post.py — вечерний пост VayboMeter-бота для Кипра.

– Публикует прогноз на завтра (температура, ветер, давление и т. д.)
– Рейтинг городов (топ-5 по дневной температуре) с SST для прибрежных
– Качество воздуха + пыльца
– Геомагнитка + Шуман
– Астрособытия на завтра (VoC, фаза Луны, советы, next_event)
– Короткий вывод (динамический «Вините …»)
– Рекомендации (GPT-фоллбэк или health-coach) с тем же «виновником»
– Факт дня
"""

from __future__ import annotations
import os
import asyncio
import json
import logging
from pathlib import Path
from typing import Dict, Any, Tuple, List, Optional

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

# logging
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

# ─────────────────────────────────────────────────────────────────────────────
TZ = pendulum.timezone("Asia/Nicosia")
TODAY    = pendulum.now(TZ).date()
TOMORROW = TODAY.add(days=1)

TOKEN   = os.getenv("TELEGRAM_TOKEN", "")
CHAT_ID = int(os.getenv("CHANNEL_ID", "0"))
if not TOKEN or CHAT_ID == 0:
    logging.error("Не заданы TELEGRAM_TOKEN и/или CHANNEL_ID")
    exit(1)

CITIES: Dict[str, Tuple[float, float]] = {
    "Nicosia":   (35.170, 33.360),
    "Larnaca":   (34.916, 33.624),
    "Limassol":  (34.707, 33.022),
    "Pafos":     (34.776, 32.424),
    "Troodos":   (34.916, 32.823),
    "Ayia Napa": (34.988, 34.012),
}
COASTAL_CITIES = {"Larnaca", "Limassol", "Pafos", "Ayia Napa"}

WMO_DESC: Dict[int, str] = {
    0:  "☀️ ясно",
    1:  "⛅️ ч.обл",
    2:  "☁️ обл",
    3:  "🌥 пасм",
    45: "🌫 туман",
    48: "🌫 изморозь",
    51: "🌦 морось",
    61: "🌧 дождь",
    71: "❄️ снег",
    95: "⛈ гроза",
}

def code_desc(code: int) -> str:
    return WMO_DESC.get(code, "—")

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

def schumann_line(sch: Dict[str, Any]) -> str:
    if sch.get("freq") is None:
        return "🎵 Шуман: н/д"
    f, amp = sch["freq"], sch["amp"]
    emoji = "🔴" if f < 7.6 else "🟣" if f > 8.1 else "🟢"
    return f"{emoji} Шуман: {f:.2f} Гц / {amp:.1f} pT {sch['trend']}"

def get_schumann_with_fallback() -> Dict[str, Any]:
    sch = get_schumann()
    if sch.get("freq") is not None:
        sch["cached"] = False
        return sch
    cache = Path(__file__).parent / "schumann_hourly.json"
    if cache.exists():
        try:
            arr = json.loads(cache.read_text(encoding="utf-8"))
            if arr:
                last = arr[-1]
                pts  = arr[-24:]
                freqs = [p["freq"] for p in pts if isinstance(p.get("freq"), (int, float))]
                trend = "→"
                if len(freqs) > 1:
                    avg = sum(freqs[:-1])/(len(freqs)-1)
                    delta = freqs[-1] - avg
                    trend = "↑" if delta>=0.1 else "↓" if delta<=-0.1 else "→"
                return {
                    "freq":   round(last["freq"],2),
                    "amp":    round(last["amp"],1),
                    "trend":  trend,
                    "cached": True,
                }
        except Exception:
            pass
    return sch

def build_msg() -> str:
    P: List[str] = []

    # 1) Заголовок
    P.append(f"<b>🌅 Добрый вечер! Погода на завтра ({TOMORROW.format('DD.MM.YYYY')})</b>")

    # 2) Усреднённая SST
    sst_vals = []
    for city in COASTAL_CITIES:
        lat, lon = CITIES[city]
        tmp = get_sst(lat, lon)
        if tmp is not None:
            sst_vals.append(tmp)
    if sst_vals:
        avg_sst = sum(sst_vals)/len(sst_vals)
        P.append(f"🌊 Ср. темп. моря: {avg_sst:.1f} °C")
    else:
        P.append("🌊 Ср. темп. моря: н/д")
    P.append("———")

    # 3) Прогноз для Limassol
    lat_lims, lon_lims = CITIES["Limassol"]
    day_max, night_min = fetch_tomorrow_temps(lat_lims, lon_lims, tz=TZ.name)
    w = get_weather(lat_lims, lon_lims) or {}
    cur = w.get("current", {}) or {}

    # ветер на 12:00 из hourly
    wind_kmh = cur.get("windspeed", 0.0)
    wind_deg = cur.get("winddirection", 0.0)
    hourly = w.get("hourly", {}) or {}
    times  = hourly.get("time", [])
    ws     = hourly.get("wind_speed_10m", []) or hourly.get("windspeed_10m", [])
    wd     = hourly.get("wind_direction_10m", []) or hourly.get("winddirection_10m", [])
    if times and ws and wd:
        prefix = TOMORROW.format("YYYY-MM-DD")+"T12:"
        for i,t in enumerate(times):
            if t.startswith(prefix):
                try:
                    wind_kmh = float(ws[i]); wind_deg = float(wd[i])
                except: ...
                break

    press  = cur.get("pressure", 1013)
    clouds = cur.get("clouds", 0)
    arrow  = pressure_arrow(hourly)

    avg_temp = ((day_max + night_min)/2) if day_max is not None and night_min is not None else cur.get("temperature", 0.0)
    P.append(
        f"🌡️ Ср. темп: {avg_temp:.0f} °C • {clouds_word(clouds)} "
        f"• 💨 {wind_kmh:.1f} км/ч ({compass(wind_deg)}) • 💧 {press:.0f} гПа {arrow}"
    )
    P.append("———")

    # 4) Рейтинг городов
    temps: Dict[str, Tuple[float, float, int, Optional[float]]] = {}
    for city,(la,lo) in CITIES.items():
        d,n = fetch_tomorrow_temps(la, lo, tz=TZ.name)
        if d is None: continue
        wcod = get_weather(la, lo) or {}
        codes = wcod.get("daily", {}).get("weathercode", [])
        code_tmr = codes[1] if isinstance(codes, list) and len(codes)>1 else 0
        sst_c = get_sst(la, lo) if city in COASTAL_CITIES else None
        temps[city] = (d, n if n is not None else d, code_tmr, sst_c)

    if temps:
        P.append("🎖️ <b>Рейтинг городов (д./н.°C, погода, 🌊)</b>")
        medals = ["🥇","🥈","🥉","4️⃣","5️⃣","❄️"]
        top = sorted(temps.items(), key=lambda kv: kv[1][0], reverse=True)[:6]
        for i,(city,(d,n,code,sst_c)) in enumerate(top):
            desc = code_desc(code)
            if sst_c is not None:
                P.append(f"{medals[i]} {city}: {d:.1f}/{n:.1f}, {desc}, 🌊 {sst_c:.1f}")
            else:
                P.append(f"{medals[i]} {city}: {d:.1f}/{n:.1f}, {desc}")
        P.append("———")

    # 5) Качество воздуха + пыльца
    air = get_air() or {}
    lvl = air.get("lvl","н/д")
    P.append("🏭 <b>Качество воздуха</b>")
    P.append(
        f"{AIR_EMOJI.get(lvl,'⚪')} {lvl} (AQI {air.get('aqi','н/д')}) | "
        f"PM₂.₅: {pm_color(air.get('pm25'))} | PM₁₀: {pm_color(air.get('pm10'))}"
    )
    if (p := get_pollen()):
        P.append("🌿 <b>Пыльца</b>")
        P.append(f"Деревья: {p['tree']} | Травы: {p['grass']} | Сорняки: {p['weed']} — риск {p['risk']}")
    P.append("———")

    # 6) Геомагнитка + Шуман
    kp, kp_state = get_kp()
    P.append(
        f"{kp_emoji(kp)} Геомагнитка: Kp={kp:.1f} ({kp_state})"
        if kp is not None else "🧲 Геомагнитка: н/д"
    )
    P.append(schumann_line(get_schumann_with_fallback()))
    P.append("———")

    # 7) Астрособытия
    P.append("🌌 <b>Астрособытия</b>")
    astro = astro_events(offset_days=1, show_all_voc=True)
    P.extend(astro if astro else ["— нет данных —"])
    P.append("———")

    # 8) Динамический «Вывод»
    culprit = "неблагоприятный прогноз погоды"  # ваша логика выбора
    P.append("📜 <b>Вывод</b>")
    P.append(f"Если что-то пойдёт не так, вините {culprit}! 😉")
    P.append("———")

    # 9) Рекомендации
    P.append("✅ <b>Рекомендации</b>")
    _, tips = gpt_blurb(culprit)
    for tip in tips[:3]:
        P.append(f"• {tip.strip()}")
    P.append("———")

    # 10) Факт дня
    P.append(f"📚 {get_fact(TOMORROW)}")

    return "\n".join(P)

async def send_main_post(bot: Bot) -> None:
    text = build_msg()
    logging.info("Preview: %s", text[:200].replace("\n"," | "))
    try:
        await bot.send_message(chat_id=CHAT_ID,
                               text=text,
                               parse_mode="HTML",
                               disable_web_page_preview=True)
        logging.info("Сообщение отправлено ✓")
    except tg_err.TelegramError as e:
        logging.error("Telegram error: %s", e)
        raise

async def send_poll_if_friday(bot: Bot) -> None:
    if pendulum.now(TZ).weekday() == 4:
        try:
            await bot.send_poll(
                chat_id=CHAT_ID,
                question="Как сегодня ваше самочувствие? 🤔",
                options=["🔥 Полон(а) энергии","🙂 Нормально","😴 Слегка вялый(ая)","🤒 Всё плохо"],
                is_anonymous=False, allows_multiple_answers=False
            )
        except tg_err.TelegramError:
            pass

async def main() -> None:
    bot = Bot(token=TOKEN)
    await send_main_post(bot)
    await send_poll_if_friday(bot)

if __name__ == "__main__":
    asyncio.run(main())