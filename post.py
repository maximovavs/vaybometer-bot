#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
post.py — вечерний пост VayboMeter-бота
Обновлено 2025-06-XX
• рейтинг 5 городов (Troodos) + WMO-описание
• суточный тренд давления ↑ ↓ →
• индикатор Шумана 🟢 / 🔴 / 🟣
• астро-блок без “(… % освещ.)” и без нумерации советов
"""

from __future__ import annotations
import os, re, json, asyncio, logging
from pathlib import Path
from typing import Dict, Any, Tuple, List

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
#  lunar / get_day_lunar_info больше не нужен в этом файле

# ── базовые константы ─────────────────────────────────────────
TZ       = pendulum.timezone("Asia/Nicosia")
TODAY    = pendulum.now(TZ).date()
TOMORROW = TODAY.add(days=1)

TOKEN   = os.getenv("TELEGRAM_TOKEN", "")
CHAT_ID = int(os.getenv("CHANNEL_ID", 0))

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

# ── локации для рейтинга ───────────────────────────────────────
CITIES = {
    "Nicosia" : (35.170, 33.360),
    "Larnaca" : (34.916, 33.624),
    "Limassol": (34.707, 33.022),
    "Pafos"   : (34.776, 32.424),
    "Troodos" : (34.916, 32.823),
}

# ── WMO weather-code → краткое слово ───────────────────────────
WMO_DESC = {
    0:"ясно",1:"част.обл.",2:"облачно",3:"пасмурно",
    45:"туман",48:"изморось",51:"морось",53:"морось",
    61:"дождь",63:"дождь",65:"ливень",
    71:"снег",73:"снег",75:"снег",
    95:"гроза",96:"гроза+",99:"гроза+",
}
code_desc = lambda c: WMO_DESC.get(int(c), "—")

# ── суточный тренд давления ────────────────────────────────────
def pressure_arrow(hourly: Dict[str, Any]) -> str:
    prs = hourly.get("surface_pressure", [])
    if len(prs) < 2: return "→"
    delta = prs[-1] - prs[0]
    return "↑" if delta > 1 else "↓" if delta < -1 else "→"

# ── Шуман-строка (цветофор) ────────────────────────────────────
def schumann_line(s: Dict[str, Any]) -> str:
    if s.get("freq") is None:
        return "🎵 Шуман: н/д"
    f = s["freq"]; amp = s["amp"]
    emoji = "🔴" if f < 7.6 else "🟣" if f > 8.1 else "🟢"
    return f"{emoji} Шуман: {f:.2f} Гц / {amp:.1f} pT {s.get('trend','→')}"

def get_schumann_with_fallback() -> Dict[str,Any]:
    s = get_schumann()
    if s.get("freq") is not None:
        return s
    cache = Path(__file__).parent / "schumann_hourly.json"
    if cache.exists():
        try:
            arr = json.loads(cache.read_text())[-24:]
            if not arr: return {"freq":None}
            last = arr[-1]
            avg  = sum(x["freq"] for x in arr[:-1]) / max(1,len(arr)-1)
            delta= last["freq"]-avg
            trend= "↑" if delta>=.1 else "↓" if delta<=-.1 else "→"
            return {"freq":round(last["freq"],2),"amp":round(last["amp"],1),"trend":trend}
        except Exception as e:
            logging.warning("Schumann cache error: %s", e)
    return {"freq":None}

# ── GPT-блок с защитой (1-3 строки) ────────────────────────────
def safe_gpt_reco(topic:str) -> Tuple[str,List[str]]:
    summary, tips = gpt_blurb(topic)
    tips = [re.sub(r"^\d+[.)]\s*","",t).strip("• ").strip() for t in tips if t.strip()]
    if not tips:
        tips = ["Сегодня — прислушайтесь к своему состоянию 😉"]
    return summary, tips[:3]

# ── Чистка строк из astro_events() ─────────────────────────────
PCT_RE   = re.compile(r"\s*\(\d+% освещ\.\)\s*–?\s*")   # «(14% освещ.) –»
NUM_RE   = re.compile(r"^\d+[.)]\s*")                   # «1. » или «2) »

def clean_astro_line(line:str) -> str:
    line = PCT_RE.sub("\n", line)       # перенос после фазы
    line = NUM_RE.sub("", line)         # убираем нумерацию
    return line.strip()

# ── основная сборка сообщения ─────────────────────────────────
def build_msg() -> str:
    P : List[str]=[]
    P.append(f"<b>🌅 Добрый вечер! Погода на завтра ({TOMORROW.format('DD.MM.YYYY')})</b>")

    if (sst:=get_sst()) is not None:
        P.append(f"🌊 Темп. моря: {sst:.1f} °C")

    # Limassol
    la0,lo0 = CITIES["Limassol"]
    dmax,dmin = fetch_tomorrow_temps(la0,lo0, tz=TZ.name)
    w0   = get_weather(la0,lo0) or {}
    cur  = w0.get("current_weather", w0.get("current", {}))
    avgT = (dmax+dmin)/2 if dmax and dmin else cur.get("temperature",0)
    P.append(
        f"🌡️ Ср. темп: {avgT:.0f} °C • {clouds_word(cur.get('clouds',0))} "
        f"• 💨 {cur.get('windspeed',0):.1f} км/ч ({compass(cur.get('winddirection',0))}) "
        f"• 💧 {cur.get('pressure',1013):.0f} гПа {pressure_arrow(w0.get('hourly',{}))}"
    )
    P.append("———")

    # рейтинг городов
    temps: Dict[str,Tuple[float,float,int]]={}
    for city,(la,lo) in CITIES.items():
        d,n=fetch_tomorrow_temps(la,lo,tz=TZ.name)
        if d is None: continue
        code = (get_weather(la,lo) or {}).get("daily",{}).get("weathercode",[None,None])[1]
        temps[city]=(d,n or d,code)
    if temps:
        P.append("🎖️ <b>Рейтинг городов (дн./ночь, погода)</b>")
        medals=["🥇","🥈","🥉","4️⃣","5️⃣"]
        for i,(city,(d,n,code)) in enumerate(
            sorted(temps.items(), key=lambda kv: kv[1][0], reverse=True)[:5]):
            P.append(f"{medals[i]} {city}: {d:.1f}/{n:.1f} °C, {code_desc(code)}")
        P.append("———")

    # воздух + пыльца
    air=get_air() or {}; lvl=air.get("lvl","н/д")
    P.append("🏙️ <b>Качество воздуха</b>")
    P.append(f"{AIR_EMOJI.get(lvl,'⚪')} {lvl} (AQI {air.get('aqi','н/д')}) | "
             f"PM₂.₅: {pm_color(air.get('pm25'))} | PM₁₀: {pm_color(air.get('pm10'))}")
    if (pol:=get_pollen()):
        P.append("🌿 <b>Пыльца</b>")
        P.append(f"Деревья: {pol['tree']} | Травы: {pol['grass']} | "
                 f"Сорняки: {pol['weed']} — риск {pol['risk']}")
    P.append("———")

    # космическая погода
    kp,kp_state=get_kp()
    P.append(f"{kp_emoji(kp)} Геомагнитка: Kp={kp:.1f} ({kp_state})" if kp else "🧲 Геомагнитка: н/д")
    P.append(schumann_line(get_schumann_with_fallback()))
    P.append("———")

    # астрособытия
    P.append("🌌 <b>Астрособытия</b>")
    for raw in astro_events():
        P.append(clean_astro_line(raw))
    P.append("———")

    # вывод + рекомендации
    summary,tips=safe_gpt_reco("погода")
    P.append(f"📜 <b>Вывод</b>\n{summary}")
    P.append("———")
    P.append("✅ <b>Рекомендации</b>")
    for t in tips:
        P.append(f"• {t}")
    P.append("———")
    P.append(f"📚 {get_fact(TOMORROW)}")
    return "\n".join(P)

# ── Telegram I/O ───────────────────────────────────────────────
async def send_main_post(bot: Bot)->None:
    html=build_msg()
    logging.info("Preview: %s", html.replace('\n',' | ')[:300])
    try:
        await bot.send_message(
            CHAT_ID, html,
            parse_mode="HTML", disable_web_page_preview=True
        )
        logging.info("Message sent ✓")
    except tg_err.TelegramError as e:
        logging.error("Telegram error: %s", e); raise

async def send_poll_if_friday(bot: Bot)->None:
    if pendulum.now(TZ).weekday()==4:
        try:
            await bot.send_poll(
                CHAT_ID,"Как сегодня ваше самочувствие? 🤔",
                ["🔥 Полон(а) энергии","🙂 Нормально",
                 "😴 Слегка вялый(ая)","🤒 Всё плохо"],
                is_anonymous=False,allows_multiple_answers=False
            )
        except tg_err.TelegramError as e:
            logging.warning("Poll send error: %s", e)

async def main()->None:
    bot=Bot(token=TOKEN)
    await send_main_post(bot)
    await send_poll_if_friday(bot)

if __name__=="__main__":
    asyncio.run(main())
