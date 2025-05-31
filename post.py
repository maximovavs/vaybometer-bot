#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
post.py  — вечерний пост VayboMeter-бота (испр. 2025-06-01)

• Астрособытия выводятся для ЗАВТРА, фаза отдельной строкой + 3 совета.
• VoC выводится там же (если ≥ 15 мин).
• Рекомендаций всегда 3 шт., без дубликатов.
• В рейтинге городов добавлены эмодзи погоды по WMO-коду.
• Исправлено «вините погода» → «вините погоду».
"""

from __future__ import annotations
import os, json, asyncio, logging, re, random
from pathlib import Path
from typing   import Dict, Any, List, Tuple, Optional

import pendulum, requests
from requests.exceptions import RequestException
from telegram import Bot, error as tg_err

# ── свои модули ──────────────────────────────────────────────
from utils   import compass, clouds_word, get_fact, AIR_EMOJI, pm_color, kp_emoji
from weather import get_weather, fetch_tomorrow_temps
from air     import get_air, get_sst, get_kp
from pollen  import get_pollen
from schumann import get_schumann
from lunar    import get_day_lunar_info
from gpt      import gpt_blurb

# ── базовые константы ───────────────────────────────────────
TZ          = pendulum.timezone("Asia/Nicosia")
TODAY       = pendulum.now(TZ).date()
TOMORROW    = TODAY.add(days=1)

TOKEN       = os.getenv("TELEGRAM_TOKEN", "")
CHAT_ID     = int(os.getenv("CHANNEL_ID", 0))

CITIES = {
    "Limassol": (34.707, 33.022),
    "Larnaca" : (34.916, 33.624),
    "Nicosia" : (35.170, 33.360),
    "Pafos"   : (34.776, 32.424),
    "Troodos" : (34.916, 32.823),   # плато ≈ высота 1300 м
}

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

# ── helpers ─────────────────────────────────────────────────
WMO_TEXT = {0:"ясно",1:"част. облач.",2:"облачно",3:"пасмурно",
            45:"туман",48:"изморозь",51:"морось",61:"дождь",71:"снег",95:"гроза"}
WMO_ICON = {0:"☀️",1:"⛅",2:"☁️",3:"☁️",
            45:"🌫️",48:"🌫️",51:"🌧️",61:"🌧️",71:"🌨️",95:"🌩️"}

def code_desc(c:int)->str:
    return f"{WMO_ICON.get(c,'🌡️')} {WMO_TEXT.get(c,'—')}"

def pressure_arrow(hourly:Dict[str,Any])->str:
    pr = hourly.get("surface_pressure", [])
    if len(pr) < 2: return "→"
    delta = pr[-1] - pr[0]
    return "↑" if delta > 1 else "↓" if delta < -1 else "→"

def schumann_line(s:Dict[str,Any])->str:
    if s.get("freq") is None:
        return "🎵 Шуман: н/д"
    f, amp = s["freq"], s["amp"]
    emoji = "🔴" if f < 7.6 else "🟣" if f > 8.1 else "🟢"
    return f"{emoji} Шуман: {f:.2f} Гц / {amp:.1f} pT {s['trend']}"

def get_schumann_safe()->Dict[str,Any]:
    sch = get_schumann()
    if sch.get("freq") is not None:
        sch["trend"] = "→"
        return sch
    # простейший кэш на случай сбоя
    fp = Path(__file__).parent / "schumann_hourly.json"
    if fp.exists():
        arr = json.loads(fp.read_text())
        if arr:
            last = arr[-1]
            return {"freq":round(last["freq"],2),"amp":round(last["amp"],1),"trend":"→"}
    return {}

# ── АСТРО-блок (для TOMORROW) ───────────────────────────────
def astro_block() -> List[str]:
    rec = get_day_lunar_info(TOMORROW)
    if not rec: return []

    out: List[str] = []

    # VoC
    voc = rec.get("void_of_course", {})
    if voc.get("start") and voc.get("end"):
        t1 = pendulum.parse(voc["start"])
        t2 = pendulum.parse(voc["end"])
        if (t2 - t1).in_minutes() >= 15:
            out.append(f"⚫️ VoC {t1.format('HH:mm')}–{t2.format('HH:mm')}")

    # Фаза + советы
    phase = rec.get("phase", "")
    # убираем процент освещения, чтобы короче
    phase = re.sub(r"\s*\(\d+%.*?⟩?\)", "", phase).strip()
    if phase: out.append(phase)

    tips = rec.get("advice", [])[:3]
    tips = [re.sub(r"^\d+\.\s*", "", t).strip() for t in tips]   # убираем «1.»
    for t in tips:
        out.append(f"• {t}")

    return out

# ── CORE BUILDER ────────────────────────────────────────────
def build_msg() -> str:
    P: List[str] = []
    P.append(f"<b>🌅 Добрый вечер! Погода на завтра ({TOMORROW.format('DD.MM.YYYY')})</b>")

    if (sst:=get_sst()) is not None:
        P.append(f"🌊 Темп. моря: {sst:.1f} °C")

    # Limassol summary
    lat, lon = CITIES["Limassol"]
    t_hi, t_lo = fetch_tomorrow_temps(lat, lon, tz=TZ.name)
    w = get_weather(lat, lon) or {}
    cur = w.get("current", {})
    avg_t = (t_hi+t_lo)/2 if t_hi and t_lo else cur.get("temperature",0)
    wind_kmh, wind_deg = cur.get("windspeed",0), cur.get("winddirection",0)
    clouds, press = cur.get("clouds",0), cur.get("pressure",1013)
    P.append(
        f"🌡️ Ср. темп: {avg_t:.0f} °C • {clouds_word(clouds)} "
        f"• 💨 {wind_kmh:.1f} км/ч ({compass(wind_deg)}) "
        f"• 💧 {press:.0f} гПа {pressure_arrow(w.get('hourly',{}))}"
    )
    P.append("———")

    # рейтинг городов
    temps: Dict[str,Tuple[float,float,int]]={}
    for city,(la,lo) in CITIES.items():
        hi, lo_t = fetch_tomorrow_temps(la,lo,tz=TZ.name)
        if hi is None: continue
        code = (get_weather(la,lo) or {}).get("daily",{}).get("weathercode",[0,0,0])[1]
        temps[city]=(hi,lo_t or hi,code)
    if temps:
        P.append("🎖️ <b>Рейтинг городов (дн./ночь · погода)</b>")
        medals=["🥇","🥈","🥉","4️⃣","5️⃣"]
        for i,(c,(hi,lo_t,code)) in enumerate(
                sorted(temps.items(), key=lambda kv: kv[1][0], reverse=True)[:5]):
            P.append(f"{medals[i]} {c}: {hi:.1f}/{lo_t:.1f} °C, {code_desc(code)}")
        P.append("———")

    # воздух + пыльца
    air = get_air() or {}; lvl = air.get("lvl","н/д")
    P.append("🏙️ <b>Качество воздуха</b>")
    P.append(f"{AIR_EMOJI.get(lvl,'⚪')} {lvl} (AQI {air.get('aqi','н/д')}) | "
             f"PM₂.₅: {pm_color(air.get('pm25'))} | PM₁₀: {pm_color(air.get('pm10'))}")
    if (pol:=get_pollen()):
        P.append("🌿 <b>Пыльца</b>")
        P.append(f"Деревья: {pol['tree']} | Травы: {pol['grass']} | "
                 f"Сорняки: {pol['weed']} — риск {pol['risk']}")
    P.append("———")

    # геомагнитка + шуман
    kp,kp_state=get_kp()
    P.append(f"{kp_emoji(kp)} Геомагнитка: Kp={kp:.1f} ({kp_state})" if kp else "🧲 Геомагнитка: н/д")
    P.append(schumann_line(get_schumann_safe()))
    P.append("———")

    # астрособытия
    astro_lines = astro_block()
    if astro_lines:
        P.append("🌌 <b>Астрособытия</b>")
        P.extend(astro_lines)
        P.append("———")

    # GPT-блок
    summary, tips = gpt_blurb("погода")
    summary = re.sub(r"вините\s+погода\b", "вините погоду", summary, flags=re.I)
    P.append(f"📜 <b>Вывод</b>\n{summary}")
    P.append("———")

    # рекомендации
    tips = list(dict.fromkeys(tips))          # убираем дубликаты
    while len(tips) < 3:                      # дополняем при нехватке
        tips.append(random.choice(tips))
    P.append("✅ <b>Рекомендации</b>")
    for t in tips[:3]:
        P.append(f"• {t}")
    P.append("———")

    # исторический факт
    P.append(f"📚 {get_fact(TOMORROW)}")
    return "\n".join(P)

# ── Telegram I/O ────────────────────────────────────────────
async def main()->None:
    bot = Bot(token=TOKEN)
    try:
        await bot.send_message(
            CHAT_ID, build_msg(),
            parse_mode="HTML", disable_web_page_preview=True
        )
        logging.info("Message sent ✓")
    except tg_err.TelegramError as e:
        logging.error("Telegram error: %s", e)

if __name__ == "__main__":
    asyncio.run(main())
