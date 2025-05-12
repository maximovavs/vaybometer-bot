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


# ─────────── 1. UTILS ──────────────────────────────────────────
import os
import logging
import requests
import math
import random
import pendulum
from typing import Union

# компас
COMPASS = ["N","NNE","NE","ENE","E","ESE","SE","SSE",
           "S","SSW","SW","WSW","W","WNW","NW","NNW"]

def compass(deg: float) -> str:
    return COMPASS[int((deg/22.5)+.5) % 16]

def clouds_word(pc: int) -> str:
    return "ясно" if pc < 25 else "переменная" if pc < 70 else "пасмурно"

def wind_phrase(km_h: float) -> str:
    return ("штиль" if km_h < 2 else
            "слабый" if km_h < 8 else
            "умеренный" if km_h < 14 else
            "сильный")

# факты
FACTS_BY_DATE = {
    "05-11": "11 мая — День морского бриза на Кипре 🌬️",
    "06-08": "8 июня 2004 г. — транзит Венеры по диску Солнца 🌞",
    "07-20": "20 июля — на Кипре собирают первый урожай винограда 🍇",
}
RANDOM_FACTS = list(FACTS_BY_DATE.values())

def get_fact(date_obj: pendulum.Date) -> str:
    key = date_obj.format("MM-DD")
    return FACTS_BY_DATE.get(key, random.choice(RANDOM_FACTS))

def safe(v: Union[None,str,int,float], unit: str = "") -> str:
    if v in (None, "None", "—"):
        return "—"
    if isinstance(v, (int, float)):
        return f"{v:.1f}{unit}"
    return f"{v}{unit}"

def _get(url: str, **params) -> dict | None:
    try:
        r = requests.get(url, params=params, timeout=15, headers={"User-Agent":"VayboMeter"})
        r.raise_for_status()
        return r.json()
    except Exception as e:
        host = url.split("/")[2]
        logging.warning("%s – %s", host, e)
        return None


# ─────────── 2.  WEATHER (OWM → Open-Meteo) ─────────────────────
"""
Возвращает структурированный словарь прогноза + два булевых флага:
    • strong_wind – средняя скорость ветра > 30 км/ч
    • fog_alert   – погодный код 45/48 (туман)
В остальных частях скрипта поля используются так же, как раньше.
"""
from typing import Optional, Dict, Any

# OpenWeather → Open-Meteo → Fallback-эмуляция
def get_weather(lat: float, lon: float) -> Optional[Dict[str, Any]]:
    # 1️⃣ ─── OpenWeather One Call (нужен OWM_KEY) ────────────────
    if OWM_KEY:
        for ver in ("3.0", "2.5"):
            ow = _get(
                f"https://api.openweathermap.org/data/{ver}/onecall",
                lat=lat, lon=lon, appid=OWM_KEY,
                units="metric", exclude="minutely,hourly,alerts",
            )
            if ow and "current" in ow:
                # ▸ унифицируем структуру под open-meteo — добавляем hourly-оболочку
                cur = ow["current"]
                ow["hourly"] = {
                    "surface_pressure": [cur.get("pressure", 1013)],
                    "cloud_cover":      [cur.get("clouds",   0   )],
                    "weathercode":      [cur.get("weather", [{}])[0].get("id", 0)],
                    "wind_speed":       [cur.get("wind_speed", 0)],
                    "wind_direction":   [cur.get("wind_deg",   0)],
                }
                # ▸ вычисляем флаги
                speed_kmh  = cur.get("wind_speed", 0) * 3.6          # м/с → км/ч
                ow["strong_wind"] = speed_kmh > 30
                ow["fog_alert"]   = False                            # коды OWM ≠ open-meteo
                return ow

    # 2️⃣ ─── Open-Meteo (полный daily + hourly) ──────────────────
    om = _get(
        "https://api.open-meteo.com/v1/forecast",
        latitude=lat, longitude=lon, timezone="UTC",
        current_weather="true", forecast_days=2,
        daily="temperature_2m_max,temperature_2m_min,weathercode",
        hourly="surface_pressure,cloud_cover,weathercode,wind_speed,wind_direction",
    )
    if om and "current_weather" in om and "daily" in om:
        cur = om["current_weather"]
        # ▸ подмешиваем давление/облака в current (для унификации интерфейса)
        cur["pressure"] = om["hourly"]["surface_pressure"][0]
        cur["clouds"]   = om["hourly"]["cloud_cover"][0]

        # ▸ вычисляем флаги
        speed_kmh       = cur.get("windspeed", 0)
        wcode_day       = om["daily"]["weathercode"][0]
        om["strong_wind"] = speed_kmh > 30
        om["fog_alert"]   = wcode_day in (45, 48)
        return om

    # 3️⃣ ─── Open-Meteo fallback  («только current_weather») ─────
    om = _get(
        "https://api.open-meteo.com/v1/forecast",
        latitude=lat, longitude=lon,
        timezone="UTC", current_weather="true",
    )
    if not om or "current_weather" not in om:
        return None                                            # вообще нет данных

    cw = om["current_weather"]

    # ── эмулируем daily/hourly, чтобы остальной код не ломался ──
    om["daily"] = [{
        "temperature_2m_max": [cw["temperature"]],
        "temperature_2m_min": [cw["temperature"]],
        "weathercode":        [cw["weathercode"]],
    }]
    om["hourly"] = {
        "surface_pressure": [cw.get("pressure", 1013)],
        "cloud_cover":      [cw.get("clouds",   0   )],
        "weathercode":      [cw["weathercode"]],
        "wind_speed":       [cw.get("windspeed", 0)],
        "wind_direction":   [cw.get("winddirection", 0)],
    }

    # ▸ флаги
    speed_kmh          = cw.get("windspeed", 0)
    om["strong_wind"]  = speed_kmh > 30
    om["fog_alert"]    = cw["weathercode"] in (45, 48)
    return om

# ─────────── 3-A.  AIR / POLLEN / SST / KP  ──────────────────────
# ─────────── 3-A. AIR / POLLEN / SST / KP ───────────────────────

# шкала AQI по US-EPA
AQI_BANDS = (
    (0,  50,  "хороший"),
    (51, 100, "умеренный"),
    (101,150, "вредный для чувствительных"),
    (151,200, "вредный"),
    (201,300, "оч. вредный"),
    (301,500, "опасный"),
)

def aqi_color(aqi: Union[int,float,None]) -> str:
    if aqi is None or aqi == "—":
        return "⚪️"
    for lo, hi, name in AQI_BANDS:
        if lo <= aqi <= hi:
            em = ("🟢" if lo==0 else
                  "🟡" if lo==51 else
                  "🟠" if lo==101 else
                  "🔴" if lo==151 else
                  "🟣" if lo==201 else
                  "🟤")
            return f"{em} {name}"
    return f"⚫ опасный"

def get_air() -> dict | None:
    from .utils import _get  # или прямой импорт
    if not AIR_KEY:
        return None
    j = _get("https://api.airvisual.com/v2/nearest_city",
             lat=LAT, lon=LON, key=AIR_KEY)
    if not j:
        return None

    pol = j["data"]["current"]["pollution"]
    aqi = pol.get("aqius")
    pm25 = pol.get("p2")
    pm10 = pol.get("p1")
    lvl = aqi_color(aqi)

    return {"aqi": aqi, "lvl": lvl, "pm25": pm25, "pm10": pm10}

def get_pollen() -> dict | None:
    if not AMBEE_KEY:
        return None
    d = _get("https://api.tomorrow.io/v4/timelines",
             apikey=AMBEE_KEY,
             location=f"{LAT},{LON}",
             fields="treeIndex,grassIndex,weedIndex",
             timesteps="1d", units="metric")
    try:
        return d["data"]["timelines"][0]["intervals"][0]["values"]
    except:
        return None

def get_sst() -> float | None:
    j = _get("https://marine-api.open-meteo.com/v1/marine",
             latitude=LAT, longitude=LON,
             hourly="sea_surface_temperature",
             timezone="UTC")
    try:
        return round(j["hourly"]["sea_surface_temperature"][0], 1)
    except:
        return None

def get_kp() -> tuple[float|None,str]:
    j = _get("https://services.swpc.noaa.gov/products/noaa-planetary-k-index.json")
    try:
        v = float(j[-1][1])
    except:
        return None, "н/д"
    state = "спокойный" if v < 4 else "повышенный" if v < 5 else "буря"
    return v, state



# ─────────── 3-B.  SCHUMANN  ─────────────────────────────────────
"""
📌 Изменения
• SCH_QUOTES расширен до 7 вариантов.
• Если частота > 8 Гц ⇒ добавляем флаг `"high": True`
"""

SCH_QUOTES = [
    "датчики молчат — ретрит 🌱",
    "кошачий мяу-фактор заглушил сенсоры 😸",
    "волны ушли ловить чаек 🐦",
    "показания медитируют 🧘",
    "данные в отпуске 🏝️",
    "Шуман спит — не будим 🔕",
    "тишина в эфире… 🎧",
]

def get_schumann() -> dict:
    for url in (
        "https://api.glcoherence.org/v1/earth",
        "https://gci-api.ucsd.edu/data/latest",
    ):
        j = _get(url)
        if j:
            try:
                if "data" in j:                     # второй энд-поинт
                    j = j["data"]["sr1"]
                freq = j.get("frequency_1") or j.get("frequency")
                amp  = j.get("amplitude_1")  or j.get("amplitude")
                return {
                    "freq": freq,
                    "amp": amp,
                    "high": freq is not None and freq > 8,  # ⚡️ high-vibe
                }
            except Exception:
                pass

    # оба источника упали → шуточная заглушка
    return {"msg": random.choice(SCH_QUOTES)}


# ─────────── 4. ASTRO ────────────────────────────────────────────
SIGNS = ["Козероге","Водолее","Рыбах","Овне","Тельце","Близнецах",
         "Раке","Льве","Деве","Весах","Скорпионе","Стрельце"]
EFFECT = ["фокусирует на деле","дарит странные идеи","усиливает эмпатию",
          "придаёт смелости","настраивает на комфорт","повышает коммуникабельность",
          "усиливает заботу","разжигает творческий огонь","настраивает на порядок",
          "заставляет искать баланс","поднимает страсть","толкает к приключениям"]

MOON_ICONS = "🌑🌒🌓🌔🌕🌖🌗🌘"

def moon_phase() -> str:
    jd = swe.julday(*dt.datetime.utcnow().timetuple()[:3])
    sun = swe.calc_ut(jd, swe.SUN )[0][0]
    moon= swe.calc_ut(jd, swe.MOON)[0][0]
    phase = ((moon - sun + 360) % 360) / 360         # 0…1
    illum = round(abs(math.cos(math.pi*phase))*100)
    icon  = MOON_ICONS[int(phase*8)%8]
    name  = ("Новолуние" if illum<5 else
             "Растущая Луна" if phase<.5 else
             "Полнолуние" if illum>95 else
             "Убывающая Луна")
    sign  = int(moon//30)
    return f"{icon} {name} в {SIGNS[sign]} ({illum} %) — {EFFECT[sign]}"

def planet_parade() -> str | None:
    """Мини-парад: 3 планеты в «секторе» < 90°."""
    jd = swe.julday(*dt.datetime.utcnow().timetuple()[:3])
    lons = sorted(swe.calc_ut(jd, b)[0][0]
                  for b in (swe.MERCURY, swe.VENUS,
                            swe.MARS,    swe.JUPITER,
                            swe.SATURN))
    best = min((lons[i+2]-lons[i]) % 360
               for i in range(len(lons)-2))
    return "Мини-парад планет" if best < 90 else None

def eta_aquarids() -> str | None:
    yday = dt.datetime.utcnow().timetuple().tm_yday
    return "Eta Aquarids (метеоры)" if 120 <= yday <= 140 else None

def upcoming_event(days:int=3) -> str | None:
    """Заглушка-пример: в будущем здесь можно вычислять реальные явления."""
    # пока просто демонстрируем формат
    return f"Через {days} дня частное солнечное затмение" if days==3 else None

def astro_events() -> list[str]:
    ev: list[str] = [moon_phase()]
    if p := planet_parade(): ev.append(p)
    if m := eta_aquarids():  ev.append(m)
    if a := upcoming_event(): ev.append(a)
    return ev

# ─────────── 5.  GPT  /  CULPRITS ──────────────────────────────
CULPRITS: dict[str, dict[str, Any]] = {
    "туман": {
        "emoji": "🌁",
        "tips": [
            "🔦 Светлая одежда и фонарь",
            "🚗 Водите аккуратнее",
            "⏰ Платируйте дорогу с запасом",
        ],
    },
    "магнитные бури": {
        "emoji": "🧲",
        "tips": [
            "🧘 5-минутная дыхательная пауза",
            "🌿 Заварите чай с мелиссой",
            "🙅 Избегайте стресса и новостей",
            "😌 Лёгкая растяжка перед сном",
        ],
    },
    "низкое давление": {
        "emoji": "🌡️",
        "tips": [
            "💧 Пейте больше воды",
            "😴 20-мин дневной отдых",
            "🤸 Нежная зарядка",
            "🥗 Лёгкий ужин без соли",
        ],
    },
    "шальной ветер": {
        "emoji": "💨",
        "tips": [
            "🧣 Захватите шарф",
            "🚶  Короткая быстрая прогулка",
            "🕶️ Защитите глаза от пыли",
        ],
    },
    "жара": {
        "emoji": "🔥",
        "tips": [
            "💦 Держите бутылку воды под рукой",
            "🧢 Головной убор обязателен",
            "🌳 Ищите тень в полдень",
        ],
    },
    "сырость": {
        "emoji": "💧",
        "tips": [
            "👟 Сменная обувь не помешает",
            "🌂 Компактный зонт в рюкзак",
            "🌬️ Проветривайте помещения",
        ],
    },
    "полная луна": {
        "emoji": "🌕",
        "tips": [
            "📝 Запишите яркие идеи",
            "🧘 Мягкая медитация перед сном",
            "🌙 Полюбуйтесь луной без гаджетов",
        ],
    },
    "мини-парад планет": {
        "emoji": "✨",
        "tips": [
            "🔭 Посмотрите на небо на рассвете",
            "📸 Фото заката для настроения",
            "🤔 Задумайтесь о вселенной",
        ],
    },
}

def gpt_blurb(culprit: str) -> tuple[str, list[str]]:
    """1-строчный вывод + 2 совета. GPT-4o-mini если ключ есть,
       иначе берём готовые советы из CULPRITS."""
    tips_pool = CULPRITS[culprit]["tips"]
    if not OPENAI_KEY:
        return (f"Если завтра что-то пойдёт не так, вините {culprit}! 😉",
                random.sample(tips_pool, 2))
    prompt = (f"Одна строка «Если завтра что-то пойдёт не так, вините {culprit}!». "
              f"После точки — позитив ≤12 слов. Далее 3 bullet-совета ≤12 слов с эмодзи.")
    ans = OpenAI(api_key=OPENAI_KEY).chat.completions.create(
        model="gpt-4o-mini", temperature=0.6,
        messages=[{"role": "user", "content": prompt}]
    ).choices[0].message.content.strip().splitlines()
    line = [l.strip() for l in ans if l.strip()]
    summary = line[0]
    tips = [l.lstrip("-• ").strip() for l in line[1:4]]
    if len(tips) < 2:         # страховка
        tips = random.sample(tips_pool, 2)
    return summary, tips
# ─────────── 6. BUILD MESSAGE ────────────────────────────────────
from .utils import compass, clouds_word, wind_phrase, safe, get_fact
from .weather import get_weather  # ваш модуль 2
from .air import get_air, get_pollen, get_sst, get_kp
from .schumann import get_schumann
from .astro import astro_events
from .gpt import gpt_blurb, CULPRITS

WEATHER_ICONS = {
    "ясно":       "☀️",
    "переменная": "🌤️",
    "пасмурно":   "☁️",
    "дождь":      "🌧️",
    "туман":      "🌁",
}

def build_msg() -> str:
    w = get_weather(LAT, LON)
    if not w:
        raise RuntimeError("Источники погоды недоступны")

    # единый wind_deg и wind_kmh
    if "current" in w:
        cur = w["current"]
        wind_kmh = cur["wind_speed"] * 3.6
        wind_deg = cur["wind_deg"]
        press    = cur["pressure"]
        cloud_w  = clouds_word(cur.get("clouds",0))
        day_block = w["daily"][0]["temp"]
        day_max, night_min = day_block["max"], day_block["min"]
    else:
        cur = w["current_weather"]
        wind_kmh = cur["windspeed"]
        wind_deg = cur["winddirection"]
        press    = cur.get("pressure", w["hourly"]["surface_pressure"][0])
        cloud_w  = clouds_word(w["hourly"]["cloud_cover"][0])
        d = w["daily"]
        # завтра из Open-Meteo
        arr_max = d["temperature_2m_max"]
        arr_min = d["temperature_2m_min"]
        codes   = d["weathercode"]
        day_max   = arr_max[1] if len(arr_max)>1 else arr_max[0]
        night_min = arr_min[1] if len(arr_min)>1 else arr_min[0]
        wcode     = codes[1]   if len(codes)>1   else codes[0]

    strong_wind = w["strong_wind"]
    fog_alert   = w["fog_alert"]

    # температурные лидеры
    temps = {}
    for city,(la,lo) in CITIES.items():
        wc = get_weather(la,lo)
        if not wc: continue
        if "current" in wc:
            temps[city] = wc["daily"][0]["temp"]["max"]
        else:
            dm = wc["daily"]
            arr = dm["temperature_2m_max"]
            temps[city] = arr[1] if len(arr)>1 else arr[0]
    warm = max(temps, key=temps.get)
    cold = min(temps, key=temps.get)

    # остальное
    air    = get_air() or {}
    pollen = get_pollen()
    sst    = get_sst()
    kp, kp_state = get_kp()
    sch    = get_schumann()
    astro_list = astro_events()

    # виновник
    if fog_alert:
        culprit = "туман"
    elif kp_state=="буря":
        culprit = "магнитные бури"
    elif press < 1007:
        culprit = "низкое давление"
    elif strong_wind:
        culprit = "шальной ветер"
    else:
        culprit = "мини-парад планет"
    summary, tips = gpt_blurb(culprit)

    icon = WEATHER_ICONS.get(cloud_w, "🌦️")

    lines = [
        f"{icon} <b>Погода на завтра в Лимассоле {TOMORROW.format('DD.MM.YYYY')}</b>",
        f"<b>Темп. днём:</b> до {day_max:.1f} °C",
        f"<b>Темп. ночью:</b> около {night_min:.1f} °C",
        f"<b>Облачность:</b> {cloud_w}",
        f"<b>Ветер:</b> {wind_phrase(wind_kmh)} ({wind_kmh:.1f} км/ч, {compass(wind_deg)})",
        *(["⚠️ Ветер может усиливаться"] if strong_wind else []),
        *(["🌁 Возможен туман, водите аккуратно"] if fog_alert else []),
        f"<b>Давление:</b> {press:.0f} гПа",
        f"<i>Самый тёплый город:</i> {warm} ({temps[warm]:.1f} °C)",
        f"<i>Самый прохладный город:</i> {cold} ({temps[cold]:.1f} °C)",
        "———",
        "🏙️ <b>Качество воздуха</b>",
        f"{air.get('lvl','⚪️')} AQI {air.get('aqi','—')} | "
        f"PM2.5: {safe(air.get('pm25'),' µg/м³')} | PM10: {safe(air.get('pm10'),' µg/м³')}",
    ]

    if pollen:
        idx = lambda v: ["нет","низкий","умеренный","высокий","оч. высокий","экстрим"][int(round(v))]
        lines += [
            "🌿 <b>Пыльца</b>",
            f"Деревья — {idx(pollen['treeIndex'])} | "
            f"Травы — {idx(pollen['grassIndex'])} | "
            f"Сорняки — {idx(pollen['weedIndex'])}",
        ]

    if kp is not None:
        lines += ["🧲 <b>Геомагнитная активность</b>",
                  f"K-index: {kp:.1f} ({kp_state})"]
    else:
        lines += ["🧲 <b>Геомагнитная активность</b>", "нет данных"]

    if sch.get("high"):
        lines += ["🎵 <b>Шуман:</b> ⚡️ вибрации повышены (>8 Гц)"]
    elif "freq" in sch:
        lines += [f"🎵 <b>Шуман:</b> ≈{sch['freq']:.1f} Гц, амплитуда стабильна"]
    else:
        lines += [f"🎵 <b>Шуман:</b> {sch.get('msg','нет данных')}"]

    if sst is not None:
        lines += [f"🌊 <b>Температура воды</b>\nСейчас: {sst:.1f} °C"]

    if astro_list:
        lines += ["🌌 <b>Астрологические события</b>\n" + " | ".join(astro_list)]

    lines += [
        "———",
        f"📜 <b>Вывод</b>\n{summary}",
        "———",
        "✅ <b>Рекомендации</b>",
        *[f"• {t}" for t in tips],
        "———",
        f"📚 {get_fact(TOMORROW)}",
    ]
    return "\n".join(lines)



# ─────────── 7.  SEND / EXTRA ──────────────────────────────────────────
UNSPLASH_KEY = os.getenv("UNSPLASH_KEY")          # optional – фото заката

POLL_QUESTION = "Как сегодня ваше самочувствие? 🤔"
POLL_OPTIONS  = ["🔥 Полон(а) энергии", "🙂 Нормально", "😴 Слегка вялый(ая)", "🤒 Всё плохо"]

async def send_main_post(bot: Bot, text: str) -> None:
    """Отправка самого HTML-поста."""
    await bot.send_message(
        int(CHAT),
        text[:4096],
        parse_mode="HTML",
        disable_web_page_preview=True,
    )

async def send_friday_poll(bot: Bot) -> None:
    """Раз в неделю (пятница) кидаем опрос под постом."""
    try:
        await bot.send_poll(
            int(CHAT),
            question=POLL_QUESTION,
            options=POLL_OPTIONS,
            is_anonymous=False,
            allows_multiple_answers=False,
        )
    except tg_err.TelegramError as e:
        logging.warning("Poll send error: %s", e)

async def fetch_unsplash_photo() -> Optional[str]:
    """Берём случайное фото Кипра / Лимассола (Unsplash Source API)."""
    if not UNSPLASH_KEY:
        return None
    url = "https://api.unsplash.com/photos/random"
    j   = _get(url, query="cyprus coast sunset", client_id=UNSPLASH_KEY)
    try:
        return j["urls"]["regular"]
    except Exception:
        return None

async def send_media(bot: Bot, photo_url: str) -> None:
    """Прикрепляем фото отдельным сообщением (media group ненужна, если 1 фото)."""
    try:
        await bot.send_photo(int(CHAT), photo=photo_url, caption="Фото дня • Unsplash")
    except tg_err.TelegramError as e:
        logging.warning("Photo send error: %s", e)

# ─────────── main() ────────────────────────────────────────────────────
async def main() -> None:
    bot  = Bot(TOKEN)

    # 1) главный пост
    html = build_msg()
    logging.info("Preview: %s", html.replace('\n', ' | ')[:250])
    await send_main_post(bot, html)

    # 2) пятничный опрос
    if pendulum.now(TZ).is_friday():
        await send_friday_poll(bot)

    # 3) каждые 3 дня — картинка (UTC-дата, чтобы было стабильно)
    if UNSPLASH_KEY and (dt.datetime.utcnow().toordinal() % 3 == 0):
        if (photo := await fetch_unsplash_photo()):
            await send_media(bot, photo)

    logging.info("All messages sent ✓")

# ───────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    asyncio.run(main())
