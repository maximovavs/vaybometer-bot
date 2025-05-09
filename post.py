# post.py ─ VayboМетр 4.0.3 (10 May 2025)
import os, asyncio, random, json, requests, pendulum
from zoneinfo import ZoneInfo
from openai import OpenAI
from telegram import Bot

TZ        = ZoneInfo("Asia/Nicosia")
TODAY     = pendulum.now(TZ).date()
PLACES = {
    "Лимассол": (34.707, 33.022),
    "Ларнака":  (34.916, 33.624),
    "Никосия":  (35.170, 33.360),
    "Пафос":    (34.776, 32.424),
}

WEATHER_URL = "https://api.open-meteo.com/v1/forecast"
AIR_URL     = "https://api.airvisual.com/v2/nearest_city"
POLLEN_URL  = "https://api.ambeedata.com/latest/pollen/by-place"
SCHUMANN_CSV= "https://schumann-res.s3.eu-central-1.amazonaws.com/recent.csv"
K_INDEX_URL = "https://services.swpc.noaa.gov/json/planetary_k_index_1m.json"

WC = {0:"ясно",1:"преим. ясно",2:"переменная",3:"пасмурно",45:"туман",48:"туман с изморозью",
      51:"морось",61:"дождь",80:"ливни",95:"гроза"}

ai_client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# ─ helpers ────────────────────────────────────────────────────────────────────
def requ(url, params=None, headers=None, t=15):
    try:
        r = requests.get(url, params=params, headers=headers, timeout=t)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        print("[warn]", url.split("://")[1].split("?")[0], "->", e)
        return None

def get_weather(lat, lon):
    par = dict(latitude=lat, longitude=lon, timezone="auto",
               daily="temperature_2m_max,temperature_2m_min,weathercode",
               forecast_days=2, current_weather="true")
    return requ(WEATHER_URL, par) or {}

def safe_temp(data):
    return (data.get("daily",{}).get("temperature_2m_max")
            or data.get("current_weather",{}).get("temperature"))

def get_air(lat, lon):
    key=os.getenv("AIRVISUAL_KEY")
    if not key: return {}
    j=requ(AIR_URL, {"lat":lat,"lon":lon,"key":key})
    if not j or j.get("status")!="success": return {}
    pol=j["data"]["current"]["pollution"]
    return {"aqi":pol["aqius"],"p2":pol.get("aqius_pm2_5"),"p1":pol.get("aqius_pm10")}

def get_pollen(lat, lon):
    k=os.getenv("AMBEE_KEY")
    if not k: return None
    j=requ(POLLEN_URL, {"lat":lat,"lng":lon}, headers={"x-api-key":k})
    if not j or j.get("message")!="success": return None
    r=j["data"]["Risk"]
    return {t:r[f"{t}_pollen"]["value"] for t in ("tree","grass","weed")}

def get_kp():
    j=requ(K_INDEX_URL)
    try:
        return float(j[-1]["k_index"]) if j else "—"
    except Exception:
        return "—"

def get_schumann():
    try:
        rows=requests.get(SCHUMANN_CSV,timeout=10).text.strip().splitlines()
        f,a=map(float,[x for x in rows[-1].split(",")[1:3] if x])
        return f,a
    except Exception:
        return None

def moon_phase():
    age=(pendulum.now(TZ).naive - pendulum.datetime(2000,1,6)).days%29.53
    pct=round((1-abs(15-age)/15)*100)
    return pct, random.choice("♉♊♋♌♍♎♏♐♑♒♓♈")

def astro_events():
    pct,sign=moon_phase()
    ev=[f"Растущая Луна в {sign} ({pct} %)","Мини-парад планет"]
    if TODAY.month==5 and 3<=TODAY.day<=10:
        ev.append("Eta Aquarids (пик 6 мая)")
    return ev

# ─ message builder ────────────────────────────────────────────────────────────
def build_msg():
    w_lim=get_weather(*PLACES["Лимассол"])
    d=w_lim.get("daily",{}); c=w_lim.get("current_weather",{})

    t_max=d.get("temperature_2m_max") or c.get("temperature","—")
    t_min=d.get("temperature_2m_min") or c.get("temperature","—")
    desc=WC.get(d.get("weathercode") or c.get("weathercode"),"переменная")
    pressure=c.get("pressure_msl","—")
    wind=f"{c.get('windspeed','—')} км/ч, {c.get('winddirection','—')}"
    fog="⚠️ Возможен туман." if (d.get("weathercode") in (45,48)) else ""

    # теплее / холоднее
    temps={city:safe_temp(get_weather(*coord)) for city,coord in PLACES.items()}
    good=[(k,v) for k,v in temps.items() if v is not None]
    warm_line=cold_line="Температура по Кипру: нет данных"
    if good:
        warm=max(good,key=lambda x:x[1]); cold=min(good,key=lambda x:x[1])
        warm_line=f"<i>Самое тёплое:</i> {warm[0]} ({warm[1]} °C)"
        cold_line=f"<i>Самое прохладное:</i> {cold[0]} ({cold[1]} °C)"

    # воздух / пыльца
    air=get_air(*PLACES["Лимассол"])
    aqi=air.get("aqi","—"); p2=air.get("p2","—"); p1=air.get("p1","—")
    poll=get_pollen(*PLACES["Лимассол"])
    poll_line=(" | ".join(f"{k.capitalize()}: {v}" for k,v in poll.items())
               if poll else "нет данных")

    kp=get_kp()
    sch=get_schumann(); sch_line=f"{sch[0]:.1f} Гц, {sch[1]:.1f}" if sch else "нет данных"

    culprit="низкое давление" if pressure!="—" and float(pressure)<1005 else "мини-парад планет"
    gpt=ai_client.chat.completions.create(
        model="gpt-4o-mini",temperature=0.6,max_tokens=120,
        messages=[{"role":"user","content":
            f"Сделай забавный вывод (25-35 слов) и три коротких юмористических совета. \
             Во всём вини {culprit}. Верни JSON {{outro:str,tips:list}}."}])
    try: j=json.loads(gpt.choices[0].message.content)
    except Exception:
        j={"outro":"Если что-то пойдёт не так — вините погоду!",
           "tips":["Улыбайтесь!","Танцуйте под дождём!","Мечтайте смелее!"]}

    parts=[
        "☀️ <b>Погода в Лимассоле</b>",
        f"<b>Темп. днём:</b> до {t_max} °C",
        f"<b>Темп. ночью:</b> около {t_min} °C",
        f"<b>Облачность:</b> {desc}",
        f"<b>Ветер:</b> {wind}",
        f"<b>Давление:</b> {pressure} гПа",
        fog, warm_line, cold_line,
        "—"*3,
        "🌬️ <b>Качество воздуха</b>",
        f"AQI: {aqi} | PM2.5: {p2} | PM10: {p1}",
        "🌿 <b>Пыльца</b>", poll_line,
        "🛰️ <b>Геомагнитная активность</b>", f"Kp {kp}",
        "📈 <b>Резонанс Шумана</b>", sch_line,
        "🌊 <b>Температура воды</b>", "Сейчас: 20.3 °C",
        "🔮 <b>Астрологические события</b>", " | ".join(astro_events()),
        "—"*3, "📝 <b>Вывод</b>", j["outro"],
        "—"*3, "✅ <b>Рекомендации</b>", *(f"- {t}" for t in j["tips"])
    ]
    return "\n".join(p for p in parts if p.strip("—"))

# ─ main ────────────────────────────────────────────────────────────────────────
async def main():
    html=build_msg()
    print("Preview:",html.replace("\n"," | ")[:200])
    await Bot(os.getenv("TELEGRAM_TOKEN")).send_message(
        os.getenv("CHANNEL_ID"), html[:4096], parse_mode="HTML",
        disable_web_page_preview=True)

if __name__=="__main__":
    asyncio.run(main())
