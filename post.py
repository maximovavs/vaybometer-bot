#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
post.py  (rev. 2025-06-01)

• Extra-alerts: туман (WMO 45/48) ⇒ ⚠️;   осадки >50 % ⇒ «Зонт пригодится».
• Culprit выбирается: низкое давление / магнитная буря / туман /
  ретроградный Меркурий / случайное.
• CTA-фраза в конце поста.
"""

from __future__ import annotations
import os, json, asyncio, logging, random, re
from pathlib import Path
from typing import Dict, Any, List, Tuple, Optional

import pendulum, requests
from requests.exceptions import RequestException
from telegram import Bot, error as tg_err

from utils   import compass, clouds_word, get_fact, AIR_EMOJI, pm_color, kp_emoji
from weather import get_weather, fetch_tomorrow_temps
from air     import get_air, get_sst, get_kp
from pollen  import get_pollen
from schumann import get_schumann
from lunar    import get_day_lunar_info
from gpt      import gpt_blurb

TZ          = pendulum.timezone("Asia/Nicosia")
TOMORROW    = pendulum.now(TZ).add(days=1).date()

TOKEN       = os.getenv("TELEGRAM_TOKEN", "")
CHAT_ID     = int(os.getenv("CHANNEL_ID", 0))

CITIES = {
    "Limassol": (34.707, 33.022),
    "Larnaca" : (34.916, 33.624),
    "Nicosia" : (35.170, 33.360),
    "Pafos"   : (34.776, 32.424),
    "Troodos" : (34.916, 32.823),
}

# ───── helpers ──────────────────────────────────────────────
WMO_TEXT = {0:"ясно",1:"част. облач.",2:"облачно",3:"пасмурно",
            45:"туман",48:"туман",51:"морось",61:"дождь",63:"дождь",
            71:"снег",95:"гроза"}
WMO_ICON = {0:"☀️",1:"⛅",2:"☁️",3:"☁️",
            45:"🌫️",48:"🌫️",51:"🌧️",61:"🌧️",63:"🌧️",71:"🌨️",95:"🌩️"}

def code_desc(code:int)->str:
    return f"{WMO_ICON.get(code,'🌡️')} {WMO_TEXT.get(code,'—')}"

def pressure_arrow(hourly:Dict[str,Any])->str:
    pr = hourly.get("surface_pressure", [])
    if len(pr)<2: return "→"
    delta = pr[-1]-pr[0]
    return "↑" if delta>1 else "↓" if delta<-1 else "→"

def schumann_line(s:Dict[str,Any])->str:
    if s.get("freq") is None:
        return "🎵 Шуман: н/д"
    f=s["freq"]; amp=s["amp"]
    emoji="🔴" if f<7.6 else "🟣" if f>8.1 else "🟢"
    return f"{emoji} Шуман: {f:.2f} Гц / {amp:.1f} pT {s['trend']}"

def safe_schumann()->Dict[str,Any]:
    s=get_schumann()
    if s.get("freq") is not None: s.setdefault("trend","→"); return s
    fp=Path(__file__).parent/'schumann_hourly.json'
    if fp.exists():
        arr=json.loads(fp.read_text())
        if arr: last=arr[-1]; return {"freq":round(last["freq"],2),"amp":round(last["amp"],1),"trend":"→"}
    return {}

# ───── Astro-block for tomorrow ────────────────────────────
def astro_block()->List[str]:
    info=get_day_lunar_info(TOMORROW)
    if not info: return []
    out=[]
    voc=info.get("void_of_course",{})
    if voc.get("start") and voc.get("end"):
        t1,t2=pendulum.parse(voc["start"]),pendulum.parse(voc["end"])
        if (t2-t1).in_minutes()>=15:
            out.append(f"⚫️ VoC {t1.format('HH:mm')}–{t2.format('HH:mm')}")
    phase=re.sub(r"\s*\(\d+%.*","",info.get("phase","")).strip()
    if phase: out.append(phase)
    tips=[re.sub(r"^\d+\.\s*","",t).strip() for t in info.get("advice",[])[:3]]
    out.extend(f"• {t}" for t in tips)
    return out

# ───── choose culprit ──────────────────────────────────────
def choose_culprit(press:float, kp_val:float, code:int, retro:bool)->str:
    if press and press<1005:            return "низкое давление"
    if kp_val and kp_val>=4:            return "магнитная буря"
    if code in {45,48}:                 return "туман"
    if retro:                           return "ретроградный Меркурий"
    return random.choice(["влага","циклон","океанский бриз"])

# ───── build message ───────────────────────────────────────
def build_msg()->str:
    P:List[str]=[]
    P.append(f"<b>🌅 Добрый вечер! Погода на завтра ({TOMORROW.format('DD.MM.YYYY')})</b>")

    if (sst:=get_sst()) is not None:
        P.append(f"🌊 Темп. моря: {sst:.1f} °C")

    lim_lat,lim_lon=CITIES["Limassol"]
    t_hi,t_lo=fetch_tomorrow_temps(lim_lat,lim_lon,TZ.name)
    w_lim=get_weather(lim_lat,lim_lon) or {}
    cur=w_lim.get("current",{})
    avg=(t_hi+t_lo)/2 if t_hi and t_lo else cur.get("temperature",0)
    wind,wd=cur.get("windspeed",0),cur.get("winddirection",0)
    clouds,press=cur.get("clouds",0),cur.get("pressure",1013)
    code_lim=(w_lim.get("daily",{}).get("weathercode",[0,0,0])[1] if w_lim else 0)

    P.append(f"🌡️ Ср. темп: {avg:.0f} °C • {clouds_word(clouds)} "
             f"• 💨 {wind:.1f} км/ч ({compass(wd)}) "
             f"• 💧 {press:.0f} гПа {pressure_arrow(w_lim.get('hourly',{}))}")
    P.append("———")

    # рейтинг
    temps={}
    for city,(la,lo) in CITIES.items():
        hi,lo_t=fetch_tomorrow_temps(la,lo,TZ.name)
        if hi is None: continue
        code=(get_weather(la,lo) or {}).get("daily",{}).get("weathercode",[0,0,0])[1]
        temps[city]=(hi,lo_t or hi,code)
    medals=["🥇","🥈","🥉","4️⃣","5️⃣"]
    P.append("🎖️ <b>Рейтинг городов (дн./ночь · погода)</b>")
    for i,(c,(hi,lo_t,code)) in enumerate(sorted(temps.items(),key=lambda kv:kv[1][0],reverse=True)[:5]):
        P.append(f"{medals[i]} {c}: {hi:.1f}/{lo_t:.1f} °C, {code_desc(code)}")
    P.append("———")

    # alerts
    alerts=[]
    if code_lim in {45,48}: alerts.append("⚠️ Предупреждение: возможен туман утром.")
    prob_rain=(w_lim.get("daily",{}).get("precipitation_probability_max",[0,0,0])[1] if w_lim else 0)
    if prob_rain and prob_rain>50: alerts.append("☔ Высока вероятность осадков — зонт пригодится.")
    if alerts: P.extend(alerts+["———"])

    # воздух / пыльца
    air=get_air() or {}; lvl=air.get("lvl","н/д")
    P.append("🏙️ <b>Качество воздуха</b>")
    P.append(f"{AIR_EMOJI.get(lvl,'⚪')} {lvl} (AQI {air.get('aqi','н/д')}) | "
             f"PM₂.₅: {pm_color(air.get('pm25'))} | PM₁₀: {pm_color(air.get('pm10'))}")
    if (pol:=get_pollen()):
        P.append("🌿 <b>Пыльца</b>")
        P.append(f"Деревья: {pol['tree']} | Травы: {pol['grass']} | "
                 f"Сорняки: {pol['weed']} — риск {pol['risk']}")
    P.append("———")

    # space weather
    kp_val,kp_state=get_kp()
    P.append(f"{kp_emoji(kp_val)} Геомагнитка: Kp={kp_val:.1f} ({kp_state})" if kp_val else "🧲 Геомагнитка: н/д")
    P.append(schumann_line(safe_schumann()))
    P.append("———")

    # astro
    block=astro_block()
    if block:
        P.append("🌌 <b>Астрособытия</b>")
        P.extend(block)
        P.append("———")

    # GPT-вывод
    retro=False  # placeholder; замените, если в коде есть проверка ретрограда
    culprit=choose_culprit(press,kp_val,code_lim,retro)
    summary,tips=gpt_blurb(culprit)

    summary=re.sub(r"\bвините\s+погода\b","вините погоду",summary,flags=re.I)
    P.append(f"📜 <b>Вывод</b>\n{summary}")
    P.append("———")

    tips=list(dict.fromkeys(tips)) or ["Берегите себя!"]
    while len(tips)<3: tips.append(random.choice(tips))
    P.append("✅ <b>Рекомендации</b>")
    for t in tips[:3]: P.append(f"• {t}")
    P.append("———")

    # факт + CTA
    P.append(f"📚 {get_fact(TOMORROW)}")
    P.append("\nА вы уже решили, как проведёте вечер? 🌆")

    return "\n".join(P)

# ───── main ────────────────────────────────────────────────
async def main():
    html=build_msg()
    bot=Bot(token=TOKEN)
    try:
        await bot.send_message(CHAT_ID,html,parse_mode="HTML",disable_web_page_preview=True)
        logging.info("sent ✓")
    except tg_err.TelegramError as e:
        logging.error(e)

if __name__=="__main__":
    asyncio.run(main())
