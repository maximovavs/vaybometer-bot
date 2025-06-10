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
– Рекомендации (GPT-фоллбэк или health-coach)
– Факт дня
"""

from __future__ import annotations
import os, json, logging, asyncio
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

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

TZ        = pendulum.timezone("Asia/Nicosia")
TODAY     = pendulum.now(TZ).date()
TOMORROW  = TODAY.add(days=1)

TOKEN   = os.getenv("TELEGRAM_TOKEN", "")
CHAT_ID = int(os.getenv("CHANNEL_ID", "0"))
if not TOKEN or CHAT_ID == 0:
    logging.error("Не заданы TELEGRAM_TOKEN и/или CHANNEL_ID")
    exit(1)

CITIES = {
    "Nicosia":   (35.170, 33.360),
    "Larnaca":   (34.916, 33.624),
    "Limassol":  (34.707, 33.022),
    "Pafos":     (34.776, 32.424),
    "Troodos":   (34.916, 32.823),
    "Ayia Napa": (34.988, 34.012),
}
COASTAL_CITIES = {"Larnaca", "Limassol", "Pafos", "Ayia Napa"}

WMO_DESC = {
    0:  "☀️ ясно", 1: "⛅️ ч.обл", 2: "☁️ обл", 3: "🌥 пасм",
   45:  "🌫 туман", 48: "🌫 изморозь", 51: "🌦 морось",
   61:  "🌧 дождь", 71: "❄️ снег", 95: "⛈ гроза",
}

def code_desc(code: int) -> str:
    return WMO_DESC.get(code, "—")

def pressure_arrow(hourly: Dict[str, Any]) -> str:
    """
    Стрелка по изменению давления:
     ↑ если Δ > +1 hPa, ↓ если Δ < -1, иначе →
     Δ = last(surface_pressure) - first(surface_pressure)
    """
    pr = hourly.get("surface_pressure", [])
    if len(pr) >= 2:
        delta = pr[-1] - pr[0]
        if delta > 1:  return "↑"
        if delta < -1: return "↓"
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
            pts  = arr[-24:]
            freqs = [p["freq"] for p in pts if isinstance(p.get("freq"), (int, float))]
            trend = "→"
            if len(freqs) > 1:
                avg = sum(freqs[:-1])/(len(freqs)-1)
                Δ   = freqs[-1] - avg
                trend = "↑" if Δ>=0.1 else "↓" if Δ<=-0.1 else "→"
            last = arr[-1]
            return {"freq": round(last["freq"],2), "amp": round(last["amp"],1),
                    "trend": trend, "cached": True}
        except:
            pass
    return sch

def build_msg() -> str:
    P: List[str] = []

    # Заголовок
    P.append(f"<b>🌅 Добрый вечер! Погода на завтра ({TOMORROW.format('DD.MM.YYYY')})</b>")

    # 1) Усреднённая SST
    vals = []
    for c in COASTAL_CITIES:
        t = get_sst(*CITIES[c])
        if t is not None: vals.append(t)
    if vals:
        P.append(f"🌊 Ср. темп. моря: {sum(vals)/len(vals):.1f} °C")
    else:
        P.append("🌊 Ср. темп. моря: н/д")
    P.append("———")

    # 2) Прогноз Limassol
    lat, lon = CITIES["Limassol"]
    d_max, d_min = fetch_tomorrow_temps(lat, lon, tz=TZ.name)
    w = get_weather(lat, lon) or {}
    cur = w.get("current",{}) or {}

    # ветер в 12:00
    wind_kmh = cur.get("windspeed",0.0)
    wind_deg = cur.get("winddirection",0.0)
    hr = w.get("hourly",{}) or {}
    times = hr.get("time",[])
    ws10  = hr.get("wind_speed_10m",[]) or hr.get("windspeed_10m",[])
    wd10  = hr.get("wind_direction_10m",[]) or hr.get("winddirection_10m",[])
    if times and ws10 and wd10:
        pref = TOMORROW.format("YYYY-MM-DD")+"T12:"
        for i,t in enumerate(times):
            if t.startswith(pref):
                try:
                    wind_kmh = float(ws10[i]); wind_deg = float(wd10[i])
                except: pass
                break

    press  = cur.get("pressure",1013)
    clouds = cur.get("clouds",0)
    arrow  = pressure_arrow(hr)
    avg_t  = ((d_max+d_min)/2) if d_max is not None and d_min is not None else cur.get("temperature",0.0)

    P.append(
        f"🌡️ Ср. темп: {avg_t:.0f} °C • {clouds_word(clouds)} "
        f"• 💨 {wind_kmh:.1f} км/ч ({compass(wind_deg)}) • 💧 {press:.0f} гПа {arrow}"
    )
    P.append("———")

    # 3) Рейтинг городов
    temps: Dict[str,Tuple[float,float,int,Optional[float]]] = {}
    for city,(la,lo) in CITIES.items():
        tmax, tmin = fetch_tomorrow_temps(la,lo, tz=TZ.name)
        if tmax is None: continue
        src = get_weather(la,lo) or {}
        codes = src.get("daily",{}).get("weathercode",[])
        wc = codes[1] if isinstance(codes,list) and len(codes)>1 else 0
        sst = get_sst(la,lo) if city in COASTAL_CITIES else None
        temps[city] = (tmax, tmin or tmax, wc, sst)

    if temps:
        P.append("🎖️ <b>Рейтинг городов (д./н.°C, погода, 🌊)</b>")
        medals = ["🥇","🥈","🥉","4️⃣","5️⃣","❄️"]
        for i,(city,(tmax,tmin,code,sst)) in enumerate(sorted(temps.items(),
                                    key=lambda kv: kv[1][0], reverse=True)[:6]):
            line = f"{medals[i]} {city}: {tmax:.1f}/{tmin:.1f}, {code_desc(code)}"
            if sst is not None:
                line += f", 🌊 {sst:.1f}"
            P.append(line)
        P.append("———")

    # 4) Air & pollen
    air = get_air() or {}
    lvl = air.get("lvl","н/д")
    P.append("🏭 <b>Качество воздуха</b>")
    P.append(
        f"{AIR_EMOJI.get(lvl,'⚪')} {lvl} (AQI {air.get('aqi','н/д')}) | "
        f"PM₂.₅: {pm_color(air.get('pm25'))} | PM₁₀: {pm_color(air.get('pm10'))}"
    )
    if (p:=get_pollen()):
        P.append("🌿 <b>Пыльца</b>")
        P.append(f"Деревья: {p['tree']} | Травы: {p['grass']} | Сорняки: {p['weed']} — риск {p['risk']}")
    P.append("———")

    # 5) Геомагнитка & Шуман
    kp, ks = get_kp()
    P.append(f"{kp_emoji(kp)} Геомагнитка: Kp={kp:.1f} ({ks})" if kp else "🧲 Геомагнитка: н/д")
    P.append(schumann_line(get_schumann_with_fallback()))
    P.append("———")

    # 6) Астрособытия
    P.append("🌌 <b>Астрособытия</b>")
    astro = astro_events(offset_days=1, show_all_voc=True)
    P.extend(astro if astro else ["— нет данных —"])
    P.append("———")

    # 7) Вывод & советы
    culprit = "неблагоприятный прогноз погоды"  # ваша логика выбора
    P.append("📜 <b>Вывод</b>")
    P.append(f"Если что-то пойдёт не так, вините {culprit}! 😉")
    P.append("———")
    P.append("✅ <b>Рекомендации</b>")
    _, tips = gpt_blurb(culprit)
    for tip in tips[:3]:
        P.append(f"• {tip.strip()}")
    P.append("———")

    # Факт дня
    P.append(f"📚 {get_fact(TOMORROW)}")

    return "\n".join(P)

async def send_main_post(bot: Bot) -> None:
    text = build_msg()
    logging.info("Preview: %s", text[:200].replace("\n"," | "))
    try:
        await bot.send_message(chat_id=CHAT_ID, text=text,
                               parse_mode="HTML", disable_web_page_preview=True)
        logging.info("Отправлено ✓")
    except tg_err.TelegramError as e:
        logging.error("Telegram error: %s", e)

async def main() -> None:
    bot = Bot(token=TOKEN)
    await send_main_post(bot)

if __name__ == "__main__":
    asyncio.run(main())