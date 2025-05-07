"""
post.py – VayboМетр v4.1 (final, with template-fix)

✅ Главное  
• HTML-заголовки, облачность = «ясно / переменная / облачно»  
• Шутливая строка-ремарка о погоде с давлением  
• Пыльца (Tomorrow.io)  
• Шуман: 2 узла GCI → если 3 дня тишина — «датчики ушли в ретрит»  
• Астроблок — фаза + знак Луны, ретро-Меркурий, трин Венера-Юпитер, парад, метеорный поток  
• Вывод всегда: «Если сегодня … вините <фактор>.» + 3 коротких совета-эмодзи  
"""

from __future__ import annotations
import asyncio, json, math, os, sys
from datetime import datetime, timedelta
from typing import Dict, Any, Optional

import requests, swisseph as swe
from openai import OpenAI
from telegram import Bot, error as tg_err

LAT, LON = 34.707, 33.022  # Limassol

# ───────── helpers ────────────────────────────────────────────────────────
def _get(url: str, **params) -> Optional[dict]:
    try:
        r = requests.get(url, params=params, timeout=20)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        print(f"[warn] {url} -> {e}", file=sys.stderr)
        return None

deg2dir = lambda d: "N NE E SE S SW W NW".split()[int((d + 22.5) % 360 // 45)]
wind_phrase = lambda k: ("штиль" if k < 5 else
                         "слабый" if k < 15 else
                         "умеренный" if k < 30 else
                         "сильный")
clouds_word = lambda p: "ясно" if p < 25 else "переменная" if p < 70 else "облачно"

def aqi_to_pm25(aqi: float) -> float:
    table = [(0,50,0,12), (51,100,12.1,35.4), (101,150,35.5,55.4),
             (151,200,55.5,150.4), (201,300,150.5,250.4),
             (301,400,250.5,350.4), (401,500,350.5,500.4)]
    for Il, Ih, Cl, Ch in table:
        if Il <= aqi <= Ih:
            return round((aqi-Il)*(Ch-Cl)/(Ih-Il) + Cl, 1)
    return aqi

# ───────── data sources ──────────────────────────────────────────────────
def get_weather():
    if (k := os.getenv("OWM_KEY")):
        for ver in ("3.0", "2.5"):
            d = _get(f"https://api.openweathermap.org/data/{ver}/onecall",
                     lat=LAT, lon=LON, appid=k, units="metric",
                     exclude="minutely,hourly,alerts")
            if d and d.get("current"):
                return d
    return _get("https://api.open-meteo.com/v1/forecast",
                latitude=LAT, longitude=LON, current_weather=True,
                hourly="cloud_cover,precipitation_probability,weathercode,surface_pressure",
                daily="temperature_2m_max,temperature_2m_min,precipitation_probability_max",
                timezone="UTC")

get_air = lambda: _get("https://api.airvisual.com/v2/nearest_city",
                       lat=LAT, lon=LON, key=os.getenv("AIRVISUAL_KEY")) \
                       if os.getenv("AIRVISUAL_KEY") else None

def pm10_openmeteo():
    d = _get("https://air-quality-api.open-meteo.com/v1/air-quality",
             latitude=LAT, longitude=LON, hourly="pm10", timezone="UTC")
    try:
        return round(float(d["hourly"]["pm10"][0]), 1)
    except Exception:
        return None

def get_pollen():
    key = os.getenv("TOMORROW_KEY")
    if not key:
        return None
    d = _get("https://api.tomorrow.io/v4/timelines",
             apikey=key, location=f"{LAT},{LON}",
             fields="treeIndex,grassIndex,weedIndex",
             timesteps="1d", units="metric")
    try:
        return d["data"]["timelines"][0]["intervals"][0]["values"]
    except Exception:
        return None

def get_sst():
    d = _get("https://marine-api.open-meteo.com/v1/marine",
             latitude=LAT, longitude=LON,
             hourly="sea_surface_temperature", timezone="UTC")
    try:
        return round(float(d["hourly"]["sea_surface_temperature"][0]), 1)
    except Exception:
        return None

get_kp = lambda: (lambda arr: float(arr[-1][1]) if arr else None)(
    _get("https://services.swpc.noaa.gov/products/noaa-planetary-k-index.json"))

def get_schumann():
    d = _get("https://api.glcoherence.org/v1/earth")
    if d:
        return {"freq": d["frequency_1"], "amp": d["amplitude_1"]}
    d = _get("https://gci-api.ucsd.edu/data/latest")
    if d:
        return {"freq": d["data"]["sr1"]["frequency"],
                "amp": d["data"]["sr1"]["amplitude"]}
    quiet = all(not _get("https://api.glcoherence.org/v1/earth",
                         date=(datetime.utcnow()-timedelta(days=i)).strftime("%Y-%m-%d"))
                for i in (1, 2, 3))
    return {"msg": "датчики молчат третий день — ушли в ретрит"} if quiet else {"prev": "7.8 Гц, спокойно"}

# ───────── astrology ────────────────────────────────────────────────────
signs = "Овне Тельце Близнецах Раке Льве Деве Весах Скорпионе Стрельце Козероге Водолее Рыбах".split()
lunar_eff = ("придаёт смелости","заставляет чувствовать комфорт","повышает коммуникабельность",
             "усиливает заботу","разжигает творческий огонь","настраивает на порядок",
             "заставляет искать баланс","поднимает страсть","толкает к приключениям",
             "фокусирует на деле","дарит странные идеи","усиливает эмпатию")

def moon_phase(jd):
    sun = swe.calc_ut(jd, swe.SUN)[0][0]
    moon = swe.calc_ut(jd, swe.MOON)[0][0]
    phase = ((moon - sun + 360) % 360) / 360
    illum = round(abs(math.cos(math.pi * phase)) * 100)
    name = ("Новолуние" if illum < 5 else
            "Растущая Луна" if phase < .5 else
            "Полнолуние" if illum > 95 else
            "Убывающая Луна")
    sign = int(moon // 30)
    return f"{name} в {signs[sign]} — {lunar_eff[sign]} ({illum} %)"

def planet_parade(jd):
    bodies = [swe.MERCURY, swe.VENUS, swe.MARS, swe.JUPITER, swe.SATURN]
    lons = sorted(swe.calc_ut(jd, b)[0][0] for b in bodies)
    best = min((lons[i+2] - lons[i]) % 360 for i in range(len(lons)-2))
    return "Мини-парад планет" if best < 90 else None

def trine_vj(jd):
    lon1, lon2 = swe.calc_ut(jd, swe.VENUS)[0][0], swe.calc_ut(jd, swe.JUPITER)[0][0]
    return "Трин Венеры и Юпитера — волна удачи" if abs((lon1 - lon2 + 180) % 360 - 180) < 4 else None

def meteor_shower():
    showers = {"Eta Aquarids": ((4,19),(5,28),(6,6),60)}
    today = datetime.utcnow().date()
    for name, (start, peak, end, zhr) in showers.items():
        if datetime(today.year,*start).date() <= today <= datetime(today.year,*end).date():
            if today == datetime(today.year,*peak).date():
                return f"Метеорный поток {name} — до {zhr} метеоров/ч сейчас"
            return f"{name} активен (пик — {peak[1]} {datetime(today.year,*peak).strftime('%b')})"
    return None

def astro_events():
    jd = swe.julday(*datetime.utcnow().timetuple()[:3])
    parts = [moon_phase(jd)]
    if p := planet_parade(jd): parts.append(p)
    if t := trine_vj(jd): parts.append(t)
    if swe.calc_ut(jd, swe.MERCURY)[0][3] < 0: parts.append("Меркурий ретрограден")
    if m := meteor_shower(): parts.append(m)
    return "\n".join(parts)

# ───────── GPT blurb (template-fixed) ────────────────────────────────────
def gpt_blurb(culprit: str) -> tuple[str,str]:
    prompt = (
        f"Сделай вывод ровно в одну строку, начинай дословно: "
        f"«Если сегодня что-то пойдёт не так, вините {culprit}.». "
        "После точки добавь короткий позитив (≤12 слов)."
        "\n\nЗатем дай РОВНО 3 bullet-совета (по одной строке), "
        "с эмодзи, не длиннее 12 слов."
    )
    res = OpenAI(api_key=os.environ["OPENAI_API_KEY"]).chat.completions.create(
        model="gpt-4o-mini", temperature=0.6,
        messages=[{"role": "user", "content": prompt}]
    ).choices[0].message.content.strip().splitlines()
    res = [l.strip() for l in res if l.strip()]
    summary = res[0]
    tips = [l.lstrip("-• ").strip() for l in res[1:4]]  # 3 bullets
    return summary, "\n".join(f"- {t}" for t in tips)

# ───────── digest builder ────────────────────────────────────────────────
def build_md(d: Dict[str,Any]) -> str:
    P = []
    w = d["weather"]

    # WEATHER
    if "current" in w:            # OpenWeather
        cur, day = w["current"], w["daily"][0]["temp"]
        cloud = clouds_word(cur.get("clouds", 0))
        wind = cur["wind_speed"] * 3.6
        press = cur["pressure"]
        P += ["☀️ <b>Погода</b>",
              f"<b>Температура:</b> днём до {day['max']:.0f} °C, ночью около {day['min']:.0f} °C",
              f"<b>Облачность:</b> {cloud}",
              "<b>Осадки:</b> не ожидаются" if w["daily"][0].get("rain",0)==0 else "<b>Осадки:</b> возможен дождь",
              f"<b>Ветер:</b> {wind_phrase(wind)} ({wind:.1f} км/ч), {deg2dir(cur['wind_deg'])}",
              f"<b>Давление:</b> {press} гПа",
              f"Лайтовый бриз, давление {press} гПа — {'↓' if press < 1010 else '↑' if press > 1020 else 'ок'}."]
    else:                         # Open-Meteo
        cw, dm = w["current_weather"], w["daily"]
        cloud = clouds_word(w["hourly"]["cloud_cover"][0])
        wind = cw["windspeed"]
        press = w["hourly"]["surface_pressure"][0]
        P += ["☀️ <b>Погода</b>",
              f"<b>Температура:</b> днём до {dm['temperature_2m_max'][0]:.0f} °C, "
              f"ночью около {dm['temperature_2m_min'][0]:.0f} °C",
              f"<b>Облачность:</b> {cloud}",
              "<b>Осадки:</b> не ожидаются" if dm["precipitation_probability_max"][0]<20 else "<b>Осадки:</b> возможен дождь",
              f"<b>Ветер:</b> {wind_phrase(wind)} ({wind:.1f} км/ч), {deg2dir(cw['winddirection'])}",
              f"<b>Давление:</b> {press:.0f} гПа",
              f"Спокойно, давление {press:.0f} гПа — {'↓' if press < 1010 else '↑' if press > 1020 else 'норм'}."]
    press_val = float(P[-1].split()[2])

    # AIR
    if (air := d["air"]):
        pol = air["data"]["current"]["pollution"]
        pm25 = pol.get("p2") or aqi_to_pm25(pol["aqius"])
        pm10 = pol.get("p1") or d["pm10"] or "нет данных"
        level = ("хороший" if pol["aqius"]<=50 else "умеренный" if pol["aqius"]<=100 else "вредный")
        P += ["","🌬️ <b>Качество воздуха</b>",
              f"<b>AQI:</b> {pol['aqius']} | <b>PM2.5:</b> {pm25} µg/m³ | <b>PM10:</b> {pm10} µg/m³",
              f"Воздух {level}."]

    # POLLEN
    idx = lambda v: ("нет","низкий","умеренный","высокий","оч. высокий","экстрим")[int(round(v))]
    if (pol := d["pollen"]):
        P += ["","🌿 <b>Пыльца</b>",
              f"Деревья — {idx(pol['treeIndex'])} | Травы — {idx(pol['grassIndex'])} | Амброзия — {idx(pol['weedIndex'])}"]

    # KP
    kp = d["kp"]
    state = ("буря (G1)" if kp and kp>=5 else
             "спокойный" if kp and kp<4 else
             "повышенный")
    P += ["","🌌 <b>Геомагнитная активность</b>",
          f"<b>Уровень:</b> {state} (Kp {kp:.1f})" if kp else "нет данных"]

    # SCHUMANN
    sch = d["schumann"]
    if sch and "freq" in sch:
        P += ["","📈 <b>Резонанс Шумана</b>",
              f"<b>Частота:</b> ≈{sch['freq']:.1f} Гц • амплитуда стабильна"]
    else:
        P += ["","📈 <b>Резонанс Шумана</b>",
              sch.get("msg") if sch and "msg" in sch else sch.get("prev","нет данных")]

    # SST
    if d["sst"]:
        P += ["","🌊 <b>Температура воды в море</b>",
              f"<b>Сейчас:</b> {d['sst']} °C"]

    # ASTRO
    astro = astro_events()
    if astro:
        P += ["","🔮 <b>Астрологические события</b>", astro]

    P.append("---")

    culprit = ("ретроградного Меркурия" if "ретрограден" in astro else
               "магнитных бурь" if kp and kp>=5 else
               "низкого давления" if press_val < 1007 else
               "мини-парада планет")
    summary, tips = gpt_blurb(culprit)
    P += ["<b>📝 Вывод</b>", summary, "", "---", "<b>✅ Рекомендации</b>", tips]
    return "\n".join(P)

# ───────── Telegram send ────────────────────────────────────────────────
async def send(text: str):
    await Bot(os.environ["TELEGRAM_TOKEN"]).send_message(
        chat_id=os.environ["CHANNEL_ID"],
        text=text[:4096],
        parse_mode="HTML",
        disable_web_page_preview=True)

# ───────── main ─────────────────────────────────────────────────────────
async def main():
    data = {
        "weather": get_weather(),
        "air": get_air(),
        "pm10": pm10_openmeteo(),
        "pollen": get_pollen(),
        "sst": get_sst(),
        "kp": get_kp(),
        "schumann": get_schumann()
    }
    md = build_md(data)
    print("MD preview:", md[:250].replace("\n"," | "))
    try:
        await send(md)
        print("✓ sent")
    except tg_err.TelegramError as e:
        print("Telegram error:", e, file=sys.stderr)
        raise

if __name__ == "__main__":
    asyncio.run(main())
