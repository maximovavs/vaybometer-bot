#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
VayboМетр Limassol v6.2 — вечерний дайджест (21:00 Asia/Nicosia, прогноз на завтра)

Требуются secrets:
OPENAI_API_KEY, TELEGRAM_TOKEN, CHANNEL_ID,
OWM_KEY, AIRVISUAL_KEY, AMBEE_KEY (optional), COPERNICUS_USER/PASS (optional).
"""

import os, sys, math, asyncio
from datetime import datetime, timedelta, timezone
import requests, pendulum
from statistics import mean

# ──────────────────────────  Настройки  ──────────────────────────────────
TZ      = pendulum.timezone("Asia/Nicosia")
BOT_KEY = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("CHANNEL_ID")
OPENAI  = os.getenv("OPENAI_API_KEY")
AIR_KEY = os.getenv("AIRVISUAL_KEY")
AMBEE   = os.getenv("AMBEE_KEY")

CITIES = {
    "Лимассол": (34.707, 33.022),
    "Ларнака":  (34.916, 33.624),
    "Никосия":  (35.17,  33.36),
    "Пафос":    (34.776, 32.424),
}

S = requests.Session()
S.headers["User-Agent"] = "VayboMeter/6.2 (+github)"

def j(url, **p):
    try:
        r = S.get(url, params=p, timeout=20); r.raise_for_status(); return r.json()
    except Exception as e:
        print("[warn]", url.split('//')[1].split('?')[0], "->", e, file=sys.stderr); return None

# ─── Open-Meteo прогноз ЗАВТРА ───────────────────────────────────────────
def forecast(lat, lon):
    data = j("https://api.open-meteo.com/v1/forecast",
             latitude=lat, longitude=lon, timezone="auto",
             forecast_days=2,  # сегодня + завтра
             daily="temperature_2m_max,temperature_2m_min,weathercode,pressure_msl")
    if not data or "daily" not in data: return None
    d = data["daily"]                    # завтра → индекс 1
    return dict(t_max=d["temperature_2m_max"][1],
                t_min=d["temperature_2m_min"][1],
                wcode=d["weathercode"][1],
                press=d["pressure_msl"][1])

def mean_t(lat, lon):
    f = forecast(lat, lon)
    return None if not f else (f["t_max"] + f["t_min"]) / 2

def fog_risk(code): return code in (45, 48)

# ─── Доп.-данные ─────────────────────────────────────────────────────────
def aqi(lat, lon):
    if not AIR_KEY: return None
    return j("https://api.airvisual.com/v2/nearest_city",
             lat=lat, lon=lon, key=AIR_KEY)

def pollen(lat, lon):
    if not AMBEE: return None
    h={"x-api-key":AMBEE}; url="https://api.ambeedata.com/latest/pollen/by-lat-lng"
    try:
        r=S.get(url,headers=h,params=dict(lat=lat,lng=lon),timeout=20); r.raise_for_status(); return r.json()
    except: return None

def kp_now():
    k=j("https://services.swpc.noaa.gov/json/planetary_k_index_1m.json")
    try: return float(k[-1]["kp_index"])
    except: return None

def schumann():
    y=(datetime.utcnow()-timedelta(days=1)).strftime("%Y%m%d")
    url=f"https://data.glcoherence.org/gci{y}.csv"
    try:
        txt=S.get(url,timeout=20).text.splitlines()[1:]
        vals=[float(r.split(',')[1]) for r in txt if r]
        return {"freq":mean(vals)}
    except: return {"msg":"нет данных"}

def sst(lat, lon):
    m=j("https://marine-api.open-meteo.com/v1/marine",
        latitude=lat, longitude=lon,
        hourly="sea_surface_temperature",
        start_date=pendulum.today(TZ).to_date_string(),
        end_date=pendulum.today(TZ).to_date_string(),
        timezone="auto")
    try: return round(mean(m["hourly"]["sea_surface_temperature"]),1)
    except: return None

# ─── Астрология (фаза Луны + трин Венера/Юпитер или парад) ───────────────
def astro():
    try:
        import swisseph as swe
        jd=swe.julday(*datetime.utcnow().timetuple()[:3])
        phase = ((swe.calc_ut(jd,swe.MOON)[0][0]-swe.calc_ut(jd,swe.SUN)[0][0]+360)%360)/360
        lune = ("Новолуние","Растущая","Полнолуние","Убывающая")[int(phase*4)%4]
        v,j=swe.calc_ut(jd,swe.VENUS)[0][0],swe.calc_ut(jd,swe.JUPITER)[0][0]
        diff=abs((v-j+180)%360-180)
        extra="Трин Венеры и Юпитера — волна удачи" if diff<4 else "Мини-парад планет"
        return f"{lune} Луна | {extra}"
    except: return "нет данных"

# ─── GPT-шутка + советы ──────────────────────────────────────────────────
def gpt(culprit):
    import openai, random
    client=openai.OpenAI(api_key=OPENAI)
    prompt=(f"Одной строкой: 'Если завтра что-то пойдёт не так, вините {culprit}.' "
            "Добавь позитив ≤12 слов. Затем пустая строка и 3 весёлых bullet-совета, ≤12 слов.")
    txt=client.chat.completions.create(model="gpt-4o-mini",temperature=0.6,
        messages=[{"role":"user","content":prompt}]).choices[0].message.content.strip().splitlines()
    lines=[l.strip() for l in txt if l.strip()]
    summary=lines[0]; tips="\n".join(f"- {l.lstrip('-• ').strip()}" for l in lines[1:4])
    return summary,tips

# ─── Формируем HTML-сообщение ────────────────────────────────────────────
def build_msg():
    base = forecast(*CITIES["Лимассол"]) or {}
    fog  = fog_risk(base.get("wcode",0))

    temps={ct:mean_t(*loc) for ct,loc in CITIES.items()}
    valid={k:v for k,v in temps.items() if v is not None}
    hot=max(valid,key=valid.get) if valid else "—"
    cold=min(valid,key=valid.get) if valid else "—"

    P=[]; A=P.append
    A("☀️ <b>Погода завтра</b>")
    A(f"<b>Темп. днём:</b> до {base.get('t_max','—')} °C")
    A(f"<b>Темп. ночью:</b> около {base.get('t_min','—')} °C")
    A(f"Самое тёплое: {hot} ({valid.get(hot,'—'):.1f} °C)" if hot!="—" else "Самое тёплое: —")
    A(f"Самое прохладное: {cold} ({valid.get(cold,'—'):.1f} °C)" if cold!="—" else "Самое прохладное: —")
    if fog: A("Возможен туман 🌫️ (>40 % часов)")

    # ── AQI ──
    air=aqi(*CITIES["Лимассол"])
    if air:
        p=air["data"]["current"]["pollution"]
        pm10 = p.get("p1") or "нет данных"
        A(""); A("🌬️ <b>Качество воздуха</b>")
        A(f"<b>AQI:</b> {p['aqius']} | <b>PM2.5:</b> {p.get('p2','—')} µg/m³ | <b>PM10:</b> {pm10}")

    else: A(""); A("🌬️ <b>Качество воздуха</b>"); A("нет данных")

    # ── Пыльца ──
    pol=pollen(*CITIES["Лимассол"])
    if pol:
        v=pol["data"][0]
        idx=lambda n:["нет","низкая","умер","высок","экстрим"][int(round(n))]
        A(""); A("🌿 <b>Пыльца</b>")
        A(f"Деревья — {idx(v['tree_pollen'])} | Травы — {idx(v['grass_pollen'])} | Амброзия — {idx(v['weed_pollen'])}")
    else: A(""); A("🌿 <b>Пыльца</b>"); A("нет данных")

    # ── КП ──
    kp=kp_now()
    if kp is not None:
        state="буря (G1)" if kp>=5 else "повышенный" if kp>=4 else "спокойный"
        A(""); A("🌌 <b>Геомагнитная активность</b>"); A(f"<b>Уровень:</b> {state} (Kp {kp})")
    else: A(""); A("🌌 <b>Геомагнитная активность</b>"); A("нет данных")

    # ── Шуман ──
    sch=schumann()
    A(""); A("📈 <b>Резонанс Шумана</b>")
    A(f"{sch['freq']:.1f} Гц, амплитуда стабильна" if "freq" in sch else sch["msg"])

    # ── SST ──
    sea=sst(*CITIES["Лимассол"])
    A(""); A("🌊 <b>Температура воды в море</b>")
    A(f"<b>Сейчас:</b> {sea} °C" if sea else "нет данных")

    # ── Астрология ──
    A(""); A("🔮 <b>Астрологические события</b>"); A(astro())

    # ── Вывод + рекомендации ──
    A("---")
    culprit = "туман" if fog else \
              "низкое давление" if base.get("press",1013)<1005 else \
              ("магнитные бури" if kp and kp>=5 else "мини-парад планет")
    summary,tips = gpt(culprit)
    A("📝 <b>Вывод</b>"); A(summary); A("")
    A("✅ <b>Рекомендации</b>"); A(tips)

    return "\n".join(P)

# ────────────────────────── Telegram ─────────────────────────────────────
from telegram import Bot, error as tg_err
async def send(msg):
    await Bot(BOT_KEY).send_message(CHAT_ID, msg[:4096], parse_mode="HTML",
                                    disable_web_page_preview=True)

async def main():
    html=build_msg()
    print("Preview:",html.replace("\n"," | ")[:200])
    await send(html)

if __name__ == "__main__":
    asyncio.run(main())
