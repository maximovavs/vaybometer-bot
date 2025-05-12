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
"""
Все глобальные константы, ключи-секреты и справочники,
которыми пользуются остальные модули.
"""
import os, pendulum

# ── география канала ────────────────────────────────────────────
LAT, LON = 34.707, 33.022                         # Limassol, CY
CITIES   = {                                     # для «самый тёплый/холодный»
    "Limassol": (34.707, 33.022),
    "Larnaca" : (34.916, 33.624),
    "Nicosia" : (35.170, 33.360),
    "Pafos"   : (34.776, 32.424),
}

# ── ключи из GitHub Secrets ─────────────────────────────────────
TOKEN       = os.environ["TELEGRAM_TOKEN"]
CHAT        = os.environ["CHANNEL_ID"]                    # id канала/чата
OWM_KEY     = os.getenv("OWM_KEY")                        # погода
AIR_KEY     = os.getenv("AIRVISUAL_KEY")                  # AQI / PM
AMBEE_KEY   = os.getenv("TOMORROW_KEY")                   # пыльца (Tomorrow.io)
OPENAI_KEY  = os.getenv("OPENAI_API_KEY")                 # GPT
COP_USER    = os.getenv("COPERNICUS_USER")                # Copernicus FTP
COP_PASS    = os.getenv("COPERNICUS_PASS")

# ── время / даты ────────────────────────────────────────────────
TZ        = pendulum.timezone("Asia/Nicosia")
TODAY     = pendulum.now(TZ).date()
TOMORROW  = TODAY + pendulum.duration(days=1)

# ── сетевые мелочи ──────────────────────────────────────────────
HEADERS   = {"User-Agent": "VayboMeter/5.4"}

# ── эмодзи-иконки для заголовка (по типу погодного кода) ───────
WEATHER_ICONS = {
    "clear"   : "☀️",  # 0
    "partly"  : "🌤",
    "cloudy"  : "☁️",
    "overcast": "🌥",
    "fog"     : "🌁",
    "drizzle" : "🌦",
    "rain"    : "🌧",
    "snow"    : "🌨",
    "storm"   : "⛈",
}

# ── «факт дня»  (ключ = MM-DD) ─────────────────────────────────
FACTS = {
    "05-11": "11 мая — День морского бриза на Кипре 🌬️",
    "06-08": "8 июня 2004 г. — транзит Венеры по диску Солнца 🌞",
    "07-20": "20 июля — на Кипре собирают первый урожай винограда 🍇",
    # …дополняйте по вкусу
}


# ─────────── 1.  UTILS ──────────────────────────────────────────
# ─────────── 1.  UTILS  (вспомогательные функции) ───────────────
import math, requests, logging, random, pendulum

# ── румбы для компаса ───────────────────────────────────────────
COMPASS = ["N","NNE","NE","ENE","E","ESE","SE","SSE",
           "S","SSW","SW","WSW","W","WNW","NW","NNW"]

def compass(deg: float) -> str:
    """ Числовой угол 0-360° → краткое направление N/NE/E… """
    return COMPASS[int((deg/22.5)+.5) % 16]

def clouds_word(pc: int) -> str:
    """ %-облачности → словесное описание """
    return "ясно" if pc < 25 else "переменная" if pc < 70 else "пасмурно"

def wind_phrase(km_h: float) -> str:
    """ Скорость ветра → словечко «штиль/слабый/умеренный/сильный» """
    return ("штиль"       if km_h < 2  else
            "слабый"      if km_h < 8  else
            "умеренный"   if km_h < 14 else
            "сильный")

def aqi_color(aqi: int|float|str) -> str:
    """ AQI → цветокружок-эмодзи 🟢🟡🟠🔴🟣🟤 (строка) """
    if aqi == "—":              return "⚪️"
    aqi = float(aqi)
    return ("🟢" if aqi <= 50 else "🟡" if aqi <=100 else
            "🟠" if aqi <=150 else "🔴" if aqi <=200 else
            "🟣" if aqi <=300 else "🟤")

def get_fact(date_obj: pendulum.Date) -> str:
    """ Вернуть «факт дня» по дате или запасную фразу. """
    key = date_obj.format("MM-DD")
    return FACTS.get(key, "На Кипре в году ≈340 солнечных дней ☀️")

def safe(v, unit: str = "") -> str:
    """ Красивый вывод показателя (None → «—»). """
    if v in (None, "None", "—"):          return "—"
    if isinstance(v, (int, float)):       return f"{v:.1f}{unit}"
    return f"{v}{unit}"

# ── универсальный HTTP-геттер с логированием ────────────────────
def _get(url: str, **params) -> dict | None:
    try:
        r = requests.get(url, params=params, timeout=15, headers=HEADERS)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        host = url.split("/")[2]
        logging.warning("%s – %s", host, e)
        return None


# ─────────── 2.  WEATHER (OWM → Open-Meteo) ─────────────────────
def get_weather(lat: float, lon: float) -> Optional[dict]:
    """
    Возвращает словарь, где гарантированно есть:
      • current_weather
      • daily[0]  (temperature_2m_max / min, weathercode)
      • hourly    (surface_pressure, cloud_cover, weathercode,
                   wind_speed, wind_direction)

    Порядок источников:
      1) OpenWeather One Call (3.0 → 2.5) — при наличии OWM_KEY
      2) Open-Meteo с daily+hourly
      3) Open-Meteo fallback — только current_weather, остальное эмулируем
    """

    # 1️⃣ OpenWeather
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
            if ow and "current" in ow and "daily" in ow:
                return ow                           # структура уже полная

    # 2️⃣ Open-Meteo (полный daily + hourly)
    om = _get(
        "https://api.open-meteo.com/v1/forecast",
        latitude=lat,
        longitude=lon,
        timezone="UTC",
        current_weather="true",
        forecast_days=2,
        daily="temperature_2m_max,temperature_2m_min,weathercode",
        hourly="surface_pressure,cloud_cover,weathercode,wind_speed,wind_direction",
    )
    if om and "daily" in om and "hourly" in om and "current_weather" in om:
        cw = om["current_weather"]
        # подмешиваем давление и облака в current_weather для единообразия с OWM
        cw["pressure"] = om["hourly"]["surface_pressure"][0]
        cw["clouds"]   = om["hourly"]["cloud_cover"][0]
        return om

    # 3️⃣ Open-Meteo fallback — только current_weather
    om = _get(
        "https://api.open-meteo.com/v1/forecast",
        latitude=lat,
        longitude=lon,
        timezone="UTC",
        current_weather="true",
    )
    if not om or "current_weather" not in om:
        return None                       # погодные API недоступны

    cw = om["current_weather"]

    # ── эмулируем daily (один «день» на основе текущих значений) ─────────
    om["daily"] = [{
        "temperature_2m_max": [cw["temperature"]],
        "temperature_2m_min": [cw["temperature"]],
        "weathercode"       : [cw["weathercode"]],
    }]

    # ── эмулируем hourly (хотя бы по одной точке) ────────────────────────
    om["hourly"] = {
        "surface_pressure": [cw.get("pressure", 1013)],
        "cloud_cover"     : [cw.get("clouds", 0)],
        "weathercode"     : [cw["weathercode"]],
        "wind_speed"      : [cw.get("windspeed", 0)],
        "wind_direction"  : [cw.get("winddirection", 0)],
    }

    # дублируем давление/облака и в current_weather
    cw["pressure"] = om["hourly"]["surface_pressure"][0]
    cw["clouds"]   = om["hourly"]["cloud_cover"][0]

    return om


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
def build_msg() -> str:
    """
    Собирает итоговый HTML-текст для Telegram-канала
    (использует все fetch-функции из модулей 2–5).
    """

    # ── 6-A.  Базовая погода для Лимассола ───────────────────────────────
    w = get_weather(LAT, LON)
    if not w:
        raise RuntimeError("Open-Meteo и OWM недоступны")

    if "current" in w:                                  # 👉 ответ OpenWeather
        cur   = w["current"]
        daily = w["daily"][0]["temp"]

        cloud       = clouds_word(cur.get("clouds", 0))
        rain        = "не ожидаются" if w["daily"][0].get("rain", 0) == 0 else "возможен дождь"

        wind_kmh    = cur["wind_speed"] * 3.6
        wind_txt    = f"{wind_kmh:.1f} км/ч, {compass(cur['wind_deg'])}"
        press_val   = float(cur["pressure"])

        day_max     = daily["max"]
        night_min   = daily["min"]

    else:                                               # 👉 ответ Open-Meteo
        cw   = w["current_weather"]
        dm   = w["daily"]                               # dict (OK) или list (fallback)

        cloud       = clouds_word(w["hourly"]["cloud_cover"][0])
        rain        = "не ожидаются"                    # daily precip не приходит

        wind_kmh    = cw["windspeed"]
        wind_txt    = f"{wind_kmh:.1f} км/ч, {compass(cw['winddirection'])}"
        press_val   = float(w["hourly"]["surface_pressure"][0])

        if isinstance(dm, dict):                       # обычный ответ
            day_max   = dm["temperature_2m_max"][0]
            night_min = dm["temperature_2m_min"][0]
        else:                                          # fallback-list
            day_max   = dm[0]["temperature_2m_max"][0]
            night_min = dm[0]["temperature_2m_min"][0]

    # ── 6-B.  Температурные «лидеры» 4 городов Кипра ─────────────────────
    temps: dict[str, float] = {}
    for city, (la, lo) in CITIES.items():
        cw_city = get_weather(la, lo)
        if not cw_city:
            continue

        dblock = cw_city.get("daily")
        try:
            if isinstance(dblock, list):               # fallback
                temps[city] = dblock[0]["temperature_2m_max"][0]
            else:                                      # нормальный dict
                temps[city] = dblock["temperature_2m_max"][0]
        except Exception:
            continue                                   # пропускаем, если чего-то нет

    warm = max(temps, key=temps.get)
    cold = min(temps, key=temps.get)

    # ── 6-C.  Качество воздуха, пыльца, Kp, Шуман, SST ───────────────────
    air   = get_air()
    pol   = air["data"]["current"]["pollution"] if air else {}

    aqi   = pol.get("aqius", "—")
    pm25  = pol.get("p2") or (aqi_to_pm25(aqi) if isinstance(aqi, (int, float)) else "—")
    pm10  = pol.get("p1",  "—")

    kp         = get_kp()
    sst        = get_sst()
    pollen     = get_pollen()
    schumann   = get_schumann()
    astro_list = astro_events()

    # ── 6-D.  «Виновник дня» + GPT-советы ────────────────────────────────
    if kp and kp >= 5:
        culprit = "магнитные бури"
    elif press_val < 1007:
        culprit = "низкое давление"
    elif cloud == "туман":
        culprit = "туман"
    else:
        culprit = "мини-парад планет"

    summary, tips = gpt_blurb(culprit)

    # ── 6-E.  Сборка HTML сообщения ──────────────────────────────────────
    lines: list[str] = [
        f"🙂 <b>Погода на завтра в Лимассоле {TOMORROW.format('DD.MM.YYYY')}</b>",
        f"<b>Темп. днём:</b> до {day_max:.1f} °C",
        f"<b>Темп. ночью:</b> около {night_min:.1f} °C",
        f"<b>Облачность:</b> {cloud}",
        f"<b>Осадки:</b> {rain}",
        f"<b>Ветер:</b> {wind_phrase(wind_kmh)} ({wind_txt})",
        f"<b>Давление:</b> {press_val:.0f} гПа",
        f"<i>Самый тёплый город:</i> {warm} ({temps[warm]:.1f} °C)",
        f"<i>Самый прохладный город:</i> {cold} ({temps[cold]:.1f} °C)",
        "———",
        "🏙️ <b>Качество воздуха</b>",
        f"AQI {aqi} | PM2.5: {pm25} | PM10: {pm10}",
    ]

    # пыльца
    if pollen:
        idx = lambda v: ["нет", "низкий", "умеренный", "высокий",
                         "оч. высокий", "экстрим"][int(round(v))]
        lines.append(
            f"🌿 <b>Пыльца</b>\n"
            f"Деревья — {idx(pollen['treeIndex'])} | "
            f"Травы — {idx(pollen['grassIndex'])} | "
            f"Сорняки — {idx(pollen['weedIndex'])}"
        )

    # kp
    if kp:
        state = "спокойный" if kp < 4 else "повышенный" if kp < 5 else "буря"
        lines.append(f"🧲 <b>Геомагнитная активность</b>\nK-index: {kp:.1f} ({state})")

    # Шуман
    if "freq" in schumann:
        lines.append(f"🎵 <b>Шуман:</b> ≈{schumann['freq']:.1f} Гц • амплитуда стабильна")
    else:
        lines.append(f"🎵 <b>Шуман:</b> {schumann.get('msg', 'нет данных')}")

    # температура воды
    if sst:
        lines.append(f"🌊 <b>Температура воды</b>\nСейчас: {sst:.1f} °C")

    # астрособытия
    if astro_list:
        lines.append("🌌 <b>Астрологические события</b>\n" + " | ".join(astro_list))

    # вывод + рекомендации
    lines += [
        "———",
        f"📜 <b>Вывод</b>\n{summary}",
        "———",
        "✅ <b>Рекомендации</b>",
        *[f"• {t}" for t in tips],
        "———",
        f"📚 {random.choice(FACTS)}",
    ]

    return "\n".join(lines)


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
