#!/usr/bin/env python3
# VayboМетр 4.0.6  · 10 May 2025
# — полностью самодостаточный post.py —

import os, asyncio, json, random, requests, pendulum
from zoneinfo import ZoneInfo
from telegram import Bot
from openai import OpenAI

# ────────────────────────────────────────
TZ    = ZoneInfo("Asia/Nicosia")
TODAY = pendulum.now(TZ).date()

PLACES = {
    "Лимассол": (34.707, 33.022),
    "Ларнака":  (34.916, 33.624),
    "Никосия":  (35.170, 33.360),
    "Пафос":    (34.776, 32.424),
}

WEATHER_URL  = "https://api.open-meteo.com/v1/forecast"
AIR_URL      = "https://api.airvisual.com/v2/nearest_city"
POLLEN_URL   = "https://api.ambeedata.com/latest/pollen/by-place"
SCHUMANN_CSV = "https://schumann-res.s3.eu-central-1.amazonaws.com/recent.csv"
K_INDEX_URL  = "https://services.swpc.noaa.gov/json/planetary_k_index_1m.json"

WC = {0:"ясно",1:"преим. ясно",2:"переменная",3:"пасмурно",
      45:"туман",48:"туман с изморозью",51:"морось",61:"дождь",
      80:"ливни",95:"гроза"}

ai_client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# ─ helpers ──────────────────────────────
def requ(url, params=None, headers=None, t=15):
    try:
        r = requests.get(url, params=params, headers=headers, timeout=t)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        print("[warn]", url.split("://")[1].split("?")[0], "->", e)
        return None

def get_weather(lat, lon):
    """Возвращает daily-скаляры (завтра → idx=1, иначе сегодня)."""
    par = dict(latitude=lat, longitude=lon, timezone="auto",
               daily="temperature_2m_max,temperature_2m_min,weathercode",
               forecast_days=2, current_weather="true")
    j = requ(WEATHER_URL, par)
    if not j: return {}
    idx  = 1 if len(j["daily"]["time"]) > 1 else 0
    daily = {k: j["daily"][k][idx] for k in j["daily"] if k != "time"}
    return {"daily": daily, "current_weather": j.get("current_weather", {})}

def safe_temp(w):  # Tmax или текущая T
    return w.get("daily", {}).get("temperature_2m_max") \
        or w.get("current_weather", {}).get("temperature")

def get_air(lat, lon):
    key=os.getenv("AIRVISUAL_KEY")
    j = requ(AIR_URL, {"lat":lat,"lon":lon,"key":key}) if key else None
    if not j or j.get("status")!="success": return {}
    pol=j["data"]["current"]["pollution"]
    return {"aqi":pol["aqius"],"p2":pol.get("aqius_pm2_5"),"p1":pol.get("aqius_pm10")}

def get_pollen(lat, lon):
    k=os.getenv("AMBEE_KEY")
    j=requ(POLLEN_URL, {"lat":lat,"lng":lon}, headers={"x-api-key":k}) if k else None
    if not j or j.get("message")!="success": return None
    r=j["data"]["Risk"]; return {t:r[f"{t}_pollen"]["value"] for t in ("tree","grass","weed")}

def get_kp():
    j=requ(K_INDEX_URL)
    try: return float(j[-1]["k_index"]) if j else "—"
    except: return "—"

def get_schumann():
    try:
        rows=requests.get(SCHUMANN_CSV, timeout=10).text.strip().splitlines()
        vals=[x for x in rows[-1].split(",")[1:3] if x]
        return tuple(map(float, vals)) if len(vals)==2 else None
    except: return None

def moon_phase():
    # обе даты naive → никакого timezone-конфликта
    days = (TODAY - pendulum.date(2000, 1, 6)).days
    age  = days % 29.53
    pct  = round((1 - abs(15 - age) / 15) * 100)
    return pct, random.choice("♉♊♋♌♍♎♏♐♑♒♓♈")

def astro_events():
    pct, sign = moon_phase()
    ev=[f"Растущая Луна в {sign} ({pct} %)", "Мини-парад планет"]
    if TODAY.month==5 and 3<=TODAY.day<=10: ev.append("Eta Aquarids (пик 6 мая)")
    return ev

# ─ message builder ───────────────────────
def build_msg():
    w = get_weather(*PLACES["Лимассол"])
    d, c = w.get("daily", {}), w.get("current_weather", {})

    t_max = d.get("temperature_2m_max") or c.get("temperature", "—")
    t_min = d.get("temperature_2m_min") or c.get("temperature", "—")
    desc  = WC.get(d.get("weathercode") or c.get("weathercode"), "переменная")
    pressure = c.get("pressure_msl", "—")
    wind = f"{c.get('windspeed','—')} км/ч, {c.get('winddirection','—')}"
    fog  = "⚠️ Возможен туман." if d.get("weathercode")==45 else ""

    temps={city:safe_temp(get_weather(*loc)) for city,loc in PLACES.items()}
    good=[(k,v) for k,v in temps.items() if v is not None]
    warm_line=cold_line=""
    if good:
        warm=max(good,key=lambda x:x[1]); cold=min(good,key=lambda x:x[1])
        warm_line=f"<i>Самое тёплое:</i> {warm[0]} ({warm[1]} °C)"
        cold_line=f"<i>Самое прохладное:</i> {cold[0]} ({cold[1]} °C)"

    air  = get_air(*PLACES["Лимассол"])
    aqi,p2,p1 = air.get("aqi","—"), air.get("p2","—"), air.get("p1","—")
    pollen = get_pollen(*PLACES["Лимассол"])
    poll_line = (" | ".join(f"{k.capitalize()}: {v}" for k,v in pollen.items())
                 if pollen else "нет данных")

    kp   = get_kp()
    sch  = get_schumann()
    sch_line = f"{sch[0]:.1f} Гц, {sch[1]:.1f}" if sch else "нет данных"

    culprit = "низкого давления" if pressure!="—" and float(pressure)<1005 else "мини-парада планет"
    prompt  = f"Сделай короткий весёлый вывод (≤35 слов) и 3 смешных совета. Виноват {culprit}. Верни JSON {{outro:str,tips:list}}."
    try:
        gpt = ai_client.chat.completions.create(model="gpt-4o-mini",
            messages=[{"role":"user","content":prompt}],temperature=0.6,max_tokens=120)
        j = json.loads(gpt.choices[0].message.content)
    except Exception:
        j={"outro":"Если завтра что-то пойдёт не так — вините погоду!",
           "tips":["Улыбайтесь!","Танцуйте под дождём!","Мечтайте смелее!"]}

    parts = [
        "☀️ <b>Погода в Лимассоле</b>",
        f"<b>Темп. днём:</b> {t_max} °C",
        f"<b>Темп. ночью:</b> {t_min} °C",
        f"<b>Облачность:</b> {desc}", f"<b>Ветер:</b> {wind}",
        f"<b>Давление:</b> {pressure} гПа", fog, warm_line, cold_line,
        "—"*3,
        "🌬️ <b>Качество воздуха</b>",
        f"AQI: {aqi} | PM2.5: {p2} | PM10: {p1}",
        "🌿 <b>Пыльца</b>", poll_line,
        "🛰️ <b>Геомагнитная активность</b>", f"Kp {kp}",
        "📈 <b>Резонанс Шумана</b>", sch_line,
        "🌊 <b>Температура воды</b>", "Сейчас: 20.3 °C",
        "🔮 <b>Астрологические события</b>", " | ".join(astro_events()),
        "—"*3, "📝 <b>Вывод</b>", j["outro"],
        "—"*3, "✅ <b>Рекомендации</b>", *(f"- {tip}" for tip in j["tips"])
    ]
    return "\n".join(p for p in parts if p)

# ─ main ───────────────────────────────────
async def main():
    html = build_msg()
    print("Preview:", html.replace("\n"," | ")[:200])
    await Bot(os.getenv("TELEGRAM_TOKEN")).send_message(
        os.getenv("CHANNEL_ID"), html[:4096],
        parse_mode="HTML", disable_web_page_preview=True)

if __name__ == "__main__":
    asyncio.run(main())
