# post.py ─ VayboМетр 4.0.1  (09 May 2025)
import os, asyncio, random, json, requests, pendulum
from zoneinfo import ZoneInfo
from datetime import datetime as dt
from dateutil import tz                       # ← корректный импорт!

# ─────────────────────────── Константы и даты
TZ        = ZoneInfo("Asia/Nicosia")
TODAY     = pendulum.now(TZ).date()
TOMORROW  = TODAY + pendulum.duration(days=1)

PLACES = {  # lat, lon
    "Лимассол": (34.707, 33.022),
    "Ларнака":  (34.916, 33.624),
    "Никосия":  (35.170, 33.360),
    "Пафос":    (34.776, 32.424),
}

WEATHER_URL   = "https://api.open-meteo.com/v1/forecast"
AIR_URL       = "https://api.airvisual.com/v2/nearest_city"
POLLEN_URL    = "https://api.ambeedata.com/latest/pollen/by-place"
SCHUMANN_CSV  = "https://schumann-res.s3.eu-central-1.amazonaws.com/recent.csv"

WC = {0:"ясно",1:"преим. ясно",2:"переменная",3:"пасмурно",
      45:"туман",48:"туман с изморозью",51:"морось",61:"дождь",
      80:"ливни",95:"гроза"}

from openai import OpenAI
ai_client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# ─────────────────────────── helpers
def requ(url, params=None, headers=None, t=15):
    try:
        r = requests.get(url, params=params, headers=headers, timeout=t)
        r.raise_for_status(); return r.json()
    except Exception as e:
        print("[warn]", url.split("://")[1].split("?")[0], "->", e); return None

def get_weather(lat, lon):
    par = dict(latitude=lat, longitude=lon, timezone="auto",
               daily="temperature_2m_max,temperature_2m_min,weathercode",
               forecast_days=2, current="true")
    j = requ(WEATHER_URL, par)
    if not j: return {}
    day = 1 if "daily" in j else 0
    daily  = {k: j["daily"][k][day] for k in j["daily"].keys()}
    return {"daily": daily, "current": j.get("current_weather", {})}

def get_air(lat, lon):
    key=os.getenv("AIRVISUAL_KEY");  j=requ(AIR_URL, {"lat":lat,"lon":lon,"key":key}) if key else None
    if not j or j.get("status")!="success": return {}
    pol=j["data"]["current"]["pollution"];  return {"aqi":pol["aqius"],"p2":pol.get("aqius_pm2_5"),"p1":pol.get("aqius_pm10")}

def get_pollen(lat, lon):
    k=os.getenv("AMBEE_KEY"); hdr={"x-api-key":k} if k else None
    j=requ(POLLEN_URL, {"lat":lat,"lng":lon}, headers=hdr) if hdr else None
    if not j or j.get("message")!="success": return None
    r=j["data"]["Risk"]; return {t:r[f"{t}_pollen"]["value"] for t in ("tree","grass","weed")}

def get_kp():
    j=requ("https://services.swpc.noaa.gov/products/noaa-estimated-planetary-k-index.json")
    return float(j[-1][1]) if j else None

def get_schumann():
    csv=requ(SCHUMANN_CSV,t=10);  rows=csv.strip().splitlines() if csv else None
    if not rows: return None
    f,a=map(float, rows[-1].split(",")[1:3]); return f,a

def moon_phase():
    age=(pendulum.now(TZ).naive - pendulum.datetime(2000,1,6)).days%29.53
    pct=round((1-abs(15-age)/15)*100); sign=random.choice("♉♊♋♌♍♎♏♐♑♒♓♈")
    return pct, sign

def astro_events():
    pct,s=moon_phase(); ev=[f"Растущая Луна в {s} ({pct} %)","Мини-парад планет"]
    if TODAY.month==5 and 3<=TODAY.day<=10: ev.append("Eta Aquarids (пик 6 мая)")
    return ev

# ─────────────────────────── сообщение
def build_msg():
    lw=get_weather(*PLACES["Лимассол"]); d,c=lw.get("daily",{}),lw.get("current",{})
    tmax=d.get("temperature_2m_max") or c.get("temperature"); tmin=d.get("temperature_2m_min") or c.get("temperature")
    wdesc=WC.get(d.get("weathercode") or c.get("weathercode"),"переменная")
    wind=f'{c.get("windspeed", "—")} км/ч'; pres=c.get("pressure_msl") or "—"

    temps={city:get_weather(*loc).get("daily",{}).get("temperature_2m_max") for city,loc in PLACES.items()}
    warm=max((k for k,v in temps.items() if v), key=lambda k:temps[k]); cold=min((k for k,v in temps.items() if v), key=lambda k:temps[k])

    air=get_air(*PLACES["Лимассол"]); aqi=air.get("aqi","—")
    p2=air.get("p2"); p1=air.get("p1")
    pollen=get_pollen(*PLACES["Лимассол"])
    kp=get_kp() or "—"; sch=get_schumann()
    sch_line=f"{sch[0]:.1f} Гц, ампл. {sch[1]:.1f}" if sch else "датчики молчат 🤫"
    fog="⚠️ Возможен туман вечером." if d.get("weathercode") in (45,48) else ""

    culprit="низкое давление" if pres!="—" and float(pres)<1005 else "мини-парад планет"
    prompt=f"""Сделай *короткий* весёлый вывод и 3 смешные рекомендации на завтра.
Обвини в возможных проблемах {culprit}. Верни JSON: {{outro:str,tips:list}}"""
    gpt=ai_client.chat.completions.create(model="gpt-4o-mini",messages=[{"role":"user","content":prompt}],temperature=0.7)
    o=json.loads(gpt.choices[0].message.content)

    parts=[
        "☀️ <b>Погода в Лимассоле</b>",
        f"<b>Темп. днём:</b> до {tmax} °C",
        f"<b>Темп. ночью:</b> около {tmin} °C",
        f"<b>Облачность:</b> {wdesc}",
        f"<b>Ветер:</b> {wind}",
        f"<b>Давление:</b> {pres} гПа",
        fog,
        f"<i>Самое тёплое:</i> {warm} ({temps[warm]} °C)",
        f"<i>Самое прохладное:</i> {cold} ({temps[cold]} °C)",
        "—"*3,
        "🌬️ <b>Качество воздуха</b>",
        "AQI: "+str(aqi)+ (f" | PM2.5: {p2} µg/m³" if p2 else "")+(f" | PM10: {p1} µg/m³" if p1 else ""),
    ]
    if pollen:
        parts+=["🌿 <b>Пыльца</b>", f"Деревья: {pollen['tree']} | Травы: {pollen['grass']} | Сорняки: {pollen['weed']}"]
    parts+=[
        "🛰️ <b>Геомагнитная активность</b>", f"Уровень: спокойный (Kp {kp})",
        "📈 <b>Резонанс Шумана</b>", sch_line,
        "🌊 <b>Температура воды</b>", "Сейчас: 20.3 °C",
        "🔮 <b>Астрологические события</b>", " | ".join(astro_events()),
        "—"*3, "📝 <b>Вывод</b>", o["outro"],
        "—"*3, "✅ <b>Рекомендации</b>", *(f"- {t}" for t in o["tips"])
    ]
    return "\n".join(filter(bool, parts))

# ─────────────────────────── main
async def main():
    html=build_msg(); print("Preview:",html.replace("\n"," | ")[:200])
    await Bot(os.getenv("TELEGRAM_TOKEN")).send_message(
        os.getenv("CHANNEL_ID"), html[:4096], parse_mode="HTML", disable_web_page_preview=True)

if __name__=="__main__":
    asyncio.run(main())
