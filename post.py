"""
post.py – VayboМетр v3.3  (Markdown clean)

• Эмодзи-блоки, жирные подписи, пустые строки, линия '---'
• GPT – только абзац «Вывод» + список «Рекомендации»
• Пустые данные скрываются, <br>/<html> нет.
"""

from __future__ import annotations
import asyncio, json, os, sys
from datetime import datetime
from typing import Any, Dict, Optional

import requests, swisseph as swe
from openai import OpenAI
from telegram import Bot, error as tg_err
from dateutil import tz

LAT, LON = 34.707, 33.022
TZ = tz.gettz("Asia/Nicosia")


# ────────────────── helpers ──────────────────
def _get(u: str, **p) -> Optional[dict]:
    try:
        r = requests.get(u, params=p, timeout=20); r.raise_for_status()
        return r.json()
    except Exception as e:                                 # noqa: BLE001
        print(f"[warn] {u} -> {e}", file=sys.stderr); return None


def aqi_to_pm25(aqi: float) -> float:                      # EPA breakpoints
    bp = [(0, 50, 0, 12), (51, 100, 12.1, 35.4),
          (101, 150, 35.5, 55.4), (151, 200, 55.5, 150.4),
          (201, 300, 150.5, 250.4), (301, 400, 250.5, 350.4),
          (401, 500, 350.5, 500.4)]
    for Il, Ih, Cl, Ch in bp:
        if Il <= aqi <= Ih:
            return round((aqi - Il)*(Ch-Cl)/(Ih-Il)+Cl, 1)
    return aqi


# ────────────────── sources ──────────────────
def get_weather():
    k = os.getenv("OWM_KEY")
    if k:
        for ver in ("3.0", "2.5"):
            d = _get(f"https://api.openweathermap.org/data/{ver}/onecall",
                     lat=LAT, lon=LON, appid=k, units="metric",
                     exclude="minutely,hourly")
            if d and d.get("current") and d.get("daily"):
                return d
    return _get("https://api.open-meteo.com/v1/forecast",
                latitude=LAT, longitude=LON, current_weather=True,
                hourly="cloud_cover",
                daily="temperature_2m_max,temperature_2m_min",
                timezone="UTC")


def get_air():
    k = os.getenv("AIRVISUAL_KEY")
    return _get("https://api.airvisual.com/v2/nearest_city",
                lat=LAT, lon=LON, key=k) if k else None


def get_pollen():
    k = os.getenv("TOMORROW_KEY")
    if not k: return None
    d = _get("https://api.tomorrow.io/v4/timelines", apikey=k,
             location=f"{LAT},{LON}",
             fields="treeIndex,grassIndex,weedIndex", timesteps="1d")
    try:
        v = d["data"]["timelines"][0]["intervals"][0]["values"]
        return {"tree": v["treeIndex"], "grass": v["grassIndex"], "weed": v["weedIndex"]}
    except Exception: return None


def get_sst():
    d = _get("https://marine-api.open-meteo.com/v1/marine",
             latitude=LAT, longitude=LON,
             hourly="sea_surface_temperature", timezone="UTC")
    try: return round(float(d["hourly"]["sea_surface_temperature"][0]), 1)
    except Exception: return None


def get_kp():
    arr = _get("https://services.swpc.noaa.gov/products/noaa-planetary-k-index.json")
    try: return float(arr[-1][1])
    except Exception: return None


def get_schumann():
    d = _get("https://api.glcoherence.org/v1/earth")
    try: return {"freq": d["frequency_1"], "amp": d["amplitude_1"]}
    except Exception: return None


def get_astro():
    jd = swe.julday(*datetime.utcnow().timetuple()[:3])
    lonV = swe.calc_ut(jd, swe.VENUS)[0][0]
    lonS = swe.calc_ut(jd, swe.SATURN)[0][0]
    if abs((lonV - lonS + 180) % 360 - 180) < 3:
        return "Конъюнкция Венеры и Сатурна — фокус на отношениях"
    return None


# ───────── GPT block ─────────
def gpt_comment(snippet: dict) -> tuple[str, str]:
    client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
    rsp = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user",
                   "content": "Дай один абзац вывода и 4–5 советов:\n" +
                              json.dumps(snippet, ensure_ascii=False)}],
        temperature=0.4,
    )
    txt = rsp.choices[0].message.content.strip()
    summary = txt.split("Советы:")[0].replace("Вывод:", "").strip()
    tips = "\n".join(f"- {l.strip('- ')}"
                     for l in txt.split("Советы:")[-1].splitlines() if l.strip())
    return summary, tips


# ───────── Digest build ─────────
def build_md(d: Dict[str, Any]) -> str:
    P, snip = [], {}

    # Погода
    if w := d["weather"]:
        if "current" in w:                      # OWM
            cur, day = w["current"], w["daily"][0]["temp"]
            P += ["☀️ **Погода**",
                  f"**Температура:** днём до {day['max']:.0f} °C, ночью около {day['min']:.0f} °C",
                  f"**Облачность:** {cur.get('clouds', '—')} %",
                  f"**Ветер:** {cur['wind_speed']*3.6:.1f} км/ч",
                  f"**Давление:** {cur['pressure']} гПа"]
            snip.update(temp_min=day['min'], temp_max=day['max'], pressure=cur['pressure'])
        else:                                   # Open-Meteo
            cw, dm, cloud = w["current_weather"], w["daily"], w["hourly"]["cloud_cover"][0]
            P += ["☀️ **Погода**",
                  f"**Температура:** днём до {dm['temperature_2m_max'][0]:.0f} °C, "
                  f"ночью около {dm['temperature_2m_min'][0]:.0f} °C",
                  f"**Облачность:** {cloud} %",
                  f"**Ветер:** {cw['windspeed']:.1f} км/ч"]
            snip.update(temp_min=dm['temperature_2m_min'][0],
                         temp_max=dm['temperature_2m_max'][0])

    # AQI
    if (air := d["air"]):
        pol = air["data"]["current"]["pollution"]
        pm25 = pol.get("p2") or aqi_to_pm25(pol["aqius"])
        pm10 = pol.get("p1", "—")
        P += ["", "🌬️ **Качество воздуха**",
              f"**AQI:** {pol['aqius']}  |  **PM2.5:** {pm25} µg/m³  |  **PM10:** {pm10} µg/m³"]
        snip["aqi"] = pol['aqius']

    # Пыльца
    if p := d["pollen"]:
        P += ["", "🌿 **Уровень пыльцы**",
              f"**Деревья:** {p['tree']}  |  **Травы:** {p['grass']}  |  **Сорняки:** {p['weed']}"]

    # Kp
    if (kp := d["kp"]) is not None:
        state = "буря (G1)" if kp >= 5 else "спокойный" if kp < 4 else "повышенный"
        P += ["", "🌌 **Геомагнитная активность**", f"**Уровень:** {state} (Kp {kp:.1f})"]
        snip["kp"] = kp

    # Schumann
    if s := d["schumann"]:
        P += ["", "📈 **Резонанс Шумана**",
              f"**Частота:** ≈{s['freq']:.1f} Гц", f"**Амплитуда:** {s['amp']}"]

    # SST
    if d["sst"]:
        P += ["", "🌊 **Температура воды в море**", f"**Сейчас:** {d['sst']} °C"]

    # Astro
    if d["astro"]:
        P += ["", "🔮 **Астрологические события**", d["astro"]]

    P.append("---")

    # GPT
    summary, tips = gpt_comment(snip)
    P += ["**📝 Вывод**", summary, "", "---", "**✅ Рекомендации**", tips]

    return "\n".join(P)


# ───────── Telegram ─────────
async def send(md: str):
    bot = Bot(os.environ["TELEGRAM_TOKEN"])
    await bot.send_message(os.environ["CHANNEL_ID"], md[:4096],
                           parse_mode="Markdown", disable_web_page_preview=True)


# ───────── main ─────────
async def main():
    d = {"weather": get_weather(), "air": get_air(), "pollen": get_pollen(),
         "sst": get_sst(), "kp": get_kp(), "schumann": get_schumann(),
         "astro": get_astro()}
    md = build_md(d)
    print("MD snippet:", md[:200].replace("\n", " | "))
    try:
        await send(md); print("✓ sent")
    except tg_err.TelegramError as e:
        print("Telegram error:", e, file=sys.stderr); raise


if __name__ == "__main__":
    asyncio.run(main())
