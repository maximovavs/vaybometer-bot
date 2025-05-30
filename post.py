#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
post.py  –  вечерний «VayboMeter»

• Погода, море, воздух, пыльца
• Геомагнитка, резонанс Шумана
• Астрособытия (фаза + 3 совета + VoC)
• Вывод + рекомендации от GPT (с надёжным fallback)
"""

from __future__ import annotations
import os, asyncio, logging, json
from pathlib import Path
from typing import Optional, Tuple, Dict, Any

import pendulum, requests
from requests.exceptions import RequestException
from telegram import Bot, error as tg_err

# ── внутренние модули ──────────────────────────────────────────
from utils import (
    compass, clouds_word, wind_phrase, get_fact,
    WEATHER_ICONS, AIR_EMOJI, pressure_trend, kp_emoji, pm_color
)
from weather  import get_weather
from air      import get_air, get_sst, get_kp
from pollen   import get_pollen
from schumann import get_schumann
from astro    import astro_events
from gpt      import gpt_blurb

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

# ─────────── Constants ─────────────────────────────────────────
TZ           = pendulum.timezone("Asia/Nicosia")
TODAY        = pendulum.now(TZ).date()
TOMORROW     = TODAY.add(days=1)

TOKEN        = os.getenv("TELEGRAM_TOKEN", "")
CHAT_ID      = int(os.getenv("CHANNEL_ID", 0))

CITIES = {
    "Limassol": (34.707, 33.022),
    "Larnaca" : (34.916, 33.624),
    "Nicosia" : (35.170, 33.360),
    "Pafos"   : (34.776, 32.424),
}

# ─────────── Schumann fallback ─────────────────────────────────
def get_schumann_with_fallback() -> Dict[str, Any]:
    sch = get_schumann()
    if sch.get("freq") is not None:
        return sch

    cache_path = Path(__file__).parent / "schumann_hourly.json"
    if cache_path.exists():
        try:
            arr = json.loads(cache_path.read_text())
            if arr:
                last = arr[-1]
                freqs = [p["freq"] for p in arr[-24:]]
                avg   = sum(freqs[:-1])/(len(freqs)-1) if len(freqs) > 1 else last["freq"]
                delta = last["freq"]-avg
                trend = "↑" if delta>=.1 else "↓" if delta<=-.1 else "→"
                return {"freq":round(last["freq"],2),
                        "amp": round(last["amp"],1),
                        "trend":trend}
        except Exception as e:
            logging.warning("Schumann fallback parse error: %s", e)
    return sch

# ─────────── helpers ───────────────────────────────────────────
def fetch_tomorrow_temps(lat: float, lon: float)->Tuple[Optional[float],Optional[float]]:
    url="https://api.open-meteo.com/v1/forecast"
    params={"latitude":lat,"longitude":lon,"timezone":TZ.name,
            "daily":"temperature_2m_max,temperature_2m_min",
            "start_date":TOMORROW.to_date_string(),"end_date":TOMORROW.to_date_string()}
    try:
        r=requests.get(url,params=params,timeout=15); r.raise_for_status()
        d=r.json()["daily"]
        return d["temperature_2m_max"][0],d["temperature_2m_min"][0]
    except Exception:
        return None,None

# ─────────── Main message builder ─────────────────────────────
def build_msg()->str:
    P: list[str]=[]
    P.append(f"<b>🌅 Добрый вечер! Погода на завтра ({TOMORROW.format('DD.MM.YYYY')})</b>")

    if (sst:=get_sst()) is not None:
        P.append(f"🌊 Темп. моря: {sst:.1f} °C")

    lat,lon=CITIES["Limassol"]
    day_max,night_min=fetch_tomorrow_temps(lat,lon)
    w=get_weather(lat,lon) or {}
    cur=w.get("current") or w.get("current_weather",{})
    avg=(day_max+night_min)/2 if day_max and night_min else cur.get("temperature",0)
    wind=cur.get("windspeed") or cur.get("wind_speed",0)
    wdir=cur.get("winddirection") or cur.get("wind_deg",0)
    press=cur.get("pressure") or w.get("hourly",{}).get("surface_pressure",[0])[0]
    clouds=cur.get("clouds") or w.get("hourly",{}).get("cloud_cover",[0])[0]

    P.append(f"🌡️ Ср. темп: {avg:.0f} °C • {clouds_word(clouds)} "
             f"• 💨 {wind:.1f} км/ч ({compass(wdir)}) • 💧 {press:.0f} гПа {pressure_trend(w)}")
    P.append("———")

    # рейтинг
    temps={}
    for city,(la,lo) in CITIES.items():
        d,n=fetch_tomorrow_temps(la,lo)
        if d is not None:
            temps[city]=(d,n or d)
    if temps:
        P.append("🎖️ <b>Рейтинг городов (дн./ночь)</b>")
        medals=["🥇","🥈","🥉","4️⃣"]
        for i,(city,(d,n)) in enumerate(sorted(temps.items(),key=lambda kv:kv[1][0],reverse=True)[:4]):
            P.append(f"{medals[i]} {city}: {d:.1f}/{n:.1f} °C")
        P.append("———")

    # воздух / пыльца
    air=get_air() or {}
    lvl=air.get("lvl","н/д")
    P.append("🏙️ <b>Качество воздуха</b>")
    P.append(f"{AIR_EMOJI.get(lvl,'⚪')} {lvl} (AQI {air.get('aqi','н/д')}) | "
             f"PM₂.₅: {pm_color(air.get('pm25'))} | PM₁₀: {pm_color(air.get('pm10'))}")
    if (p:=get_pollen()):
        P.append("🌿 <b>Пыльца</b>")
        P.append(f"Деревья: {p['tree']} | Травы: {p['grass']} | Сорняки: {p['weed']} — риск {p['risk']}")
    P.append("———")

    # Kp + Шуман
    kp,kps=get_kp()
    P.append(f"{kp_emoji(kp)} Геомагнитка: Kp={kp:.1f} ({kps})" if kp else "🧲 Геомагнитка: н/д")

    sch=get_schumann_with_fallback()
    if sch.get("freq") is not None:
        f=sch["freq"]
        lamp="🟢" if 7.6<=f<=8.3 else "🔴" if f<7.6 else "🟣"
        P.append(f"{lamp} Шуман: {f:.2f} Гц / {sch['amp']:.1f} пТ {sch['trend']}")
    else:
        P.append("🎵 Шуман: н/д")
    P.append("———")

    # Астрология
    P.append("🌌 <b>Астрособытия</b>")
    P.extend(astro_events())
    P.append("———")

    # GPT summary + tips (robust)
    try:
        summary,tips=gpt_blurb("погода")
        if not summary:
            summary="Завтра день обычный: пусть всё будет по-вашему!"
        if not tips:
            tips=["Сохраняйте баланс 💧","Проветривайте комнаты 🌬️","Пораньше ложитесь 💤"]
    except Exception as e:
        logging.warning("GPT fallback due to error: %s",e)
        summary="Завтра всё будет хорошо, но слушайте своё тело."
        tips=["Тёплый напиток утром ☕","Короткая разминка 🏃","10 минут без гаджетов 📵"]

    P.append(f"📜 <b>Вывод</b>\n{summary}")
    P.append("———")
    P.append("✅ <b>Рекомендации</b>")
    for t in tips:
        P.append(f"• {t}")
    P.append("———")
    P.append(f"📚 {get_fact(TOMORROW)}")

    return "\n".join(P)

# ─────────── Telegram I/O ─────────────────────────────────────
async def send_main_post(bot: Bot)->None:
    html=build_msg()
    await bot.send_message(CHAT_ID,html,parse_mode="HTML",disable_web_page_preview=True)

async def main():
    await send_main_post(Bot(token=TOKEN))

if __name__=="__main__":
    asyncio.run(main())
