# post.py – VayboМетр v5 (09-05-2025)

import os, asyncio, json, math, random, csv, textwrap, datetime as dt
import requests, pendulum
from python_dateutil import tz
from telegram import Bot

# ────────────────────────────  постоянные  ────────────────────────────
TZ          = "Asia/Nicosia"
TODAY       = pendulum.now(TZ).date()
TOMORROW    = TODAY + pendulum.duration(days=1)
DATE_STR    = TOMORROW.format("DD.MM.YYYY")

LAT, LON    = 34.707, 33.022        # Limassol
CITIES      = {                     # для «самый тёплый / прохладный»
    "Лимассол": (34.707, 33.022),
    "Ларнака" : (34.916, 33.624),
    "Никосия" : (35.170, 33.360),
    "Пафос"   : (34.776, 32.424),
}

COMPASS     = "N NE E SE S SW W NW".split()
WC          = {0:"ясно",1:"главным образом ясно",2:"переменная",3:"пасмурно",
              45:"туман",48:"изморозь",51:"морось",61:"дождь",71:"снег"}  # сокращено
EMO_BULLET  = "•"

HEADLINE    = f"☀️ <b>Погода на завтра в Лимассоле {DATE_STR}</b>"

# ──────────────────────────────  утилиты  ─────────────────────────────
def http(url, params=None, headers=None, timeout=20, key=None):
    try:
        r = requests.get(url, params=params, headers=headers, timeout=timeout)
        r.raise_for_status()
        return r.json() if key!="text" else r.text
    except Exception as e:
        print("[warn]", url.split("/")[2], "->", e)
        return None

def deg_to_compass(deg: float|None):
    if deg is None: return "—"
    idx = int((deg % 360) / 45 + .5) % 8
    return COMPASS[idx]

def smart_choice(*facts):
    """Возвращает «виновника дня»."""
    picks = [f for f in facts if f] or ["погоду"]
    return random.choice(picks)

# ────────────────────────────  данные  ────────────────────────────────
def openmeteo_daily(lat, lon):
    params = dict(latitude=lat, longitude=lon, timezone="auto",
                  start_date=str(TOMORROW), end_date=str(TOMORROW),
                  daily="temperature_2m_max,temperature_2m_min,weathercode")
    j = http("https://api.open-meteo.com/v1/forecast", params)
    if not j or "daily" not in j: return {}
    d = j["daily"]
    return {
        "tmax":   d["temperature_2m_max"][0],
        "tmin":   d["temperature_2m_min"][0],
        "code":   d["weathercode"][0],
    }

def openmeteo_current(lat, lon):
    params = dict(latitude=lat, longitude=lon, timezone="auto",
                  current="temperature_2m,pressure_msl,wind_speed_10m,wind_direction_10m,weathercode")
    j = http("https://api.open-meteo.com/v1/forecast", params)
    if not j or "current" not in j: return {}
    c = j["current"]
    return {
        "temp":   c["temperature_2m"],
        "press":  c.get("pressure_msl"),
        "wind":   c.get("wind_speed_10m"),
        "wdir":   c.get("wind_direction_10m"),
        "code":   c.get("weathercode"),
    }

def air_quality():
    key = os.environ.get("AIRVISUAL_KEY")
    if not key: return {}
    p = dict(key=key, lat=LAT, lon=LON)
    j = http("https://api.airvisual.com/v2/nearest_city", p)
    if not j or j.get("status")!="success": return {}
    d=j["data"]["current"]["pollution"]; a=j["data"]["current"]["weather"]
    return {"aqi":d["aqius"],
            "pm25": j["data"]["current"]["pollution"].get("p2") or "—",
            "pm10": j["data"]["current"]["pollution"].get("p1") or "—",
            "press": a.get("pr") }

def pollen():
    key = os.environ.get("AMBEE_KEY")
    if not key: return {}
    hdr={"x-api-key":key}
    url=f"https://api.ambeedata.com/latest/pollen/by-lat-lng?lat={LAT}&lng={LON}"
    j=http(url,headers=hdr)
    if not j or j.get("message")!="success": return {}
    idx=j["data"][0]["Count"]
    return {"grass": idx["grass_pollen"]}

def kp_index():
    j=http("https://services.swpc.noaa.gov/json/planetary_k_index_1m.json")
    if not j: return None
    try: return j[-1]["kp_index"]
    except: return None

def schumann():
    txt=http("https://data.schumann-resonance.org/latest.csv",key="text")
    if not txt: return None
    rows=list(csv.reader(txt.splitlines()))
    try:
        f,a=map(float,rows[-1][1:3]); return f,a
    except: return None

def moon_phase():
    ref=pendulum.datetime(2000,1,6,tz=TZ).naive()
    age=(pendulum.now(TZ).naive()-ref).days%29.53
    pct=round(abs(14.77-age)*100/14.77)  # проценты от новолуния/полнолуния
    signs="♈♉♊♋♌♍♎♏♐♑♒♓".split("♈")[1:]  # простая карта
    sign=signs[int((pendulum.now(TZ).day_of_year%360)/30)]
    return pct,sign

# ─────────────────────────  построение сообщения  ─────────────────────
def build_msg():
    # данные Limassol
    d=openmeteo_daily(LAT,LON)
    c=openmeteo_current(LAT,LON)
    press=c.get("press") or air_quality().get("press") or "—"
    wind_dir=deg_to_compass(c.get("wdir"))
    wind_sp = f"{c.get('wind',0):.1f} км/ч" if c.get("wind") is not None else "—"
    wc_desc = WC.get(d.get("code") if d else c.get("code"),"переменная")
    if d.get("code") in (45,48): wc_desc += " — будьте внимательны, возможен туман!"
    # осадки
    precip="не ожидаются" if d.get("code",99) not in range(51,78) else "вероятны"
    # tmax/tmin
    tmax = d.get("tmax") or c.get("temp")
    tmin = d.get("tmin") or c.get("temp")
    # города Кипра
    temps={}
    for name,(la,lo) in CITIES.items():
        td=openmeteo_daily(la,lo)
        temps[name]=td.get("tmax")
    warm=max((k for k,v in temps.items() if v), key=lambda k:temps[k])
    cold=min((k for k,v in temps.items() if v), key=lambda k:temps[k])
    # AQ
    aq=air_quality()
    # Kp
    kp=kp_index()
    # Schumann
    sch=schumann()
    sch_str="нет данных" if not sch else f"{sch[0]} Гц, A={sch[1]}"
    # Pollen
    pol=pollen()
    pollen_str="нет данных" if not pol else f"Травы: {pol['grass']}"
    # Astro
    pct,sign=moon_phase()
    astro=["Растущая Луна "+sign+f" ({pct} %)","Мини-парад планет","Eta Aquarids (пик 6 мая)"]
    # «виновник дня»
    culprit=smart_choice(
        "давление"     if isinstance(press,(int,float)) and press<1005 else "",
        "магнитные бури" if kp and kp>=4 else "",
        "ретроградный Меркурий" if random.random()<0.05 else "",
        "ветер" if c.get("wind",0)>20 else ""
    )
    # рекомендации
    rec=[
        "😊 Улыбайтесь чаще — повышает гормоны радости!",
        "🌬️ Лёгкие дыхательные практики помогут, если " + culprit + " пошалит.",
        "🌙 Медитация под лунным светом — заряд креатива!",
    ]
    # формируем HTML
    parts=[
        HEADLINE,
        f"<b>Темп. днём:</b> {tmax:.1f} °C",
        f"<b>Темп. ночью:</b> {tmin:.1f} °C",
        f"Облачность: {wc_desc}",
        f"Осадки: {precip}",
        f"Ветер: {wind_sp}, {wind_dir}",
        f"Давление: {press} гПа" if press!="—" else "Давление: — гПа",
        f"<i>Самое тёплое:</i> {warm} ({temps[warm]:.1f} °C)",
        f"<i>Самое прохладное:</i> {cold} ({temps[cold]:.1f} °C)",
        "———",
        f"🌬️ <b>Качество воздуха</b>",
        f"AQI: {aq.get('aqi','—')} | PM2.5: {aq.get('pm25','—')} | PM10: {aq.get('pm10','—')}",
        "🌿 <b>Пыльца</b>",
        pollen_str,
        "🌌 <b>Геомагнитная активность</b>",
        f"Kp {kp if kp is not None else '—'}",
        "📈 <b>Резонанс Шумана</b>",
        sch_str if sch else "нет данных – датчики в ретрите",
        "🌊 <b>Температура воды</b>",
        f"Сейчас: {openmeteo_current(LAT,LON).get('temp','—')} °C",
        "🔮 <b>Астрологические события</b>",
        " | ".join(astro),
        "———",
        "📝 <b>Вывод</b>",
        f"Если завтра что-то пойдёт не так, вините {culprit}! Главное — сохраняйте хорошее настроение!",
        "———",
        "✅ <b>Рекомендации</b>",
        "\n".join([EMO_BULLET+" "+t for t in rec])
    ]
    return "\n".join(parts)

# ─────────────────────────────  отправка  ─────────────────────────────
async def main():
    html=build_msg()
    print("Preview:", html.replace("\n"," | ")[:200])
    bot=Bot(os.environ["TELEGRAM_TOKEN"])
    await bot.send_message(chat_id=os.environ["CHANNEL_ID"],
                           text=html[:4096], parse_mode="HTML",
                           disable_web_page_preview=True)

if __name__=="__main__":
    asyncio.run(main())
