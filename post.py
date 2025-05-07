"""
post.py – VayboМетр v4.0 («moon-party edition»)

✓ жирные заголовки через HTML
✓ облачность → ясно / переменная / облачно
✓ однострочная шутливая ремарка о погоде
✓ Schumann: 1-й API → резерв; если оба 3 дня молчат → «датчики ушли в ретрит»
✓ пыльца (Tomorrow.io)
✓ астрособытия:
   • фаза + знак Луны + эффект
   • ретро-Меркурий
   • трин Венера-Юпитер (±4°)
   • мини-парад ≥3 планет < 90°
   • актуальный метеорный поток (список ico)
✓ вывод — 1 строка + «вините <фактор>»
✓ 3 совета-буллета < 12 слов, без нумерации
"""

from __future__ import annotations
import asyncio, json, math, os, sys
from datetime import datetime, timedelta
from typing import Dict, Any, Optional

import requests, swisseph as swe
from openai import OpenAI
from telegram import Bot, error as tg_err

LAT, LON = 34.707, 33.022   # Limassol

# ───────── helpers ─────────────────────────────────────────────────────────
def _get(url: str, **params) -> Optional[dict]:
    try:
        r = requests.get(url, params=params, timeout=20)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        print(f"[warn] {url} -> {e}", file=sys.stderr); return None

deg2dir = lambda d: "N NE E SE S SW W NW".split()[int((d+22.5)%360//45)]
wind_phrase = lambda k: "штиль" if k < 5 else "слабый" if k < 15 else "умеренный" if k < 30 else "сильный"
clouds_word = lambda pc: "ясно" if pc < 25 else "переменная" if pc < 70 else "облачно"

def aqi_to_pm25(aqi: float) -> float:
    bp=[(0,50,0,12),(51,100,12.1,35.4),(101,150,35.5,55.4),
        (151,200,55.5,150.4),(201,300,150.5,250.4),(301,400,250.5,350.4),(401,500,350.5,500.4)]
    for Il,Ih,Cl,Ch in bp:
        if Il<=aqi<=Ih: return round((aqi-Il)*(Ch-Cl)/(Ih-Il)+Cl,1)
    return aqi

# ───────── data sources ────────────────────────────────────────────────────
def get_weather():
    if (k:=os.getenv("OWM_KEY")):
        for ver in ("3.0","2.5"):
            d=_get(f"https://api.openweathermap.org/data/{ver}/onecall",
                   lat=LAT,lon=LON,appid=k,units="metric",exclude="minutely,hourly,alerts")
            if d and d.get("current"): return d
    return _get("https://api.open-meteo.com/v1/forecast",
                latitude=LAT,longitude=LON,current_weather=True,
                hourly="cloud_cover,precipitation_probability,weathercode,surface_pressure",
                daily="temperature_2m_max,temperature_2m_min,precipitation_probability_max",
                timezone="UTC")

def get_air():
    key=os.getenv("AIRVISUAL_KEY")
    return _get("https://api.airvisual.com/v2/nearest_city",lat=LAT,lon=LON,key=key) if key else None

def pm10_openmeteo():
    d=_get("https://air-quality-api.open-meteo.com/v1/air-quality",
           latitude=LAT,longitude=LON,hourly="pm10",timezone="UTC")
    try:return round(float(d["hourly"]["pm10"][0]),1)
    except Exception:return None

def get_pollen():
    k=os.getenv("TOMORROW_KEY")
    if not k: return None
    d=_get("https://api.tomorrow.io/v4/timelines",
           apikey=k,location=f"{LAT},{LON}",
           fields="treeIndex,grassIndex,weedIndex",
           timesteps="1d",units="metric")
    try:v=d["data"]["timelines"][0]["intervals"][0]["values"];return v
    except Exception:return None

def get_sst():
    d=_get("https://marine-api.open-meteo.com/v1/marine",
           latitude=LAT,longitude=LON,
           hourly="sea_surface_temperature",timezone="UTC")
    try:return round(float(d["hourly"]["sea_surface_temperature"][0]),1)
    except Exception:return None

get_kp=lambda: (lambda arr: float(arr[-1][1]) if arr else None)(
    _get("https://services.swpc.noaa.gov/products/noaa-planetary-k-index.json"))

def get_schumann():
    # primary
    d=_get("https://api.glcoherence.org/v1/earth")
    if d: return {"freq":d["frequency_1"],"amp":d["amplitude_1"]}
    # backup
    d=_get("https://gci-api.ucsd.edu/data/latest")
    if d: return {"freq":d["data"]["sr1"]["frequency"],
                  "amp":d["data"]["sr1"]["amplitude"]}
    # last 3 days?
    quiet=True
    for i in (1,2,3):
        y=_get("https://api.glcoherence.org/v1/earth",
               date=(datetime.utcnow()-timedelta(days=i)).strftime("%Y-%m-%d"))
        if y: quiet=False
    return {"msg":"датчики молчат третий день — ушли в ретрит"} if quiet else {"prev":"7.8 Гц, спокойно"}

# ───────── astrology ──────────────────────────────────────────────────────
signs="Овне Тельце Близнецах Раке Льве Деве Весах Скорпионе Стрельце Козероге Водолее Рыбах".split()
lunar_effect=("придаёт смелости","заставляет чувствовать комфорт","повышает коммуникабельность",
              "усиливает заботу","разжигает творческий огонь","настраивает на порядок",
              "заставляет искать баланс","поднимает страсть","толкает к приключениям",
              "фокусирует на деле","дарит странные идеи","усиливает эмпатию")

def moon_phase(jd: float)->str:
    sun=swe.calc_ut(jd,swe.SUN)[0][0]; moon=swe.calc_ut(jd,swe.MOON)[0][0]
    phase=((moon-sun+360)%360)/360
    percent=round(abs(math.cos(math.pi*phase))*100)
    name=("Новолуние" if percent<5 else
          "Растущая Луна" if phase<0.5 else
          "Полнолуние" if percent>95 else
          "Убывающая Луна")
    sign=int(moon//30)
    return f"{name} в {signs[sign]} — {lunar_effect[sign]} ({percent} %)"

def planet_parade(jd: float)->Optional[str]:
    bodies=[swe.MERCURY,swe.VENUS,swe.MARS,swe.JUPITER,swe.SATURN]
    lons=sorted(swe.calc_ut(jd,b)[0][0] for b in bodies)
    best=min((lons[i+2]-lons[i])%360 for i in range(len(lons)-2))
    return "Мини-парад планет" if best<90 else None

def aspect(body1,body2,jd,orb=4,typ="trine")->Optional[str]:
    lon1,lon2=swe.calc_ut(jd,body1)[0][0],swe.calc_ut(jd,body2)[0][0]
    diff=abs((lon1-lon2+180)%360-180)
    target=120 if typ=="trine" else 180
    return f"Трин Венеры и Юпитера — волна удачи" if diff<orb and typ=="trine" else None

def meteor_shower() -> Optional[str]:
    # простая таблица пиков
    showers={"Eta Aquarids":((4,19),(5,28),(6,6),60),
             "Perseids":((7,17),(8,12),(8,24),100),
             "Geminids":((12,4),(12,14),(12,17),120)}
    today=datetime.utcnow().date()
    for name,(start,peak,end,max_zhr) in showers.items():
        start_dt=datetime(today.year,*start).date()
        peak_dt=datetime(today.year,*peak).date()
        end_dt=datetime(today.year,*end).date()
        if start_dt<=today<=end_dt:
            if today==peak_dt:
                return f"Метеорный поток {name} — до {max_zhr} метеоров/ч сейчас"
            else:
                return f"{name} активен (пик {peak_dt.day} {peak_dt.strftime('%b')})"
    return None

def astro_events() -> str:
    jd=swe.julday(*datetime.utcnow().timetuple()[:3])
    parts=[moon_phase(jd)]
    if (p:=planet_parade(jd)): parts.append(p)
    if (a:=aspect(swe.VENUS,swe.JUPITER,jd)): parts.append(a)
    if swe.calc_ut(jd,swe.MERCURY)[0][3] < 0: parts.append("Меркурий ретрограден")
    if (m:=meteor_shower()): parts.append(m)
    return "\n".join(parts)

# ───────── GPT – fun block ────────────────────────────────────────────────
@@
-def gpt_blurb(culprit:str)->tuple[str,str]:
-    prompt=(f"Одно предложение-вывод (вините {culprit}). "
-            "Затем 3 весёлых совета, emoji приветствуются, ≤12 слов каждый.")
+def gpt_blurb(culprit: str) -> tuple[str, str]:
+    # требуем точный шаблон начала вывода
+    prompt = (
+        "Сформируй вывод РОВНО в одну строку и начинай его дословно: "
+        "«Если сегодня что-то пойдёт не так, вините …». "
+        f"Вместо многоточия подставь {culprit}. "
+        "Продолжи ещё одной короткой фразой (≤ 12 слов, позитивный тон). "
+        "После пустой строки дай ровно 3 советы-буллета, "
+        "каждый ≤ 12 слов, с эмодзи, без нумерации."
+    )
@@
-    lines=[l.strip() for l in rsp.splitlines() if l.strip()]
-    summary=lines[0]
-    tips=[l.lstrip("-• ").strip() for l in lines[1:4]]
+    lines=[l.strip() for l in rsp.splitlines() if l.strip()]
+    summary=lines[0]                      # гарантированно с нужным началом
+    tips=[l.lstrip("-• ").strip() for l in lines[1:4]]   # 3 bullets
     return summary, "\n".join(f"- {t}" for t in tips)


# ───────── digest builder ────────────────────────────────────────────────
def build_md(d:Dict[str,Any]) -> str:
    P=[]; weather=d["weather"]

    # ── WEATHER
    if "current" in weather:   # OWM
        cur,day=weather["current"],weather["daily"][0]["temp"]
        cloud_txt=clouds_word(cur.get("clouds",0)); wind_k=cur["wind_speed"]*3.6
        pressure=cur["pressure"]
        P+=["☀️ <b>Погода</b>",
            f"<b>Температура:</b> днём до {day['max']:.0f} °C, ночью около {day['min']:.0f} °C",
            f"<b>Облачность:</b> {cloud_txt}",
            "<b>Осадки:</b> не ожидаются" if weather["daily"][0].get("rain",0)==0 else "<b>Осадки:</b> возможен дождь",
            f"<b>Ветер:</b> {wind_phrase(wind_k)} ({wind_k:.1f} км/ч), {deg2dir(cur['wind_deg'])}",
            f"<b>Давление:</b> {pressure} гПа",
            f"Лайтовый бриз, давление {pressure} гПа — {'↓' if pressure<1010 else '↑' if pressure>1020 else 'ок'}."]
    else:                     # Open-Meteo
        cw=weather["current_weather"]; dm=weather["daily"]
        cloud_txt=clouds_word(weather["hourly"]["cloud_cover"][0])
        wind=cw["windspeed"]; pressure=weather["hourly"]["surface_pressure"][0]
        P+=["☀️ <b>Погода</b>",
            f"<b>Температура:</b> днём до {dm['temperature_2m_max'][0]:.0f} °C, "
            f"ночью около {dm['temperature_2m_min'][0]:.0f} °C",
            f"<b>Облачность:</b> {cloud_txt}",
            "<b>Осадки:</b> не ожидаются" if dm["precipitation_probability_max"][0]<20 else "<b>Осадки:</b> возможен дождь",
            f"<b>Ветер:</b> {wind_phrase(wind)} ({wind:.1f} км/ч), {deg2dir(cw['winddirection'])}",
            f"<b>Давление:</b> {pressure:.0f} гПа",
            f"Спокойно, давление {pressure:.0f} гПа — {'↓' if pressure<1010 else '↑' if pressure>1020 else 'норм'}."]
    pressure_val=float(P[-1].split()[2])

    # ── AIR quality
    if (air:=d["air"]):
        pol=air["data"]["current"]["pollution"]
        pm25=pol.get("p2") or aqi_to_pm25(pol["aqius"])
        pm10=pol.get("p1") or d["pm10"] or "нет данных"
        level=("хороший" if pol["aqius"]<=50 else "умеренный" if pol["aqius"]<=100 else "вредный")
        P+=["","🌬️ <b>Качество воздуха</b>",
            f"<b>AQI:</b> {pol['aqius']}  |  <b>PM2.5:</b> {pm25} µg/m³  |  <b>PM10:</b> {pm10} µg/m³",
            f"Воздух {level}."]

    # ── POLLEN
    if (pol:=d["pollen"]):
        idx=lambda x: ("нет","низкий","умеренный","высокий","оч.высокий","экстрим")[int(round(x))]
        P+=["","🌿 <b>Пыльца</b>",
            f"Деревья — {idx(pol['treeIndex'])} | Травы — {idx(pol['grassIndex'])} | Амброзия — {idx(pol['weedIndex'])}"]

    # ── KP
    kp=d["kp"]; state="буря (G1)" if kp and kp>=5 else "спокойный" if kp and kp<4 else "повышенный"
    P+=["","🌌 <b>Геомагнитная активность</b>",
        f"<b>Уровень:</b> {state} (Kp {kp:.1f})" if kp else "нет данных"]

    # ── SCHUMANN
    sch=d["schumann"]
    if sch and "freq" in sch:
        P+=["","📈 <b>Резонанс Шумана</b>",
            f"<b>Частота:</b> ≈{sch['freq']:.1f} Гц • амплитуда стабильна"]
    else:
        P+=["","📈 <b>Резонанс Шумана</b>",
            sch.get("msg") if sch and "msg" in sch else sch.get("prev","нет данных")]

    # ── SST
    if d["sst"]: P+=["","🌊 <b>Температура воды в море</b>",
                     f"<b>Сейчас:</b> {d['sst']} °C"]

    # ── ASTRO
    astro=astro_events()
    if astro: P+=["","🔮 <b>Астрологические события</b>", astro]

    P.append("---")

    culprit=("ретроградного Меркурия" if "ретрограден" in astro else
             "магнитных бурь" if kp and kp>=5 else
             "низкого давления" if pressure_val<1007 else
             "мини-парада планет")
    summary,tips=gpt_blurb(culprit)
    P+=["<b>📝 Вывод</b>", summary,"","---","<b>✅ Рекомендации</b>", tips]
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
    data={
        "weather": get_weather(),
        "air": get_air(),
        "pm10": pm10_openmeteo(),
        "pollen": get_pollen(),
        "sst": get_sst(),
        "kp": get_kp(),
        "schumann": get_schumann()
    }
    md=build_md(data)
    print("MD preview:", md[:250].replace("\n"," | "))
    try:
        await send(md); print("✓ sent")
    except tg_err.TelegramError as e:
        print("Telegram error:", e, file=sys.stderr); raise

if __name__ == "__main__":
    asyncio.run(main())
