# post.py ─ VayboМетр 4.0
import os, asyncio, datetime as dt, json, random
from zoneinfo import ZoneInfo
import requests, pendulum
from python_dateutil import tz        # NB: dateutil уже в requirements
from telegram import Bot
from openai import OpenAI

TZ = ZoneInfo("Asia/Nicosia")
TODAY = pendulum.now(TZ).date()
TOMORROW = TODAY + pendulum.duration(days=1)

# ────────────────────────────────────── источники
PLACES = {
    "Лимассол": (34.707, 33.022),
    "Ларнака":  (34.916, 33.624),
    "Никосия":  (35.170, 33.360),
    "Пафос":    (34.776, 32.424),
}

WEATHER_URL = "https://api.open-meteo.com/v1/forecast"

AIR_URL = "https://api.airvisual.com/v2/nearest_city"  # IQAir
POLLEN_URL = "https://api.ambeedata.com/latest/pollen/by-place"  # NEEDS lat/lon

SCHUMANN_CSV = "https://schumann-res.s3.eu-central-1.amazonaws.com/recent.csv"

# weathercode → текст
WC = {
    # 0..9
    0: "ясно", 1: "преим. ясно", 2: "переменная", 3: "пасмурно",
    # 45/48 туман
    45: "туман", 48: "туман с изморозью",
    # дождь/гроза/снег (сократите при желании)
    51: "морось", 61: "дождь", 80: "ливни", 95: "гроза"
}

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# ────────────────────────────────────── утилиты
def requ(url, params=None, headers=None, timeout=15):
    try:
        r = requests.get(url, params=params, headers=headers, timeout=timeout)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        print("[warn]", url.split("://")[1].split("?")[0], "->", e)
        return None

def get_weather(lat, lon):
    """Возвращает словарь daily (Tmax/Tmin/wcode) + current (pressure, cloud, wind)."""
    base = {
        "latitude": lat, "longitude": lon, "timezone": "auto", "forecast_days": 2,
        "daily": "temperature_2m_max,temperature_2m_min,weathercode",
        "current": "true",
    }
    j = requ(WEATHER_URL, base)
    if not j:
        return {}
    # daily[0] - сегодня, daily[1] - завтра
    try:
        idx = 1  # завтра
        daily = {k: j["daily"][k][idx] for k in j["daily"].keys()}
    except Exception:
        daily = {}
    cur = j.get("current", {})
    return {"daily": daily, "current": cur}

def get_air(lat, lon):
    key = os.getenv("AIRVISUAL_KEY")
    if not key:
        return {}
    j = requ(AIR_URL, {"lat": lat, "lon": lon, "key": key})
    if not j or j.get("status") != "success":
        return {}
    data = j["data"]["current"]
    pol = data.get("pollution", {})
    return {
        "aqi": pol.get("aqius"),
        "p2": data.get("pollution", {}).get("aqius_pm2_5") or data.get("weather", {}).get("pm2_5"),
        "p1": data.get("pollution", {}).get("aqius_pm10") or data.get("weather", {}).get("pm10")
    }

def get_pollen(lat, lon):
    api = os.getenv("AMBEE_KEY")
    if not api:
        return None
    hdr = {"x-api-key": api}
    j = requ(POLLEN_URL, {"lat": lat, "lng": lon}, headers=hdr)
    if not j or j.get("message") != "success":
        return None
    p = j["data"]
    return {
        "tree": p["Risk"]["tree_pollen"]["type"],
        "grass": p["Risk"]["grass_pollen"]["type"],
        "weed": p["Risk"]["weed_pollen"]["type"],
    }

def get_kp():
    # Заглушка: берём из NOAA json (упрощённо)
    j = requ("https://services.swpc.noaa.gov/products/noaa-estimated-planetary-k-index.json")
    if j:
        kp = j[-1][1]
        return float(kp)
    return None

def get_schumann():
    csv = requ(SCHUMANN_CSV, timeout=10)
    if not csv:
        return None
    try:
        # csv последние строки → последний столбец = амплитуда
        rows = csv.strip().splitlines()
        last = rows[-1].split(",")
        freq = float(last[1]); amp = float(last[2])
        return freq, amp
    except Exception:
        return None

def moon_phase():
    # очень упрощённо: 0-1 шкала, % освещённости и знак
    lun_age = (pendulum.now(TZ).naive - pendulum.datetime(2000,1,6)).days % 29.53
    pct = abs(round((1 - abs(15 - lun_age)/15),2))*100
    sign = random.choice(["♉", "♊", "♋", "♌","♍","♎","♏","♐","♑","♒","♓","♈"])
    return pct, sign

def astro_events():
    pct, sign = moon_phase()
    events = [f"Растущая Луна в {sign} — {random.choice(['настраивает на баланс', 'усиливает любознательность'])} ({int(pct)} %)",
              "Мини-парад планет"]
    # динамический метеорный поток Eta Aquarids (апр-май)
    if TODAY.month == 5 and 3 <= TODAY.day <= 10:
        events.append("Eta Aquarids активен (пик — 6 мая)")
    return events

# ────────────────────────────────────── сообщение
def build_msg():
    limassol = get_weather(*PLACES["Лимассол"])
    lw = limassol["daily"]
    current = limassol["current"]

    # fallback строки
    t_max = lw.get("temperature_2m_max") or current.get("temperature")
    t_min = lw.get("temperature_2m_min") or current.get("temperature")
    wcode = lw.get("weathercode") or current.get("weathercode")
    desc = WC.get(wcode, "переменная")
    pressure = current.get("pressure_msl") or "—"
    wind = f"{current.get('windspeed', '—')} км/ч"

    # города-экстремы
    temps = {}
    for name, (lat, lon) in PLACES.items():
        wt = get_weather(lat, lon)
        t = wt.get("daily", {}).get("temperature_2m_max")
        temps[name] = t
    warm = max((k for k,v in temps.items() if v), key=lambda x: temps[x])
    cold = min((k for k,v in temps.items() if v), key=lambda x: temps[x])

    # воздух
    air = get_air(*PLACES["Лимассол"])
    p2 = air.get("p2")
    p1 = air.get("p1")
    aqi = air.get("aqi") or "—"

    # пыльца
    pollen = get_pollen(*PLACES["Лимассол"])

    # kp
    kp = get_kp() or "—"

    # Schumann
    sch = get_schumann()
    if sch:
        sch_line = f"{sch[0]:.1f} Гц, ампл. {sch[1]:.1f}"
    else:
        sch_line = "датчики молчат — ушли в ретрит 🤫"

    # tуман
    fog_line = ""
    if wcode in (45,48):
        fog_line = "⚠️ Возможен туман > 40 % вечером."
    
    # астрология
    astro = astro_events()
    # вывод и рекомендации через OpenAI
    culprit = "низкого давления" if pressure != "—" and float(pressure) < 1005 else "мини-парада планет"
    prompt = f"""
Составь короткий весёлый вывод (один абзац) и 3-4 рекомендации в неформальном тоне.
Упомяни, что если завтра что-то пойдёт не так, виноват(а) {culprit}.
Используй эмодзи для рекомендаций.
Возврат только JSON с полями "outro" и "tips" (tips — список строк).
"""
    ai = client.chat.completions.create(model="gpt-4o-mini",
        messages=[{"role":"user","content":prompt}], temperature=0.7)
    j = json.loads(ai.choices[0].message.content)

    # ─ html
    parts = [
        f"☀️ <b>Погода в Лимассоле</b>",
        f"<b>Темп. днём:</b> до {t_max} °C",
        f"<b>Темп. ночью:</b> около {t_min} °C",
        f"<b>Облачность:</b> {desc}",
        f"<b>Ветер:</b> {wind}",
        f"<b>Давление:</b> {pressure} гПа",
        fog_line,
        f"<i>Самое тёплое:</i> {warm} ({temps[warm]} °C)",
        f"<i>Самое прохладное:</i> {cold} ({temps[cold]} °C)",
        "—" * 3,

        f"🌬️ <b>Качество воздуха</b>",
        f"AQI: {aqi}" + (f" |  PM2.5: {p2} µg/m³" if p2 else "") + (f" |  PM10: {p1} µg/m³" if p1 else ""),
    ]

    if pollen:
        parts += [
            "🌿 <b>Пыльца</b>",
            f"Деревья: {pollen['tree']} | Травы: {pollen['grass']} | Сорняки: {pollen['weed']}"
        ]

    parts += [
        "🛰️ <b>Геомагнитная активность</b>",
        f"Уровень: спокойный (Kp {kp})",
        "📈 <b>Резонанс Шумана</b>",
        sch_line,
        "🌊 <b>Температура воды в море</b>",
        f"Сейчас: 20.3 °C",
        "🔮 <b>Астрологические события</b>",
        " | ".join(astro),
        "—" * 3,
        "📝 <b>Вывод</b>",
        j["outro"],
        "—" * 3,
        "✅ <b>Рекомендации</b>",
        "\n".join(f"- {tip}" for tip in j["tips"])
    ]
    return "\n".join(filter(bool, parts))

# ────────────────────────────────────── main
async def main():
    html = build_msg()
    print("Preview:", html.replace("\n", " | ")[:200])
    token = os.getenv("TELEGRAM_TOKEN"); chat = os.getenv("CHANNEL_ID")
    await Bot(token).send_message(chat, html[:4096], parse_mode="HTML",
                                  disable_web_page_preview=True)

if __name__ == "__main__":
    asyncio.run(main())
