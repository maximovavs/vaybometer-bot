#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
post.py  – вечерний пост «ВайбоМетра» (Кипр).

Главные блоки:
• море, погода, рейтинг городов (с эмодзи-иконками WMO)
• качество воздуха, пыльца
• геомагнитка + резонанс Шумана (цвет-индикатор)
• 🌌 Астрособытия (на ЗАВТРА) – VoC, фаза без процента, 3 совета,
  маркеры «благоприятно/неблагоприятно», категории ✂️/✈️/🛍/❤️
• вывод GPT + рекомендации + факт-CTA
"""

from __future__ import annotations
import os, json, asyncio, logging, random, re
from pathlib import Path
from typing import Dict, Any, List, Tuple, Optional

import requests, pendulum
from requests.exceptions import RequestException
from telegram import Bot, error as tg_err

# ─── внутренние модули ─────────────────────────────────────────
from utils   import compass, clouds_word, get_fact, AIR_EMOJI, pm_color, kp_emoji
from weather import get_weather, fetch_tomorrow_temps
from air     import get_air, get_sst, get_kp
from pollen  import get_pollen
from schumann import get_schumann
from astro    import astro_events                    # ← теперь берём готовый блок
from lunar    import get_day_lunar_info              # только для fallback при VoC-cache
from gpt      import gpt_blurb

# ─── базовые константы ─────────────────────────────────────────
TZ        = pendulum.timezone("Asia/Nicosia")
TODAY     = pendulum.now(TZ).date()
TOMORROW  = TODAY.add(days=1)

TOKEN     = os.getenv("TELEGRAM_TOKEN", "")
CHAT_ID   = int(os.getenv("CHANNEL_ID", 0))

CITIES = {
    "Limassol": (34.707, 33.022),
    "Larnaca" : (34.916, 33.624),
    "Nicosia" : (35.170, 33.360),
    "Pafos"   : (34.776, 32.424),
    "Troodos" : (34.916, 32.823),
}

# ─── WMO коды → текст+эмодзи ───────────────────────────────────
WMO_TEXT = {0:"ясно",1:"перем. облач.",2:"облачно",3:"пасмурно",
            45:"туман",48:"туман",51:"морось",61:"дождь",63:"дождь",
            71:"снег",95:"гроза"}
WMO_ICON = {0:"☀️",1:"⛅",2:"☁️",3:"☁️",
            45:"🌫️",48:"🌫️",51:"🌦️",61:"🌧️",63:"🌧️",
            71:"🌨️",95:"🌩️"}

def code_desc(code:int) -> str:
    return f"{WMO_ICON.get(code,'🌡️')} {WMO_TEXT.get(code,'—')}"

def pressure_arrow(hourly:Dict[str,Any]) -> str:
    pr = hourly.get("surface_pressure", [])
    if len(pr) < 2:
        return "→"
    delta = pr[-1] - pr[0]
    return "↑" if delta > 1 else "↓" if delta < -1 else "→"

# ─── ШУМАН ─────────────────────────────────────────────────────
def schumann_line(info:Dict[str,Any]) -> str:
    if info.get("freq") is None:
        return "🎵 Шуман: н/д"
    f = info["freq"]
    amp = info["amp"]
    mark = "🟢" if 7.6 <= f <= 8.1 else ("🟣" if f > 8.1 else "🔴")
    return f"{mark} Шуман: {f:.2f} Гц / {amp:.1f} pT {info.get('trend','→')}"

def safe_schumann() -> Dict[str,Any]:
    res = get_schumann()
    if res.get("freq") is not None:
        return res | {"trend": res.get("trend","→")}
    # fallback to cache
    fp = Path(__file__).parent / "schumann_hourly.json"
    if fp.exists():
        try:
            arr = json.loads(fp.read_text())
            if arr:
                last = arr[-1]
                return {"freq": round(last["freq"],2),
                        "amp":  round(last["amp"],1),
                        "trend":"→"}
        except Exception:
            pass
    return {}

# ─── culprit for GPT summary ──────────────────────────────────
def choose_culprit(press:float, kp:float, code:int, retro:bool=False) -> str:
    if press and press < 1005: return "низкое давление"
    if kp and kp >= 4:         return "магнитная буря"
    if code in {45,48}:        return "туман"
    if retro:                  return "ретроградный Меркурий"
    return random.choice(["циклон", "влага", "океанский бриз"])

# ─── главный билдёр ───────────────────────────────────────────
def build_msg() -> str:
    P: List[str] = []

    # — Заголовок —
    P.append(f"<b>🌅 Добрый вечер! Погода на завтра ({TOMORROW.format('DD.MM.YYYY')})</b>")

    # — Температура моря —
    if (sst := get_sst()) is not None:
        P.append(f"🌊 Темп. моря: {sst:.1f} °C")

    # — Limassol, как «база» —
    lat,lon = CITIES["Limassol"]
    hi,lo = fetch_tomorrow_temps(lat,lon,TZ.name)
    w_lim  = get_weather(lat,lon) or {}
    cur    = w_lim.get("current", {})
    avg_t  = (hi+lo)/2 if hi and lo else cur.get("temperature", 0)
    wind_s = cur.get("windspeed",0); wind_d = cur.get("winddirection",0)
    clouds = cur.get("clouds",0);   press   = cur.get("pressure",1013)

    P.append(f"🌡️ Ср. темп: {avg_t:.0f} °C • {clouds_word(clouds)} "
             f"• 💨 {wind_s:.1f} км/ч ({compass(wind_d)}) "
             f"• 💧 {press:.0f} гПа {pressure_arrow(w_lim.get('hourly',{}))}")
    P.append("———")

    # — Рейтинг городов —
    temps: Dict[str,Tuple[float,float,int]] = {}
    for city,(la,lo) in CITIES.items():
        hi_c,lo_c = fetch_tomorrow_temps(la,lo,TZ.name)
        if hi_c is None: continue
        code = (get_weather(la,lo) or {}).get("daily",{}).get("weathercode",[0,0,0])[1]
        temps[city] = (hi_c, lo_c or hi_c, code)
    if temps:
        P.append("🎖️ <b>Рейтинг городов (дн./ночь · погода)</b>")
        medals = ["🥇","🥈","🥉","4️⃣","5️⃣"]
        for i,(city,(h,l,code)) in enumerate(sorted(temps.items(),
                                            key=lambda kv: kv[1][0], reverse=True)[:5]):
            P.append(f"{medals[i]} {city}: {h:.1f}/{l:.1f} °C, {code_desc(code)}")
        P.append("———")

    # — Alerts: туман / дождь —
    code_lim = temps.get("Limassol",(0,0,0))[2]
    if code_lim in {45,48}:
        P.append("⚠️ Возможен туман утром — будьте внимательны за рулём.")
    rain_prob = (w_lim.get("daily",{}).get("precipitation_probability_max",[0,0,0])[1]
                 if w_lim else 0)
    if rain_prob and rain_prob > 50:
        P.append("☔ Осадки >50 % — зонт пригодится.")
    if len(P) and P[-1] != "———":
        P.append("———")

    # — Качество воздуха + пыльца —
    air = get_air() or {}; lvl = air.get("lvl","н/д")
    P.append("🏙️ <b>Качество воздуха</b>")
    P.append(f"{AIR_EMOJI.get(lvl,'⚪')} {lvl} (AQI {air.get('aqi','н/д')}) | "
             f"PM₂.₅: {pm_color(air.get('pm25'))} | PM₁₀: {pm_color(air.get('pm10'))}")
    if (pol := get_pollen()):
        P.append("🌿 <b>Пыльца</b>")
        P.append(f"Деревья: {pol['tree']} | Травы: {pol['grass']} | "
                 f"Сорняки: {pol['weed']} — риск {pol['risk']}")
    P.append("———")

    # — Space weather —
    kp_val,kp_state = get_kp()
    P.append(f"{kp_emoji(kp_val)} Геомагнитка: Kp={kp_val:.1f} ({kp_state})"
             if kp_val else "🧲 Геомагнитка: н/д")
    P.append(schumann_line(safe_schumann()))
    P.append("———")

    # — Астрособытия (на завтра) —
    astro_lines = astro_events(offset_days=1)   # ВАЖНО: завтра
    if astro_lines:
        P.append("🌌 <b>Астрособытия</b>")
        P.extend(astro_lines)
        P.append("———")

    # — GPT-вывод и советы —
    culprit = choose_culprit(press, kp_val, code_lim)
    summary, tips = gpt_blurb(culprit)
    summary = re.sub(r"\bвините\s+погода\b", "вините погоду", summary, flags=re.I)

    P.append(f"📜 <b>Вывод</b>\n{summary}")
    P.append("———")

    tips = list(dict.fromkeys(tips)) or ["Берегите себя!"]
    while len(tips) < 3:
        tips.append(random.choice(tips))
    P.append("✅ <b>Рекомендации</b>")
    for t in tips[:3]:
        P.append(f"• {t}")
    P.append("———")

    # — Факт + CTA —
    P.append(f"📚 {get_fact(TOMORROW)}")
    P.append("\nА вы уже решили, как проведёте вечер? 🌆")

    return "\n".join(P)

# ─── Telegram send ───────────────────────────────────────────
async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    bot = Bot(token=TOKEN)
    html = build_msg()
    try:
        await bot.send_message(CHAT_ID, html,
                               parse_mode="HTML",
                               disable_web_page_preview=True)
        logging.info("✓ Message sent")
    except tg_err.TelegramError as e:
        logging.error("Telegram error: %s", e)

if __name__ == "__main__":
    asyncio.run(main())