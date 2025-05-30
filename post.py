#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
post.py — вечерний пост VayboMeter-бота
Обновлено 2025-06-XX
• рейтинг 5 городов (Troodos) + WMO-описание
• суточный тренд давления ↑ ↓ →
• индикатор Шумана 🟢 / 🔴 / 🟣   (без текста «(кэш)»)
• блок «Рекомендации» всегда выводит 1-3 строки
• формат «Астрособытий» берётся из astro.py (без процента)
"""

from __future__ import annotations
import os, asyncio, json, logging
from pathlib import Path
from typing import Dict, Any, Tuple, List, Optional

import requests, pendulum
from requests.exceptions import RequestException
from telegram import Bot, error as tg_err

# ── внутренние модули ──────────────────────────────────────────
from utils import (
    compass, clouds_word, get_fact,
    AIR_EMOJI, pm_color, kp_emoji
)
from weather  import get_weather, fetch_tomorrow_temps
from air      import get_air, get_sst, get_kp
from pollen   import get_pollen
from schumann import get_schumann
from astro    import astro_events
from gpt      import gpt_blurb
from lunar    import get_day_lunar_info          # для VoC при желании

# ─── базовые константы ─────────────────────────────────────────
TZ       = pendulum.timezone("Asia/Nicosia")
TODAY    = pendulum.now(TZ).date()
TOMORROW = TODAY.add(days=1)

TOKEN   = os.getenv("TELEGRAM_TOKEN", "")
CHAT_ID = int(os.getenv("CHANNEL_ID", 0))

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

# ─── локации — 5 пунктов рейтинга ──────────────────────────────
CITIES = {
    "Nicosia" : (35.170, 33.360),
    "Larnaca" : (34.916, 33.624),
    "Limassol": (34.707, 33.022),
    "Pafos"   : (34.776, 32.424),
    "Troodos" : (34.916, 32.823),     # ~плато, ориентировочно
}

# ─── WMO weather-code → краткое слово ──────────────────────────
WMO_DESC = {
    0: "ясно", 1: "част.обл.", 2: "облачно", 3: "пасмурно",
    45: "туман", 48: "изморось", 51: "морось", 53: "морось",
    61: "дождь", 63: "дождь", 65: "ливень",
    71: "снег", 73: "снег", 75: "снег",
    95: "гроза", 96: "гроза+", 99: "гроза+",
}
def code_desc(code: int) -> str:
    return WMO_DESC.get(int(code), "—")

# ─── стрелка давления по суточному ряду ────────────────────────
def pressure_arrow(hourly: Dict[str, Any]) -> str:
    prs: List[float] = hourly.get("surface_pressure", [])
    if len(prs) < 2:
        return "→"
    delta = prs[-1] - prs[0]
    return "↑" if delta > 1. else "↓" if delta < -1. else "→"

# ─── Шуман с индикатором-цветофором ────────────────────────────
def schumann_line(s: Dict[str, Any]) -> str:
    if s.get("freq") is None:
        return "🎵 Шуман: н/д"
    f, amp = s["freq"], s["amp"]
    if f < 7.6:   emoji = "🔴"
    elif f > 8.1: emoji = "🟣"
    else:         emoji = "🟢"
    return f"{emoji} Шуман: {f:.2f} Гц / {amp:.1f} pT {s['trend']}"

def get_schumann_with_fallback() -> Dict[str, Any]:
    s = get_schumann()
    if s.get("freq") is not None:
        s["trend"] = s.get("trend","→")
        return s
    cache = Path(__file__).parent / "schumann_hourly.json"
    if cache.exists():
        try:
            arr = json.loads(cache.read_text())[-24:]
            if arr:
                last = arr[-1]; avg = sum(x["freq"] for x in arr[:-1])/max(1,len(arr)-1)
                delta = last["freq"]-avg
                trend = "↑" if delta>=.1 else "↓" if delta<=-.1 else "→"
                return {"freq":round(last["freq"],2),"amp":round(last["amp"],1),"trend":trend}
        except Exception as e:
            logging.warning("Schumann cache error: %s", e)
    return {"freq":None}

# ─── блок «Рекомендации» всегда с 1-3 строками ─────────────────
def safe_gpt_reco(topic:str) -> Tuple[str,List[str]]:
    summary, tips = gpt_blurb(topic)
    tips = [t.strip("• ").strip() for t in tips if t.strip()]
    if not tips:
        tips = ["Сегодня – прислушайтесь к своему состоянию 😉"]
    return summary, tips[:3]

# ─── основная сборка сообщения ─────────────────────────────────
def build_msg() -> str:
    P: List[str] = []

    # — заголовок —
    P.append(f"<b>🌅 Добрый вечер! Погода на завтра ({TOMORROW.format('DD.MM.YYYY')})</b>")

    # — температура моря —
    if (sst := get_sst()) is not None:
        P.append(f"🌊 Темп. моря: {sst:.1f} °C")

    # — Limassol кратко —
    lat0, lon0 = CITIES["Limassol"]
    day_max, night_min = fetch_tomorrow_temps(lat0, lon0, tz=TZ.name)
    w0 = get_weather(lat0, lon0) or {}
    cur = w0.get("current_weather", w0.get("current", {}))
    avg_temp = (day_max+night_min)/2 if day_max and night_min else cur.get("temperature", 0)
    wind_kmh = cur.get("windspeed", 0); wind_deg = cur.get("winddirection", 0)
    press    = cur.get("pressure", 1013)
    clouds   = cur.get("clouds", 0)

    P.append(
        f"🌡️ Ср. темп: {avg_temp:.0f} °C • {clouds_word(clouds)} "
        f"• 💨 {wind_kmh:.1f} км/ч ({compass(wind_deg)}) "
        f"• 💧 {press:.0f} гПа {pressure_arrow(w0.get('hourly',{}))}"
    )
    P.append("———")

    # — рейтинг городов —
    temps: Dict[str, Tuple[float,float,int]] = {}
    for city,(la,lo) in CITIES.items():
        d,n = fetch_tomorrow_temps(la,lo,tz=TZ.name)
        if d is None: continue
        wx = get_weather(la,lo) or {}
        code = wx.get("daily",{}).get("weathercode",[None,None])[1]
        temps[city]=(d,n or d,code)
    if temps:
        P.append("🎖️ <b>Рейтинг городов (дн./ночь, погода)</b>")
        medals = ["🥇","🥈","🥉","4️⃣","5️⃣"]
        for i,(city,(d,n,code)) in enumerate(
            sorted(temps.items(), key=lambda kv: kv[1][0], reverse=True)[:5]):
            P.append(f"{medals[i]} {city}: {d:.1f}/{n:.1f} °C, {code_desc(code)}")
        P.append("———")

    # — воздух & пыльца —
    air = get_air() or {}
    lvl = air.get("lvl","н/д")
    P.append("🏙️ <b>Качество воздуха</b>")
    P.append(f"{AIR_EMOJI.get(lvl,'⚪')} {lvl} (AQI {air.get('aqi','н/д')}) | "
             f"PM₂.₅: {pm_color(air.get('pm25'))} | PM₁₀: {pm_color(air.get('pm10'))}")
    if (pol := get_pollen()):
        P.append("🌿 <b>Пыльца</b>")
        P.append(f"Деревья: {pol['tree']} | Травы: {pol['grass']} | "
                 f"Сорняки: {pol['weed']} — риск {pol['risk']}")
    P.append("———")

    # — космическая погода —
    kp, kp_state = get_kp()
    P.append(f"{kp_emoji(kp)} Геомагнитка: Kp={kp:.1f} ({kp_state})" if kp else "🧲 Геомагнитка: н/д")
    P.append(schumann_line(get_schumann_with_fallback()))
    P.append("———")

    # — астрособытия —
    P.append("🌌 <b>Астрособытия</b>")
    for line in astro_events():
        P.append(line)
    P.append("———")

    # — вывод и рекомендации —
    summary, tips = safe_gpt_reco("погода")
    P.append(f"📜 <b>Вывод</b>\n{summary}")
    P.append("———")
    P.append("✅ <b>Рекомендации</b>")
    for t in tips:
        P.append(f"• {t}")
    P.append("———")
    P.append(f"📚 {get_fact(TOMORROW)}")

    return "\n".join(P)

# ─── Telegram I/O ───────────────────────────────────────────────
async def send_main_post(bot: Bot) -> None:
    html = build_msg()
    logging.info("Preview: %s", html.replace('\n',' | ')[:300])
    try:
        await bot.send_message(
            CHAT_ID, html,
            parse_mode="HTML", disable_web_page_preview=True
        )
        logging.info("Message sent ✓")
    except tg_err.TelegramError as e:
        logging.error("Telegram error: %s", e)
        raise

async def send_poll_if_friday(bot: Bot) -> None:
    if pendulum.now(TZ).weekday() == 4:        # 0=Mon … 4=Fri
        try:
            await bot.send_poll(
                CHAT_ID, "Как сегодня ваше самочувствие? 🤔",
                ["🔥 Полон(а) энергии","🙂 Нормально",
                 "😴 Слегка вялый(ая)","🤒 Всё плохо"],
                is_anonymous=False, allows_multiple_answers=False
            )
        except tg_err.TelegramError as e:
            logging.warning("Poll send error: %s", e)

async def main() -> None:
    bot = Bot(token=TOKEN)
    await send_main_post(bot)
    await send_poll_if_friday(bot)

if __name__ == "__main__":
    asyncio.run(main())
