"""
post.py – VayboМетр v3.1
Markdown-дайджест: эмодзи-блоки, жирные подписи, пустые строки, линия '---'.
• Python строит шаблон, GPT генерирует только «📝 Вывод» и «✅ Рекомендации».
• Пустые блоки скрываются.  • Никаких <br>/<html> – Telegram получает Markdown.

GitHub Secrets: OPENAI_API_KEY  TELEGRAM_TOKEN  CHANNEL_ID (-100…)
               OWM_KEY  AIRVISUAL_KEY  TOMORROW_KEY
requirements.txt: requests python-dateutil openai python-telegram-bot pyswisseph
"""
from __future__ import annotations

import asyncio, json, os, sys
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import requests, swisseph as swe            # pip install pyswisseph
from dateutil import tz
from openai import OpenAI
from telegram import Bot, error as tg_err

LAT, LON = 34.707, 33.022                   # Limassol marina
TZ = tz.gettz("Asia/Nicosia")


# ─────────────────── HTTP helper ────────────────────
def _get(url: str, **params) -> Optional[dict]:
    try:
        r = requests.get(url, params=params, timeout=20)
        r.raise_for_status()
        return r.json()
    except Exception as exc:                # noqa: BLE001
        print(f"[warn] {url} → {exc}", file=sys.stderr)
        return None


# ─────────────────── Data sources ───────────────────
def get_weather() -> Optional[dict]:
    key = os.getenv("OWM_KEY")
    if key:
        for ver in ("3.0", "2.5"):
            data = _get(f"https://api.openweathermap.org/data/{ver}/onecall",
                        lat=LAT, lon=LON, appid=key, units="metric",
                        exclude="minutely,hourly")
            if data and data.get("current") and data.get("daily"):
                return data
    # fallback Open-Meteo (current + daily)
    return _get("https://api.open-meteo.com/v1/forecast",
                latitude=LAT, longitude=LON,
                current_weather=True,
                daily="temperature_2m_max,temperature_2m_min,precipitation_sum",
                timezone="UTC")


def get_air() -> Optional[dict]:
    key = os.getenv("AIRVISUAL_KEY")
    return _get("https://api.airvisual.com/v2/nearest_city",
                lat=LAT, lon=LON, key=key) if key else None


def get_pollen() -> Optional[dict]:
    key = os.getenv("TOMORROW_KEY")
    if not key:
        return None
    data = _get("https://api.tomorrow.io/v4/timelines",
                apikey=key, location=f"{LAT},{LON}",
                fields="treeIndex,grassIndex,weedIndex",
                timesteps="1d", units="metric")
    try:
        v = data["data"]["timelines"][0]["intervals"][0]["values"]
        return {"tree": v["treeIndex"], "grass": v["grassIndex"], "weed": v["weedIndex"]}
    except Exception:
        return None


def get_sst() -> Optional[float]:
    data = _get("https://marine-api.open-meteo.com/v1/marine",
                latitude=LAT, longitude=LON,
                hourly="sea_surface_temperature", timezone="UTC")
    try:
        return round(float(data["hourly"]["sea_surface_temperature"][0]), 1)
    except Exception:
        return None


def get_kp() -> Optional[float]:
    arr = _get("https://services.swpc.noaa.gov/products/noaa-planetary-k-index.json")
    try:
        return float(arr[-1][1])
    except Exception:
        return None


def get_schumann() -> Optional[dict]:
    data = _get("https://api.glcoherence.org/v1/earth")
    try:
        return {"freq": data["frequency_1"], "amp": data["amplitude_1"]}
    except Exception:
        return None


def get_astro() -> Optional[str]:
    jd = swe.julday(*datetime.utcnow().timetuple()[:3])
    lon_ven = swe.calc_ut(jd, swe.VENUS)[0][0]
    lon_sat = swe.calc_ut(jd, swe.SATURN)[0][0]
    diff = abs((lon_ven - lon_sat + 180) % 360 - 180)
    if diff < 3:
        return "Конъюнкция Венеры и Сатурна — фокус на отношениях"
    return None


# ───────── GPT: вывод + советы ─────────
def gpt_comment(weather_snippet: dict) -> tuple[str, str]:
    client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
    prompt = ("На основе данных (temp_min, temp_max, pressure, kp, aqi) дай 1-абз. вывода "
              "и 4-5 советов. Формат:\nВывод: …\nСоветы:\n- …")
    resp = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user",
                   "content": prompt + "\n" + json.dumps(weather_snippet, ensure_ascii=False)}],
        temperature=0.4,
    )
    txt = resp.choices[0].message.content.strip()
    summary = txt.split("Советы:")[0].replace("Вывод:", "").strip()
    tips = "\n".join(f"- {t.strip('- ')}"
                     for t in txt.split("Советы:")[-1].splitlines() if t.strip())
    return summary, tips


# ───────── Digest builder ─────────
def build_md(d: Dict[str, Any]) -> str:
    parts: list[str] = []

    # Weather block
    w = d["weather"]
    if w:
        if "current" in w:                       # OWM
            cur, day = w["current"], w["daily"][0]["temp"]
            cloud = w["current"].get("clouds", "—")
            parts += [
                "☀️ **Погода**",
                f"**Температура:** днём до {day['max']:.0f} °C, ночью около {day['min']:.0f} °C",
                f"**Облачность:** {cloud} %",
                f"**Ветер:** {cur['wind_speed']*3.6:.1f} км/ч",
                f"**Давление:** {cur['pressure']} гПа",
            ]
            snippet = {"temp_min": day['min'], "temp_max": day['max'], "pressure": cur['pressure']}
        else:                                    # Open-Meteo
            cw, dm = w["current_weather"], w["daily"]
            parts += [
                "☀️ **Погода**",
                f"**Температура:** днём до {dm['temperature_2m_max'][0]:.0f} °C, "
                f"ночью около {dm['temperature_2m_min'][0]:.0f} °C",
                f"**Ветер:** {cw['windspeed']:.1f} км/ч",
            ]
            snippet = {"temp_min": dm['temperature_2m_min'][0],
                       "temp_max": dm['temperature_2m_max'][0], "pressure": "—"}
    else:
        snippet = {}

    # Air quality
    air = d["air"]
    if air:
        pol = air["data"]["current"]["pollution"]
        parts += [
            "",
            "🌬️ **Качество воздуха**",
            f"**AQI:** {pol['aqius']}  |  **PM2.5:** {pol['p2']} µg/m³  |  **PM10:** {pol['p1']} µg/m³",
        ]
        snippet["aqi"] = pol['aqius']

    # Pollen
    p = d["pollen"]
    if p:
        parts += [
            "",
            "🌿 **Уровень пыльцы**",
            f"**Деревья:** {p['tree']}  |  **Травы:** {p['grass']}  |  **Сорняки:** {p['weed']}",
        ]

    # Kp-index
    if (kp := d["kp"]) is not None:
        state = "спокойный" if kp < 4 else "буря" if kp >= 5 else "повышенный"
        parts += ["", "🌌 **Геомагнитная активность**", f"**Уровень:** {state} (Kp {kp:.1f})"]
        snippet["kp"] = kp

    # Schumann
    sch = d["schumann"]
    if sch:
        parts += ["", "📈 **Резонанс Шумана**",
                  f"**Частота:** ≈{sch['freq']:.1f} Гц",
                  f"**Амплитуда:** {sch['amp']}"]

    # SST
    if d["sst"]:
        parts += ["", "🌊 **Температура воды в море**", f"**Сейчас:** {d['sst']} °C"]

    # Astro
    if d["astro"]:
        parts += ["", "🔮 **Астрологические события**", d["astro"]]

    parts.append("---")

    # GPT summary / tips
    summary, tips = gpt_comment(snippet)
    parts += ["### 📝 Вывод", summary, "", "---", "### ✅ Рекомендации", tips]

    return "\n".join(parts)


# ───────── Telegram send ─────────
async def send(md: str):
    bot = Bot(os.environ["TELEGRAM_TOKEN"])
    await bot.send_message(os.environ["CHANNEL_ID"], md[:4096],
                           parse_mode="Markdown", disable_web_page_preview=True)


# ───────── Main ─────────
async def main():
    data = {
        "weather": get_weather(),
        "air": get_air(),
        "pollen": get_pollen(),
        "sst": get_sst(),
        "kp": get_kp(),
        "schumann": get_schumann(),
        "astro": get_astro(),
    }
    print("RAW slice:", json.dumps(data, ensure_ascii=False)[:350])
    md = build_md(data)
    print("MD snippet:", md[:200].replace("\n", " | "))
    try:
        await send(md)
        print("✓ sent to Telegram")
    except tg_err.TelegramError as e:
        print("Telegram error:", e, file=sys.stderr)
        raise


if __name__ == "__main__":
    asyncio.run(main())
