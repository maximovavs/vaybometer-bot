#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
post.py — вечерний пост VayboMeter-бота.

Новое в этой версии (2025-06-XX)
• Рейтинг городов → 5 пунктов (добавлен Troodos) + расшифровка WMO-кода.
• Стрелка давления ↑/↓/→ — по реальному суточному тренду (Open-Meteo hourly).
• Блок Шумана: вместо «(кэш)» показывается цвет-индикатор
  🟢 норма ≈ 7.8 Hz 🔴 ниже нормы 🟣 выше нормы.
"""

from __future__ import annotations
import os, asyncio, json, logging
from pathlib import Path
from typing import Dict, Any, Optional, Tuple

import requests, pendulum
from requests.exceptions import RequestException
from telegram import Bot, error as tg_err

from utils import (
    compass, clouds_word, get_fact,
    AIR_EMOJI, pm_color, kp_emoji
)
from weather   import get_weather, fetch_tomorrow_temps           # ◀︎ уже готово в weather.py
from air       import get_air, get_sst, get_kp
from pollen    import get_pollen
from schumann  import get_schumann
from astro     import astro_events
from gpt       import gpt_blurb
from lunar     import get_day_lunar_info

# ─── Const ───────────────────────────────────────────────────────
TZ       = pendulum.timezone("Asia/Nicosia")
TODAY    = pendulum.now(TZ).date()
TOMORROW = TODAY.add(days=1)

TOKEN   = os.getenv("TELEGRAM_TOKEN", "")
CHAT_ID = int(os.getenv("CHANNEL_ID", 0))

# Ключи могут отсутствовать — ошибки не критичны
UNSPLASH_KEY = os.getenv("UNSPLASH_KEY")

CITIES = {
    "Limassol": (34.707, 33.022),
    "Larnaca" : (34.916, 33.624),
    "Nicosia" : (35.170, 33.360),
    "Pafos"   : (34.776, 32.424),
    "Troodos" : (34.916, 32.823),   # ≈ плато местности
}

POLL_QUESTION = "Как сегодня ваше самочувствие? 🤔"
POLL_OPTIONS  = ["🔥 Полон(а) энергии", "🙂 Нормально",
                 "😴 Слегка вялый(ая)", "🤒 Всё плохо"]

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

# ─── Weather helpers ─────────────────────────────────────────────
# Open-Meteo codes → краткое описание
WMO_DESC = {
    0: "ясно", 1: "част. облач.", 2: "облачно", 3: "пасмурно",
    45: "туман", 48: "изморозь", 51: "слаб. морось",
    61: "дождь", 71: "снег", 95: "гроза",
    # … можно расширить по желанию
}
def code_desc(code: int) -> str:
    return WMO_DESC.get(code, "—")

def pressure_arrow(hourly: Dict[str, Any]) -> str:
    """Сравниваем давление на начало и конец суток."""
    pr = hourly.get("surface_pressure", [])
    if len(pr) < 2:
        return "→"
    delta = pr[-1] - pr[0]
    if delta > 1.0:
        return "↑"
    if delta < -1.0:
        return "↓"
    return "→"

# ─── Schumann display ───────────────────────────────────────────
def schumann_line(sch: Dict[str, Any]) -> str:
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
                avg  = sum(p["freq"] for p in pts[:-1]) / max(1, len(pts)-1)
                delta= last["freq"]-avg
                trend= "↑" if delta>=.1 else "↓" if delta<=-.1 else "→"
                return {"freq":round(last["freq"],2),"amp":round(last["amp"],1),
                        "trend":trend,"cached":True,"high":False}
        except Exception as e:
            logging.warning("Schumann cache parse error: %s", e)
    return sch

# ─── Core builder ───────────────────────────────────────────────
def build_msg() -> str:
    P: list[str] = []

    P.append(f"<b>🌅 Добрый вечер! Погода на завтра ({TOMORROW.format('DD.MM.YYYY')})</b>")

    if (sst := get_sst()) is not None:
        P.append(f"🌊 Темп. моря: {sst:.1f} °C")

    # --- основная локация Limassol ---------------------------------
    lat, lon = CITIES["Limassol"]
    day_max, night_min = fetch_tomorrow_temps(lat, lon, tz=TZ.name)
    w = get_weather(lat, lon) or {}
    cur = w.get("current", {})
    avg_temp = (day_max + night_min)/2 if day_max and night_min else cur.get("temperature", 0)

    wind_kmh = cur.get("windspeed", 0)
    wind_deg = cur.get("winddirection", 0)
    press    = cur.get("pressure", 1013)
    clouds   = cur.get("clouds", 0)

    P.append(
        f"🌡️ Ср. темп: {avg_temp:.0f} °C • {clouds_word(clouds)} "
        f"• 💨 {wind_kmh:.1f} км/ч ({compass(wind_deg)}) "
        f"• 💧 {press:.0f} гПа {pressure_arrow(w.get('hourly',{}))}"
    )
    P.append("———")

    # --- рейтинг городов ------------------------------------------
    temps: Dict[str, Tuple[float,float,int]] = {}
    for city, (la, lo) in CITIES.items():
        d, n = fetch_tomorrow_temps(la, lo, tz=TZ.name)
        if d is None:
            continue
        wcodes = get_weather(la, lo) or {}
        code_tmr = wcodes.get("daily", {}).get("weathercode", [])[1] if wcodes else None
        temps[city] = (d, n or d, code_tmr or 0)

    if temps:
        P.append("🎖️ <b>Рейтинг городов (дн./ночь, погода)</b>")
        medals = ["🥇","🥈","🥉","4️⃣","5️⃣"]
        for i, (city, (d,n,code)) in enumerate(
                sorted(temps.items(), key=lambda kv: kv[1][0], reverse=True)[:5]):
            P.append(f"{medals[i]} {city}: {d:.1f}/{n:.1f} °C, {code_desc(code)}")
        P.append("———")

    # --- качество воздуха & пыльца --------------------------------
    air = get_air() or {}
    lvl = air.get("lvl","н/д")
    P.append("🏙️ <b>Качество воздуха</b>")
    P.append(f"{AIR_EMOJI.get(lvl,'⚪')} {lvl} (AQI {air.get('aqi','н/д')}) | "
             f"PM₂.₅: {pm_color(air.get('pm25'))} | PM₁₀: {pm_color(air.get('pm10'))}")

    if (pollen := get_pollen()):
        P.append("🌿 <b>Пыльца</b>")
        P.append(f"Деревья: {pollen['tree']} | Травы: {pollen['grass']} | "
                 f"Сорняки: {pollen['weed']} — риск {pollen['risk']}")
    P.append("———")

    # --- Space weather -------------------------------------------
    kp, kp_state = get_kp()
    P.append(f"{kp_emoji(kp)} Геомагнитка: Kp={kp:.1f} ({kp_state})" if kp else "🧲 Геомагнитка: н/д")

    P.append(schumann_line(get_schumann_with_fallback()))
    P.append("———")

    # --- Astro ----------------------------------------------------
    P.append("🌌 <b>Астрособытия</b>")
    for line in astro_events():
        P.append(line)
    P.append("———")

    # 8) Вывод и рекомендации GPT
    fog    = w.get("fog_alert", False)
    strong = w.get("strong_wind", False)
    if fog:               culprit = "туман"
    elif kp_state == "буря": culprit = "магнитные бури"
    elif press < 1007:      culprit = "низкое давление"
    elif strong:            culprit = "шальной ветер"
    else:                   culprit = "мини-парад планет"

    summary, tips = gpt_blurb(culprit)
    P.append(f"📜 <b>Вывод\n{summary}")
    P.append("———")
    P.append("✅ <b>Рекомендации</b>")
    for t in tips:
        P.append(f"{t}")
    P.append("———")
    P.append(f"📚 {get_fact(TOMORROW)}")

    return "\n".join(P)

    # ─────────── Отправка ─────────────────────────────────────────────
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
    
    async def fetch_unsplash_photo() -> Optional[str]:
        if not UNSPLASH_KEY:
            return None
        url = "https://api.unsplash.com/photos/random"
        resp = requests.get(
            url,
            params={"query": "cyprus coast sunset", "client_id": UNSPLASH_KEY},
            timeout=15
        )
        return resp.json().get("urls", {}).get("regular")
    
    async def send_photo(bot: Bot, photo_url: str) -> None:
        try:
            await bot.send_photo(CHAT_ID, photo=photo_url, caption="Фото дня • Unsplash")
        except tg_err.TelegramError as e:
            logging.warning("Photo send error: %s", e)
    
    async def main() -> None:
        bot = Bot(token=TOKEN)
        await send_main_post(bot)
        await send_poll_if_friday(bot)
        if UNSPLASH_KEY and TODAY.day % 3 == 0:
            photo = await fetch_unsplash_photo()
            if photo:
                await send_photo(bot, photo)
        logging.info("All tasks done ✓")
    
    if __name__ == "__main__":
        asyncio.run(main())
