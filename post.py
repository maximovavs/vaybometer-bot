#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
post.py — вечерний пост VayboMeter-бота (Кипр).

• Прогноз на завтра (температура, ветер, давление …)
• Рейтинг городов (с SST для прибрежных)
• Качество воздуха + пыльца + ☢️ Радиация
• Kp-индекс + резонанс Шумана
• Астрособытия
• «Вините …» + рекомендации
• Факт дня
"""

from __future__ import annotations
import os, json, logging, asyncio
from pathlib import Path
from typing import Dict, Any, Tuple, List, Optional

import pendulum
from telegram import Bot, error as tg_err

from utils   import compass, clouds_word, get_fact, AIR_EMOJI, pm_color, kp_emoji
from weather import get_weather, fetch_tomorrow_temps
from air     import get_air, get_sst, get_kp
from pollen  import get_pollen
from schumann import get_schumann
from astro   import astro_events
from gpt     import gpt_blurb
import radiation                                   # ← NEW

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

# ────────────────── базовые константы ──────────────────
TZ        = pendulum.timezone("Asia/Nicosia")
TODAY     = pendulum.now(TZ).date()
TOMORROW  = TODAY.add(days=1)

TOKEN   = os.getenv("TELEGRAM_TOKEN", "")
CHAT_ID = int(os.getenv("CHANNEL_ID", "0"))
if not TOKEN or CHAT_ID == 0:
    logging.error("Не заданы TELEGRAM_TOKEN и/или CHANNEL_ID")
    exit(1)

CITIES: Dict[str, Tuple[float, float]] = {
    "Nicosia":   (35.170, 33.360),
    "Larnaca":   (34.916, 33.624),
    "Limassol":  (34.707, 33.022),          # базовый город
    "Pafos":     (34.776, 32.424),
    "Troodos":   (34.916, 32.823),
    "Ayia Napa": (34.988, 34.012),
}
COASTAL_CITIES = {"Larnaca", "Limassol", "Pafos", "Ayia Napa"}

WMO_DESC = {
    0: "☀️ ясно", 1: "⛅️ ч.обл", 2: "☁️ обл", 3: "🌥 пасм",
   45: "🌫 туман", 48: "🌫 изморозь", 51: "🌦 морось",
   61: "🌧 дождь", 71: "❄️ снег", 95: "⛈ гроза",
}
code_desc = lambda c: WMO_DESC.get(c, "—")

# ────────── стрелка давления (Δ ≥ 0.3 hPa) ──────────
def pressure_arrow(hourly: Dict[str, Any]) -> str:
    pr = hourly.get("surface_pressure", [])
    if len(pr) >= 2:
        delta = pr[-1] - pr[0]
        if   delta >= 0.3:  return "↑"
        elif delta <= -0.3: return "↓"
    return "→"

# ────────── Шуман ──────────
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
            arr   = json.loads(cache.read_text(encoding="utf-8"))
            pts   = arr[-24:]
            freqs = [p["freq"] for p in pts if isinstance(p.get("freq"), (int, float))]
            trend = "→"
            if len(freqs) > 1:
                avg = sum(freqs[:-1])/(len(freqs)-1)
                d   = freqs[-1] - avg
                trend = "↑" if d >= 0.1 else "↓" if d <= -0.1 else "→"
            last = arr[-1]
            return {"freq": round(last["freq"],2),
                    "amp":  round(last["amp"],1),
                    "trend": trend,
                    "cached": True}
        except Exception:
            pass
    return sch

# ───────────────────────── build_msg ─────────────────────────
def build_msg() -> str:
    P: List[str] = []

    # Заголовок
    P.append(f"<b>🌅 Добрый вечер! Погода на завтра ({TOMORROW.format('DD.MM.YYYY')})</b>")

    # Ср. SST
    sst_vals = [t for c in COASTAL_CITIES if (t:=get_sst(*CITIES[c])) is not None]
    P.append(f"🌊 Ср. темп. моря: {sum(sst_vals)/len(sst_vals):.1f} °C" if sst_vals
             else "🌊 Ср. темп. моря: н/д")
    P.append("———")

    # Прогноз для Limassol
    lat, lon = CITIES["Limassol"]
    day_max, night_min = fetch_tomorrow_temps(lat, lon, tz=TZ.name)
    w   = get_weather(lat, lon) or {}
    cur = w.get("current", {}) or {}

    # ветер в 12-00
    wind_kmh = cur.get("windspeed",0.0)
    wind_deg = cur.get("winddirection",0.0)
    hr   = w.get("hourly",{}) or {}
    tms  = hr.get("time",[])
    ws10 = hr.get("wind_speed_10m",[]) or hr.get("windspeed_10m",[])
    wd10 = hr.get("wind_direction_10m",[]) or hr.get("winddirection_10m",[])
    if tms and ws10 and wd10:
        pref = TOMORROW.format("YYYY-MM-DD")+"T12:"
        for i,t in enumerate(tms):
            if t.startswith(pref):
                try:
                    wind_kmh = float(ws10[i]); wind_deg = float(wd10[i])
                except: ...
                break

    press  = cur.get("pressure",1013)
    clouds = cur.get("clouds",0)
    arrow  = pressure_arrow(hr)
    avg_t  = ((day_max+night_min)/2) if day_max and night_min else cur.get("temperature",0.0)
    P.append(
        f"🌡️ Ср. темп: {avg_t:.0f} °C • {clouds_word(clouds)} "
        f"• 💨 {wind_kmh:.1f} км/ч ({compass(wind_deg)}) • 💧 {press:.0f} гПа {arrow}"
    )
    P.append("———")

    # Рейтинг городов
    temps: Dict[str,Tuple[float,float,int,Optional[float]]] = {}
    for city,(la,lo) in CITIES.items():
        d,n = fetch_tomorrow_temps(la,lo, tz=TZ.name)
        if d is None: continue
        wc  = (get_weather(la,lo) or {}).get("daily",{}).get("weathercode",[])
        wc  = wc[1] if isinstance(wc,list) and len(wc)>1 else 0
        sst = get_sst(la,lo) if city in COASTAL_CITIES else None
        temps[city] = (d, n if n is not None else d, wc, sst)

    if temps:
        P.append("🎖️ <b>Рейтинг городов (д./н. °C, погода, 🌊)</b>")
        medals = ["🥇","🥈","🥉","4️⃣","5️⃣","❄️"]
        for i,(city,(d,n,wc,sst)) in enumerate(sorted(temps.items(),
                                        key=lambda kv:kv[1][0], reverse=True)[:6]):
            line = f"{medals[i]} {city}: {d:.1f}/{n:.1f}, {code_desc(wc)}"
            if sst is not None:
                line += f", 🌊 {sst:.1f}"
            P.append(line)
        P.append("———")

    # Качество воздуха + пыльца
    air = get_air() or {}
    lvl = air.get("lvl","н/д")
    P.append("🏭 <b>Качество воздуха</b>")
    P.append(f"{AIR_EMOJI.get(lvl,'⚪')} {lvl} (AQI {air.get('aqi','н/д')}) | "
             f"PM₂.₅: {pm_color(air.get('pm25'))} | PM₁₀: {pm_color(air.get('pm10'))}")
    if (p:=get_pollen()):
        P.append("🌿 <b>Пыльца</b>")
        P.append(f"Деревья: {p['tree']} | Травы: {p['grass']} | Сорняки: {p['weed']} — риск {p['risk']}")

    # ☢️ Радиация (по координатам Limassol)
    rad = radiation.get_radiation(lat, lon)
    if rad and rad.get("value") is not None:
        P.append(f"☢️ Радиация: {rad['value']:.2f} µSv/h")
    P.append("———")

    # Геомагнитка + Шуман
    kp, ks = get_kp()
    P.append(f"{kp_emoji(kp)} Геомагнитка: Kp={kp:.1f} ({ks})" if kp else "🧲 Геомагнитка: н/д")
    P.append(schumann_line(get_schumann_with_fallback()))
    P.append("———")

    # Астрособытия
    P.append("🌌 <b>Астрособытия</b>")
    astro_lines = astro_events(offset_days=1, show_all_voc=True)
    P.extend(astro_lines if astro_lines else ["— нет данных —"])
    P.append("———")

    # ───── умный «Вывод»  (как в Калининграде) ─────
    culprit: str
    # 1) магнитные бури
    if kp is not None and ks.lower() == "буря":
        culprit = "магнитные бури"
    # 2) жара / похолодание
    elif day_max and day_max >= 30:
        culprit = "жару"
    elif night_min and night_min <= 5:
        culprit = "резкое похолодание"
    else:
        # 3) опасный WMO-код
        d_codes = (w or {}).get("daily",{}).get("weathercode",[])
        t_code  = d_codes[1] if isinstance(d_codes,list) and len(d_codes)>1 else None
        if   t_code == 95: culprit = "гроза"
        elif t_code == 71:  culprit = "снег"
        elif t_code == 48:  culprit = "изморозь"
        else:
            # 4) фаза Луны из astro_lines
            culprit = "неблагоприятный прогноз погоды"
            for line in astro_lines:
                low = line.lower()
                if any(x in low for x in ("новолуние","полнолуние","четверть")):
                    cl = line
                    for ch in ("🌑","🌕","🌓","🌒","🌙"):
                        cl = cl.replace(ch,"")
                    cl = cl.split("(")[0].strip().replace(" ,",",")
                    culprit = f"фазу Луны — {cl[0].upper()+cl[1:]}"
                    break

    P.append("📜 <b>Вывод</b>")
    P.append(f"Если что-то пойдёт не так, вините {culprit}! 😉")
    P.append("———")

    # рекомендации
    P.append("✅ <b>Рекомендации</b>")
    _, tips = gpt_blurb(culprit)
    for tip in tips[:3]:
        P.append(tip.strip())
    P.append("———")

    # факт дня
    P.append(f"📚 {get_fact(TOMORROW)}")
    return "\n".join(P)

# ─────────────── отправка ───────────────
async def send_main_post(bot: Bot) -> None:
    txt = build_msg()
    logging.info("Preview: %s", txt[:200].replace('\n',' | '))
    try:
        await bot.send_message(chat_id=CHAT_ID, text=txt,
                               parse_mode="HTML", disable_web_page_preview=True)
        logging.info("Отправлено ✓")
    except tg_err.TelegramError as e:
        logging.error("Telegram error: %s", e)

async def main() -> None:
    await send_main_post(Bot(token=TOKEN))

if __name__ == "__main__":
    asyncio.run(main())
