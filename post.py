"""
post.py – VayboМетр v3.9  («шоф-брейк»)

◼ Изменения относительно 3.7
─────────────────────────────────────────────────
• HTML-разметка → гарантированный <b>жирный</b> заголовок в Telegram.  
• Облачность переводится в «ясно / переменная / облачно» (по cloud_cover или weathercode).  
• Комментарий-однострочник о погоде (темп + ветер + давление).  
• Резонанс Шумана: 1-й API → резервный; если оба молчат — «вчера было спокойно 7.8 Гц».  
• Астрособытия: фаза Луны ± процент, ретроградность Меркурия, трин Венера–Юпитер,
  мини-парад (≥3 планеты в 90°).  
• Шуточный вывод всегда винит конкретный фактор (ретро, бури, давление, парад).  
• Советы – ровно 3 креативных bullet’а.

GitHub Secrets:  
OPENAI_API_KEY  TELEGRAM_TOKEN  CHANNEL_ID  
OWM_KEY  AIRVISUAL_KEY  TOMORROW_KEY   (остальные необязательны)
"""

from __future__ import annotations
import asyncio, json, math, os, sys
from datetime import datetime, timedelta
from typing import Dict, Any, Optional

import requests, swisseph as swe
from openai import OpenAI
from telegram import Bot, error as tg_err

LAT, LON = 34.707, 33.022   # Limassol

# ────────── small helpers ──────────────────────────────────────────────
def _get(url: str, **params) -> Optional[dict]:
    try:
        r = requests.get(url, params=params, timeout=20)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        print(f"[warn] {url} -> {e}", file=sys.stderr); return None

deg2dir = lambda d: "N NE E SE S SW W NW".split()[int((d + 22.5) % 360 // 45)]
wind_phrase = lambda k: "штиль" if k < 5 else "слабый" if k < 15 else "умеренный" if k < 30 else "сильный"

def clouds_word(percent: int) -> str:
    return "ясно" if percent < 25 else "переменная" if percent < 70 else "облачно"

def aqi_to_pm25(aqi: float) -> float:
    bp=[(0,50,0,12),(51,100,12.1,35.4),(101,150,35.5,55.4),
        (151,200,55.5,150.4),(201,300,150.5,250.4),
        (301,400,250.5,350.4),(401,500,350.5,500.4)]
    for Il,Ih,Cl,Ch in bp:
        if Il <= aqi <= Ih:
            return round((aqi-Il)*(Ch-Cl)/(Ih-Il)+Cl,1)
    return aqi

# ────────── data sources ───────────────────────────────────────────────
def get_weather():
    if (k := os.getenv("OWM_KEY")):
        for ver in ("3.0", "2.5"):
            d=_get(f"https://api.openweathermap.org/data/{ver}/onecall",
                   lat=LAT, lon=LON, appid=k, units="metric", exclude="minutely,hourly,alerts")
            if d and d.get("current"): return d
    # Open-Meteo fallback
    return _get("https://api.open-meteo.com/v1/forecast",
                latitude=LAT, longitude=LON, current_weather=True,
                hourly="cloud_cover,precipitation_probability,weathercode,surface_pressure",
                daily="temperature_2m_max,temperature_2m_min,precipitation_probability_max",
                timezone="UTC")

get_air = (lambda : _get("https://api.airvisual.com/v2/nearest_city", lat=LAT, lon=LON,
                         key=os.getenv("AIRVISUAL_KEY"))
           if os.getenv("AIRVISUAL_KEY") else None)

def pm10_openmeteo() -> Optional[float]:
    d=_get("https://air-quality-api.open-meteo.com/v1/air-quality",
           latitude=LAT,longitude=LON,hourly="pm10",timezone="UTC")
    try: return round(float(d["hourly"]["pm10"][0]),1)
    except Exception: return None

def get_sst():
    d=_get("https://marine-api.open-meteo.com/v1/marine",
           latitude=LAT, longitude=LON,
           hourly="sea_surface_temperature", timezone="UTC")
    try: return round(float(d["hourly"]["sea_surface_temperature"][0]),1)
    except Exception: return None

get_kp = lambda : (lambda arr: float(arr[-1][1]) if arr else None)(
    _get("https://services.swpc.noaa.gov/products/noaa-planetary-k-index.json"))

def get_schumann():
    # node-1
    d=_get("https://api.glcoherence.org/v1/earth")
    if d: return {"freq":d["frequency_1"],"amp":d["amplitude_1"]}
    # node-2
    d=_get("https://gci-api.ucsd.edu/data/latest")
    if d: return {"freq":d["data"]["sr1"]["frequency"],
                  "amp":d["data"]["sr1"]["amplitude"]}
    # yesterday static calm
    return {"prev":"7.8 Гц, спокойно"}

# ────────── astrology ──────────────────────────────────────────────────
def moon_phase(jd: float) -> str:
    sun = swe.calc_ut(jd, swe.SUN)[0][0]
    moon = swe.calc_ut(jd, swe.MOON)[0][0]
    phase = ((moon - sun + 360) % 360) / 360      # 0..1
    illum = round(abs(math.cos(math.pi * phase)) * 100)
    name = ("Новолуние" if illum < 5 else
            "Растущая Луна" if phase < .5 else
            "Полнолуние" if illum > 95 else
            "Убывающая Луна")
    return f"{name} ({illum} %)"

def planet_parade(jd: float) -> Optional[str]:
    bodies=[swe.MERCURY,swe.VENUS,swe.MARS,swe.JUPITER,swe.SATURN]
    lons=sorted(swe.calc_ut(jd,b)[0][0] for b in bodies)
    # минимальный сектор для 3+ планет
    best=min((lons[i+2]-lons[i]) % 360 for i in range(len(lons)-2))
    return "Мини-парад планет" if best < 90 else None

def astro_block() -> str:
    jd=swe.julday(*datetime.utcnow().timetuple()[:3])
    parts=[moon_phase(jd)]
    if (p:=planet_parade(jd)): parts.append(p)
    # трин Венера–Юпитер
    diff=abs((swe.calc_ut(jd,swe.VENUS)[0][0]-swe.calc_ut(jd,swe.JUPITER)[0][0]+180)%360-180)
    if diff < 4: parts.append("Трин Венеры и Юпитера – день удачи")
    if swe.calc_ut(jd,swe.MERCURY)[0][3] < 0: parts.append("Меркурий ретрограден")
    return " | ".join(parts)

# ────────── GPT helper ────────────────────────────────────────────────
def gpt_comment(culprit: str)->tuple[str,str]:
    prompt = (
        "Ты — дерзкий astro-бот Gen Z. Дай:\n"
        "1) Один смешной вывод (1-2 предложения). Вставь фразу: "
        f"«если что-то пойдёт не так — вините {culprit}».\n"
        "2) Три креативных совета-буллета, весёлых, небанальных.")
    rsp=OpenAI(api_key=os.environ["OPENAI_API_KEY"]).chat.completions.create(
        model="gpt-4o-mini",temperature=0.6,
        messages=[{"role":"user","content":prompt}]
    ).choices[0].message.content.strip()
    lines=[l.strip() for l in rsp.splitlines() if l.strip()]
    summary=lines[0]
    tips=[l.lstrip("-• ").strip() for l in lines[1:3+1]]  # 3 шт.
    return summary, "\n".join(f"- {t}" for t in tips)

# ────────── digest builder ────────────────────────────────────────────
def build_md(d: Dict[str,Any]) -> str:
    P=[]

    # ── Погода
    w=d["weather"]; jd_now=datetime.utcnow()
    if "current" in w:          # OWM
        cur,day=w["current"],w["daily"][0]["temp"]
        cloud_txt=clouds_word(cur.get("clouds",0))
        wind_k=cur["wind_speed"]*3.6
        pressure=cur["pressure"]
        P+=["☀️ <b>Погода</b>",
            f"<b>Температура:</b> днём до {day['max']:.0f} °C, ночью около {day['min']:.0f} °C",
            f"<b>Облачность:</b> {cloud_txt}",
            f"<b>Осадки:</b> не ожидаются" if w['daily'][0].get("rain",0)==0 else "<b>Осадки:</b> возможен дождь",
            f"<b>Ветер:</b> {wind_phrase(wind_k)} ({wind_k:.1f} км/ч), {deg2dir(cur['wind_deg'])}",
            f"<b>Давление:</b> {pressure} гПа",
            f"Тёплый {wind_phrase(wind_k)}, давление {pressure} гПа — "
            f"{'ниже' if pressure<1010 else 'выше' if pressure>1020 else 'в пределах'} нормы."]
    else:                       # Open-Meteo
        cw=w["current_weather"]; dm=w["daily"]
        cloud_txt=clouds_word(w["hourly"]["cloud_cover"][0])
        wind=cw["windspeed"]; pressure=w["hourly"]["surface_pressure"][0]
        P+=["☀️ <b>Погода</b>",
            f"<b>Температура:</b> днём до {dm['temperature_2m_max'][0]:.0f} °C, "
            f"ночью около {dm['temperature_2m_min'][0]:.0f} °C",
            f"<b>Облачность:</b> {cloud_txt}",
            f"<b>Осадки:</b> не ожидаются" if dm["precipitation_probability_max"][0] < 20
            else "<b>Осадки:</b> возможен дождь",
            f"<b>Ветер:</b> {wind_phrase(wind)} ({wind:.1f} км/ч), {deg2dir(cw['winddirection'])}",
            f"<b>Давление:</b> {pressure:.0f} гПа",
            f"Лайтовый бриз, давление {pressure:.0f} гПа — чувствуется{' ↓' if pressure<1010 else ' ↑' if pressure>1020 else ''}."]
    # ── Воздух
    if air:=d["air"]:
        pol=air["data"]["current"]["pollution"]
        pm25=pol.get("p2") or aqi_to_pm25(pol["aqius"])
        pm10=pol.get("p1") or d["pm10_fallback"] or "нет данных"
        level=("хороший" if pol["aqius"]<=50 else "умеренный" if pol["aqius"]<=100 else "вредный")
        P+=["","🌬️ <b>Качество воздуха</b>",
            f"<b>AQI:</b> {pol['aqius']}  |  <b>PM2.5:</b> {pm25} µg/m³  |  <b>PM10:</b> {pm10} µg/m³",
            f"Воздух {level}."]
    # ── Геомагнитика
    kp=d["kp"]; state="буря (G1)" if kp and kp>=5 else "спокойный" if kp and kp<4 else "повышенный"
    P+=["","🌌 <b>Геомагнитная активность</b>",
        f"<b>Уровень:</b> {state} (Kp {kp:.1f})" if kp else "нет данных"]

    # ── Schumann
    sch=d["schumann"]
    if sch and "freq" in sch:
        P+=["","📈 <b>Резонанс Шумана</b>",
            f"<b>Частота:</b> ≈{sch['freq']:.1f} Гц • амплитуда стабильна"]
    else:
        P+=["","📈 <b>Резонанс Шумана</b>",
            sch["prev"] if sch else "нет данных"]

    # ── Море
    if d["sst"]:
        P+=["","🌊 <b>Температура воды в море</b>",
            f"<b>Сейчас:</b> {d['sst']} °C"]

    # ── Астроблок
    astro=astro_block()
    if astro:
        P+=["","🔮 <b>Астрологические события</b>", astro]

    P.append("---")

    # culprit
    culprit=("ретроградного Меркурия" if "ретрограден" in astro else
             "магнитных бурь" if kp and kp>=5 else
             "низкого давления" if "↓" in P[6] else
             "мини-парада планет")
    summary,tips=gpt_comment(culprit)
    P+=["<b>📝 Вывод</b>", summary,"","---","<b>✅ Рекомендации</b>", tips]
    return "\n".join(P)

# ────────── Telegram send ──────────────────────────────────────────────
async def send(md: str):
    await Bot(os.environ["TELEGRAM_TOKEN"]).send_message(
        chat_id=os.environ["CHANNEL_ID"],
        text=md[:4096],
        parse_mode="HTML",
        disable_web_page_preview=True)

# ────────── main ───────────────────────────────────────────────────────
async def main():
    data={
        "weather": get_weather(),
        "air": get_air(),
        "pm10_fallback": pm10_openmeteo(),
        "sst": get_sst(),
        "kp": get_kp(),
        "schumann": get_schumann()
    }
    md=build_md(data); print("MD preview:", md[:250].replace("\n"," | "))
    try: await send(md); print("✓ sent")
    except tg_err.TelegramError as e: print("Telegram error:", e, file=sys.stderr); raise

if __name__ == "__main__":
    asyncio.run(main())
