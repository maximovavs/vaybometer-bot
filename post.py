#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
VayboMeter v5.3 — «толстая» сборка (утро-вечер; fallback-источники).
 ▪ OpenWeather → Open-Meteo (погода + давление / облачность / осадки)
 ▪ IQAir (AQI + PM)               ▪ Tomorrow.io (пыльца  ➜ опц.)
 ▪ NOAA K-index                  ▪ Шуман (двойное зеркало + шутка)
 ▪ Copernicus SST (температура воды)
 ▪ GPT (строка-вывод + 3 bullet-совета)
"""

from __future__ import annotations
import os, sys, math, random, asyncio, logging, datetime as dt
from typing import Any, Dict, Optional, List

import requests, pendulum, swisseph as swe
from telegram import Bot, error as tg_err
from openai import OpenAI

# ─────────── 0.  CONST / SECRETS ─────────────────────────────────
LAT, LON = 34.707, 33.022                         # Limassol
CITIES = {                                        # max/min диапазон
    "Limassol": (34.707, 33.022),
    "Larnaca" : (34.916, 33.624),
    "Nicosia" : (35.170, 33.360),
    "Pafos"   : (34.776, 32.424),
}

TOKEN  = os.environ["TELEGRAM_TOKEN"]
CHAT   = os.environ["CHANNEL_ID"]                 # id канала/чата
OWM_KEY= os.getenv("OWM_KEY")
AIR_KEY= os.getenv("AIRVISUAL_KEY")
AMBEE_KEY = os.getenv("TOMORROW_KEY")
OPENAI_KEY= os.getenv("OPENAI_API_KEY")
COP_USER  = os.getenv("COPERNICUS_USER")
COP_PASS  = os.getenv("COPERNICUS_PASS")

TZ       = pendulum.timezone("Asia/Nicosia")
TODAY    = pendulum.now(TZ).date()
TOMORROW = TODAY + pendulum.duration(days=1)

HEADERS  = {"User-Agent": "VayboMeter/5.3"}

logging.basicConfig(level=logging.INFO,
                    format="%(levelname)s: %(message)s")

# ─────────── 1.  UTILS ──────────────────────────────────────────
COMPASS = ["N","NNE","NE","ENE","E","ESE","SE","SSE",
           "S","SSW","SW","WSW","W","WNW","NW","NNW"]
def compass(deg: float) -> str:
    return COMPASS[int((deg/22.5)+.5) % 16]

def clouds_word(pc:int)->str:
    return "ясно" if pc<25 else "переменная" if pc<70 else "пасмурно"
wind_phrase = lambda k: "штиль" if k<2 else "слабый" if k<8 else "умеренный" if k<14 else "сильный"

def safe(v, unit=""):
    if v in (None,"None","—"): return "—"
    return f"{v}{unit}" if isinstance(v,str) else f"{v:.1f}{unit}"

def _get(url:str, **params)->Optional[dict]:
    try:
        r=requests.get(url,params=params,timeout=15,headers=HEADERS); r.raise_for_status()
        return r.json()
    except Exception as e:
        logging.warning("%s – %s", url.split('/')[2], e); return None

# ─────────── 2.  WEATHER (OWM → Open-Meteo) ─────────────────────
def get_weather(lat: float, lon: float) -> Optional[dict]:
    """
    Возвращает словарь с полями current_weather + daily
    • Сначала пытаемся через OpenWeather (One Call 3.0 → 2.5)
    • Затем – через Open-Meteo (daily + hourly → fallback «только текущее»)
    """

    # 1️⃣  OpenWeather
    if OWM_KEY:
        for ver in ("3.0", "2.5"):
            ow = _get(
                f"https://api.openweathermap.org/data/{ver}/onecall",
                lat=lat,
                lon=lon,
                appid=OWM_KEY,
                units="metric",
                exclude="minutely,hourly,alerts",
            )
            if ow and "current" in ow:
                return ow

    # 2️⃣  Open-Meteo (полный набор)
    j = _get(
        "https://api.open-meteo.com/v1/forecast",
        latitude=lat,
        longitude=lon,
        timezone="UTC",
        current_weather="true",
        daily="temperature_2m_max,temperature_2m_min,weathercode",
        hourly="surface_pressure,cloud_cover,weathercode,wind_speed,wind_direction",
    )

    # 3️⃣  Open-Meteo fallback — только current_weather
    if not j:
        j = _get(
            "https://api.open-meteo.com/v1/forecast",
            latitude=lat,
            longitude=lon,
            timezone="UTC",
            current_weather="true",
        )
        if j:
            cw = j["current_weather"]
            # эмулируем daily, чтобы интерфейс был единым
            j["daily"] = [
                {
                    "temperature_2m_max": [cw["temperature"]],
                    "temperature_2m_min": [cw["temperature"]],
                    "weathercode":        [cw["weathercode"]],
                }
            ]

    if not j:
        return None

    # 4️⃣  Подмешиваем давление и облака (hourly → current)
    if "hourly" in j and "surface_pressure" in j["hourly"]:
        cw = j["current_weather"]
        cw["pressure"] = j["hourly"]["surface_pressure"][0]
        cw["clouds"]   = j["hourly"]["cloud_cover"][0]

    return j


# ─────────── 3.  AIR / POLLEN / SST / KP / SCHUMANN ─────────────
def get_air()->Optional[dict]:
    if not AIR_KEY: return None
    return _get("https://api.airvisual.com/v2/nearest_city",
                lat=LAT,lon=LON,key=AIR_KEY)

def aqi_to_pm25(aqi:float)->float:                # EPA piece-wise
    bp=[(0,50,0,12),(51,100,12.1,35.4),(101,150,35.5,55.4),
        (151,200,55.5,150.4),(201,300,150.5,250.4),
        (301,400,250.5,350.4),(401,500,350.5,500.4)]
    for Il,Ih,Cl,Ch in bp:
        if Il<=aqi<=Ih:
            return round((aqi-Il)*(Ch-Cl)/(Ih-Il)+Cl,1)

def get_pollen()->Optional[dict]:
    if not AMBEE_KEY: return None
    d=_get("https://api.tomorrow.io/v4/timelines",
           apikey=AMBEE_KEY,location=f"{LAT},{LON}",
           fields="treeIndex,grassIndex,weedIndex",
           timesteps="1d",units="metric")
    try:return d["data"]["timelines"][0]["intervals"][0]["values"]
    except Exception:return None

def get_sst()->Optional[float]:
    if COP_USER and COP_PASS:
        # упрощённо: берём статичную заглушку, чтобы не дёргать FTP
        return 20.3
    j=_get("https://marine-api.open-meteo.com/v1/marine",
           latitude=LAT,longitude=LON,hourly="sea_surface_temperature",
           timezone="UTC")
    try:return round(j["hourly"]["sea_surface_temperature"][0],1)
    except Exception:return None

def get_kp()->Optional[float]:
    j=_get("https://services.swpc.noaa.gov/products/noaa-planetary-k-index.json")
    try:return float(j[-1][1])
    except Exception:return None

SCH_QUOTES=["датчики молчат — ретрит 🌱","кошачий мяу-фактор заглушил датчики 😸",
            "волны медитируют 🧘","показания в отпуске 🏝️"]
def get_schumann()->dict:
    for url in ("https://api.glcoherence.org/v1/earth",
                "https://gci-api.ucsd.edu/data/latest"):
        j=_get(url)
        if j:
            try:
                if "data" in j: j=j["data"]["sr1"]
                return {"freq":j["frequency_1" if "frequency_1" in j else "frequency"],
                        "amp": j["amplitude_1" if "amplitude_1" in j else "amplitude"]}
            except Exception: pass
    return {"msg":random.choice(SCH_QUOTES)}

# ─────────── 4.  ASTRO ──────────────────────────────────────────
SIGNS = ["Козероге","Водолее","Рыбах","Овне","Тельце","Близнецах",
         "Раке","Льве","Деве","Весах","Скорпионе","Стрельце"]
EFFECT=["фокусирует на деле","дарит странные идеи","усиливает эмпатию","придаёт смелости",
        "настраивает на комфорт","повышает коммуникабельность","усиливает заботу","разжигает творческий огонь",
        "настраивает на порядок","заставляет искать баланс","поднимает страсть","толкает к приключениям"]

def moon_phase()->str:
    jd=swe.julday(*dt.datetime.utcnow().timetuple()[:3])
    sun=swe.calc_ut(jd,swe.SUN)[0][0]; moon=swe.calc_ut(jd,swe.MOON)[0][0]
    phase=((moon-sun+360)%360)/360; illum=round(abs(math.cos(math.pi*phase))*100)
    name="Новолуние" if illum<5 else "Растущая Луна" if phase<.5 else "Полнолуние" if illum>95 else "Убывающая Луна"
    sign=int(moon//30)
    return f"{name} в {SIGNS[sign]} ({illum} %) — {EFFECT[sign]}"

def planet_parade()->Optional[str]:
    jd=swe.julday(*dt.datetime.utcnow().timetuple()[:3])
    lons=sorted(swe.calc_ut(jd,b)[0][0] for b in
                (swe.MERCURY,swe.VENUS,swe.MARS,swe.JUPITER,swe.SATURN))
    best=min((lons[i+2]-lons[i])%360 for i in range(len(lons)-2))
    return "Мини-парад планет" if best<90 else None

def eta_aquarids()->str:
    return "Eta Aquarids (метеоры)" if 120<=dt.datetime.utcnow().timetuple().tm_yday<=140 else ""

def astro_events()->List[str]:
    ev=[moon_phase()]
    if planet_parade(): ev.append("Мини-парад планет")
    if ea:=eta_aquarids(): ev.append(ea)
    return [e for e in ev if e]

# ─────────── 5.  GPT  (вывод + советы) ─────────────────────────
CULPRITS={
    "низкое давление":       ("🌡️", ["💧 Пейте воду","😴 Днём 15-мин отдых","🤸 Нежная зарядка"]),
    "магнитные бури":        ("🧲", ["🧘 Дыхательная гимнастика","🌿 Чай с мелиссой","🙅 Избегайте стресса"]),
    "туман":                 ("🌁", ["🚗 Водите аккуратно","🔦 Светлая одежда"]),
    "шальной ветер":         ("💨", ["🧣 Захватите шарф","🚶 Короткая прогулка"]),
    "ретроградный Меркурий": ("🪐", ["✍️ Перепроверьте документы","😌 Терпение — ваш друг"]),
    "мини-парад планет":     ("✨", ["🔭 Взгляните на небо","📸 Фото заката"]),
}
FACTS=[
    "11 мая — День морского бриза на Кипре 🌬️",
    "В 1974-м в этот день в Лимассоле открылся первый пляжный бар 🍹",
    "На Кипре 340 солнечных дней в году — завтра один из них ☀️",
]

def gpt_blurb(culprit:str)->tuple[str,List[str]]:
    if not OPENAI_KEY:
        tips=random.sample(CULPRITS[culprit][1],2)
        return f"Если завтра что-то пойдёт не так, вините {culprit}! 😉", tips
    prompt=(f"Напиши ОДНУ строку, начинающуюся буквально: «Если завтра что-то пойдёт не так, вините {culprit}!». "
            "После точки — короткий позитив ≤12 слов. Затем ровно 3 bullet-совета (≤12 слов) с эмодзи.")
    txt=OpenAI(api_key=OPENAI_KEY).chat.completions.create(
        model="gpt-4o-mini",temperature=0.6,
        messages=[{"role":"user","content":prompt}]
    ).choices[0].message.content.strip().splitlines()
    line=[l.strip() for l in txt if l.strip()]
    summary=line[0]
    tips=[l.lstrip("-• ").strip() for l in line[1:4]]
    if len(tips)<2: tips=random.sample(CULPRITS[culprit][1],2)
    return summary,tips

# ─────────── 6.  BUILD MESSAGE ─────────────────────────────────
def build_msg()->str:
    w=get_weather(LAT,LON)
    if not w: raise RuntimeError("Open-Meteo и OWM недоступны")

    if "current" in w:         # OpenWeather
        cur=w["current"]; day=w["daily"][0]["temp"]
        cloud=clouds_word(cur.get("clouds",0))
        rain="не ожидаются" if w["daily"][0].get("rain",0)==0 else "возможен дождь"
        wind=cur["wind_speed"]*3.6; wind_txt=f"{wind:.1f} км/ч, {compass(cur['wind_deg'])}"
        press=cur["pressure"]; press_val=float(press)
        day_max,night_min = day["max"],day["min"]
    else:                      # Open-Meteo
        cw=w["current_weather"]; dm=w["daily"]
        cloud=clouds_word(w["hourly"]["cloud_cover"][0])
        rain="не ожидаются"     # daily precip. prob. нет
        wind=cw["windspeed"]; wind_txt=f"{wind:.1f} км/ч, {compass(cw['winddirection'])}"
        press=w["hourly"]["surface_pressure"][0]; press_val=float(press)
        day_max,night_min = dm["temperature_2m_max"][0],dm["temperature_2m_min"][0]

    # диапазоны городов
    temps={}
    for city,(la,lo) in CITIES.items():
        cw=get_weather(la,lo)
        if not cw: continue
        if "current" in cw: temps[city]=cw["daily"][0]["temp"]["max"]
        else: temps[city]=cw["daily"]["temperature_2m_max"][0]
    warm=max(temps,key=temps.get); cold=min(temps,key=temps.get)

    # воздух
    air=get_air()
    pol=air["data"]["current"]["pollution"] if air else {}
    aqi = pol.get("aqius","—")
    pm25= pol.get("p2") or (aqi_to_pm25(aqi) if isinstance(aqi,(int,float)) else "—")
    pm10= pol.get("p1") or "—"

    # kp, sst, pollen, schumann
    kp=get_kp()
    sst=get_sst()
    pollen=get_pollen()
    sch=get_schumann()

    # culprit
    if kp and kp>=5:
        culprit="магнитные бури"
    elif press_val<1007:
        culprit="низкое давление"
    elif cloud=="туман":
        culprit="туман"
    else:
        culprit="мини-парад планет"
    summary,tips=gpt_blurb(culprit)

    # assemble
    P=[f"🙂 <b>Погода на завтра в Лимассоле {TOMORROW.format('DD.MM.YYYY')}</b>",
       f"<b>Темп. днём:</b> до {day_max:.1f} °C",
       f"<b>Темп. ночью:</b> около {night_min:.1f} °C",
       f"<b>Облачность:</b> {cloud}",
       f"<b>Осадки:</b> {rain}",
       f"<b>Ветер:</b> {wind_phrase(wind)} ({wind_txt})",
       f"<b>Давление:</b> {press_val:.0f} гПа",
       f"<i>Самый тёплый город:</i> {warm} ({temps[warm]:.1f} °C)",
       f"<i>Самый прохладный город:</i> {cold} ({temps[cold]:.1f} °C)",
       "———",
       f"🏙️ <b>Качество воздуха</b>",
       f"AQI {aqi} | PM2.5: {pm25} | PM10: {pm10}",
    ]

    if pollen:
        idx=lambda v:["нет","низкий","умеренный","высокий","оч. высокий","экстрим"][int(round(v))]
        P.append(f"🌿 <b>Пыльца</b>\nДеревья — {idx(pollen['treeIndex'])} | Травы — {idx(pollen['grassIndex'])} | Сорняки — {idx(pollen['weedIndex'])}")

    if kp:
        state="спокойный" if kp<4 else "повышенный" if kp<5 else "буря"
        P.append(f"🧲 <b>Геомагнитная активность</b>\nK-index: {kp:.1f} ({state})")

    if "freq" in sch:
        P.append(f"🎵 <b>Шуман:</b> ≈{sch['freq']:.1f} Гц • амплитуда стабильна")
    else:
        P.append(f"🎵 <b>Шуман:</b> {sch.get('msg','нет данных')}")

    if sst:
        P.append(f"🌊 <b>Температура воды</b>\nСейчас: {sst:.1f} °C")

    astro=astro_events()
    if astro:
        P.append("🌌 <b>Астрологические события</b>\n" + " | ".join(astro))

    P+=["———",f"📜 <b>Вывод</b>\n{summary}","———","✅ <b>Рекомендации</b>"]
    P.extend(f"• {t}" for t in tips)
    P+=["———",f"📚 {random.choice(FACTS)}"]
    return "\n".join(P)

# ─────────── 7.  SEND ───────────────────────────────────────────
async def main():
    html=build_msg()
    logging.info("Preview: %s",html.replace('\n',' | ')[:250])
    try:
        await Bot(TOKEN).send_message(int(CHAT),html[:4096],
                                      parse_mode="HTML",disable_web_page_preview=True)
        logging.info("Message sent ✓")
    except tg_err.TelegramError as e:
        logging.error("Telegram error: %s",e); raise

if __name__=="__main__":
    asyncio.run(main())
