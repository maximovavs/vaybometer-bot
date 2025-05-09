#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
VayboМетр Limassol v6.3 — вечeрний дайджест (прогноз на завтра, fallback на сегодня/текущее)
"""

import os, sys, math, asyncio, requests, pendulum
from datetime import datetime, timedelta
from statistics import mean

# ───── настройки & секреты ───────────────────────────────────────────────
TZ   = pendulum.timezone("Asia/Nicosia")
BOT  = os.getenv("TELEGRAM_TOKEN")
CHAT = os.getenv("CHANNEL_ID")
OPENAI_KEY = os.getenv("OPENAI_API_KEY")
AIR_KEY    = os.getenv("AIRVISUAL_KEY")
AMBEE_KEY  = os.getenv("AMBEE_KEY")

CITIES = {
    "Лимассол": (34.707, 33.022),
    "Ларнака":  (34.916, 33.624),
    "Никосия":  (35.170, 33.360),
    "Пафос":    (34.776, 32.424),
}

S = requests.Session(); S.headers["User-Agent"] = "VayboMeter/6.3 (+github)"

def j(url, **p):
    try:
        r=S.get(url,params=p,timeout=20); r.raise_for_status(); return r.json()
    except Exception as e:
        print("[warn]", url.split('//')[1].split('?')[0], "->", e, file=sys.stderr); return None

# ───── прогноз+fallback ──────────────────────────────────────────────────
def _forecast_block(lat, lon):
    """Возвращает dict: t_max, t_min, wcode, press   — или None"""
    base = j("https://api.open-meteo.com/v1/forecast",
             latitude=lat, longitude=lon, timezone="auto",
             forecast_days=2,
             daily="temperature_2m_max,temperature_2m_min,weathercode,pressure_msl")
    if base and "daily" in base:
        d = base["daily"]
        try:               # завтра (index 1)
            return dict(t_max=d["temperature_2m_max"][1],
                        t_min=d["temperature_2m_min"][1],
                        wcode=d["weathercode"][1],
                        press=d["pressure_msl"][1])
        except IndexError:
            # сегодня (index 0)
            return dict(t_max=d["temperature_2m_max"][0],
                        t_min=d["temperature_2m_min"][0],
                        wcode=d["weathercode"][0],
                        press=d["pressure_msl"][0])

    # fallback: текущая погода
    cur = j("https://api.open-meteo.com/v1/forecast",
            latitude=lat, longitude=lon,
            timezone="auto", current_weather=True)
    if cur and "current_weather" in cur:
        t = cur["current_weather"]["temperature"]
        p = cur["current_weather"].get("surface_pressure",
                                       cur["current_weather"].get("pressure_msl", 1013))
        w = cur["current_weather"]["weathercode"]
        return dict(t_max=t, t_min=t, wcode=w, press=p)
    return None

def mean_temp(lat, lon):
    f=_forecast_block(lat, lon);  return None if not f else (f["t_max"]+f["t_min"])/2

def fog(code): return code in (45, 48)   # mist/fog codes

# ───── AQI, пыльца, Kp, Шуман, SST (как раньше) ─────────────────────────
def aqi(lat,lon):
    return j("https://api.airvisual.com/v2/nearest_city",
             lat=lat, lon=lon, key=AIR_KEY) if AIR_KEY else None

def pollen(lat,lon):
    if not AMBEE_KEY: return None
    h={"x-api-key":AMBEE_KEY}
    try:
        r=S.get("https://api.ambeedata.com/latest/pollen/by-lat-lng",
                headers=h, params=dict(lat=lat,lng=lon), timeout=20)
        r.raise_for_status(); return r.json()
    except: return None

def kp_now():
    k=j("https://services.swpc.noaa.gov/json/planetary_k_index_1m.json")
    try: return float(k[-1]["kp_index"])
    except: return None

def schumann():
    y=(datetime.utcnow()-timedelta(days=1)).strftime("%Y%m%d")
    try:
        txt=S.get(f"https://data.glcoherence.org/gci{y}.csv",timeout=20).text.splitlines()[1:]
        return {"freq":mean(float(r.split(',')[1]) for r in txt if r)}
    except: return {"msg":"нет данных"}

def sst(lat,lon):
    m=j("https://marine-api.open-meteo.com/v1/marine",
        latitude=lat, longitude=lon,
        hourly="sea_surface_temperature",
        start_date=pendulum.today(TZ).to_date_string(),
        end_date=pendulum.today(TZ).to_date_string(),
        timezone="auto")
    try: return round(mean(m["hourly"]["sea_surface_temperature"]),1)
    except: return None

# ───── Астрология (фаза луны + трин)  ────────────────────────────────────
def astro():
    try:
        import swisseph as swe
        jd=swe.julday(*datetime.utcnow().timetuple()[:3])
        phase=((swe.calc_ut(jd,swe.MOON)[0][0]-swe.calc_ut(jd,swe.SUN)[0][0]+360)%360)/360
        phase_t=("Новолуние","Растущая","Полнолуние","Убывающая")[int(phase*4)%4]
        v,j=swe.calc_ut(jd,swe.VENUS)[0][0],swe.calc_ut(jd,swe.JUPITER)[0][0]
        diff=abs((v-j+180)%360-180)
        extra="Трин Венеры и Юпитера — волна удачи" if diff<4 else "Мини-парад планет"
        return f"{phase_t} Луна | {extra}"
    except: return "нет данных"

# ───── GPT юмор ──────────────────────────────────────────────────────────
def gpt_line(culprit):
    import openai, random
    c=openai.OpenAI(api_key=OPENAI_KEY)
    p=(f"Одной строкой: 'Если завтра что-то пойдёт не так, вините {culprit}.' "
       "Добавь ≤12 слов позитива. Затем пустая строка и три весёлых bullet-совета ≤12 слов.")
    res=c.chat.completions.create(model="gpt-4o-mini",temperature=0.6,
         messages=[{"role":"user","content":p}]).choices[0].message.content.strip().splitlines()
    lines=[l.strip() for l in res if l.strip()]
    return lines[0], "\n".join(f"- {l.lstrip('-• ').strip()}" for l in lines[1:4])

# ───── HTML-сообщение ────────────────────────────────────────────────────
def build_msg():
    lim= _forecast_block(*CITIES["Лимассол"]) or {}
    fog_flag=fog(lim.get("wcode",0))

    temps={c:mean_temp(*loc) for c,loc in CITIES.items()}
    valid={k:v for k,v in temps.items() if v is not None}
    hot=max(valid,key=valid.get) if valid else "—"
    cold=min(valid,key=valid.get) if valid else "—"

    P=[]; A=P.append
    A("☀️ <b>Погода в Лимассоле</b>")
    A(f"<b>Темп. днём:</b> до {lim.get('t_max','—')} °C")
    A(f"<b>Темп. ночью:</b> около {lim.get('t_min','—')} °C")
    A(f"Самое тёплое: {hot} ({valid.get(hot,'—'):.1f} °C)" if hot!="—" else "Самое тёплое: —")
    A(f"Самое прохладное: {cold} ({valid.get(cold,'—'):.1f} °C)" if cold!="—" else "Самое прохладное: —")
    if fog_flag: A("Возможен туман 🌫️ (>40 % часов)")

    # — AQI —
    air=aqi(*CITIES["Лимассол"])
    if air:
        pol=air["data"]["current"]["pollution"]; pm10=pol.get("p1","—")
        A(""); A("🌬️ <b>Качество воздуха</b>")
        A(f"<b>AQI:</b> {pol['aqius']} | <b>PM2.5:</b> {pol.get('p2','—')} µg/m³ | <b>PM10:</b> {pm10}")
    else: A(""); A("🌬️ <b>Качество воздуха</b>"); A("нет данных")

    # — Пыльца —
    pol=pollen(*CITIES["Лимассол"])
    if pol:
        val=pol["data"][0]; scale=lambda v:["нет","низкая","умер","высок","экстрим"][int(round(v))]
        A(""); A("🌿 <b>Пыльца</b>")
        A(f"Деревья — {scale(val['tree_pollen'])} | Травы — {scale(val['grass_pollen'])} | "
          f"Амброзия — {scale(val['weed_pollen'])}")
    else: A(""); A("🌿 <b>Пыльца</b>"); A("нет данных")

    # — Геомагнитка —
    kp=kp_now()
    if kp is not None:
        level="буря (G1)" if kp>=5 else "повышенный" if kp>=4 else "спокойный"
        A(""); A("🌌 <b>Геомагнитная активность</b>"); A(f"<b>Уровень:</b> {level} (Kp {kp})")
    else: A(""); A("🌌 <b>Геомагнитная активность</b>"); A("нет данных")

    # — Шуман —
    sch=schumann()
    A(""); A("📈 <b>Резонанс Шумана</b>")
    A(f"{sch['freq']:.1f} Гц" if "freq" in sch else sch["msg"])

    # — SST —
    sea=sst(*CITIES["Лимассол"])
    A(""); A("🌊 <b>Температура воды в море</b>")
    A(f"<b>Сейчас:</b> {sea} °C" if sea else "нет данных")

    # — Астрология —
    A(""); A("🔮 <b>Астрологические события</b>"); A(astro())

    A("---")
    culprit=("туман" if fog_flag else
             "низкое давление" if lim.get("press",1013)<1005 else
             "магнитные бури" if kp and kp>=5 else
             "мини-парад планет")
    summary,tips=gpt_line(culprit)
    A("📝 <b>Вывод</b>"); A(summary); A(""); A("✅ <b>Рекомендации</b>"); A(tips)
    return "\n".join(P)

# ───── Telegram send ─────────────────────────────────────────────────────
from telegram import Bot
async def send(msg):
    await Bot(BOT_KEY).send_message(CHAT_ID, msg[:4096], parse_mode="HTML",
                                    disable_web_page_preview=True)

async def main():
    html=build_msg(); print("Preview:",html.replace("\n"," | ")[:200]); await send(html)

if __name__ == "__main__":
    asyncio.run(main())
