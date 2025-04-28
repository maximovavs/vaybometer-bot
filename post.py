"""Automated daily VayboМетр poster – v2.0  (full vibe‑stack)

Собирает реальные данные по Лимассолу и выкатывает HTML‑дайджест в Telegram:

  • ☀️  Погода          — OpenWeather One Call 3.0 (OWM_KEY)
  • 🌬️  Качество воздуха — IQAir / AirVisual (AIRVISUAL_KEY)
  • 🌿  Пыльца           — Tomorrow.io (TOMORROW_KEY)
  • 🌊  Температура моря — Open‑Meteo marine API (без ключа)
  • 🌌  Kp‑индекс        — NOAA SWPC JSON (без ключа)
  • 📈  Резонанс Шумана  — Global Coherence API (без ключа)
  • 🔮  Астрособытия     — Swiss Ephemeris (локально)

GitHub Secrets (обязательные):
  OPENAI_API_KEY   – OpenAI
  TELEGRAM_TOKEN   – Telegram bot token
  CHANNEL_ID       – id/@ канала
  OWM_KEY          – OpenWeather
  AIRVISUAL_KEY    – IQAir
  TOMORROW_KEY     – Tomorrow.io

Python deps (добавь в requirements.txt / workflow):
  requests python-dateutil openai python-telegram-bot pyswisseph
"""
from __future__ import annotations

import asyncio
import json
import math
import os
import re
import sys
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import requests
import swisseph as swe  # pip install pyswisseph
from dateutil import tz
from telegram import Bot
from openai import OpenAI

LAT = 34.707  # Limassol Marina
LON = 33.022
LOCAL_TZ = tz.gettz("Asia/Nicosia")

# ---------------------------------------------------------------------------
# tiny HTTP helper -----------------------------------------------------------
# ---------------------------------------------------------------------------

def _get(url: str, **params) -> Optional[dict]:
    try:
        r = requests.get(url, params=params, timeout=20)
        r.raise_for_status()
        return r.json()
    except Exception as exc:  # noqa: BLE001, S110
        print(f"[warn] {url} failed: {exc}", file=sys.stderr)
        return None


# ---------------------------------------------------------------------------
# Data sources ---------------------------------------------------------------
# ---------------------------------------------------------------------------

def get_weather() -> Optional[dict]:
    """Try One Call 3.0 → fallback на 2.5 → в крайнем случае Open‑Meteo."""
    key = os.getenv("OWM_KEY")
    if not key:
        return None

    # --- attempt: One Call 3.0 ------------------------------------------------
    data = _get(
        "https://api.openweathermap.org/data/3.0/onecall",
        lat=LAT,
        lon=LON,
        appid=key,
        units="metric",
        exclude="minutely,hourly,alerts",
    )
    if data and "current" in data:
        return data  # успех 3.0

    # --- fallback: One Call 2.5 (бесплатная) ----------------------------------
    data = _get(
        "https://api.openweathermap.org/data/2.5/onecall",
        lat=LAT,
        lon=LON,
        appid=key,
        units="metric",
        exclude="minutely,hourly,alerts",
    )
    if data and "current" in data:
        return data

    # --- ultimate fallback: Open‑Meteo (no key) ------------------------------
    return _get(
        "https://api.open-meteo.com/v1/forecast",
        latitude=LAT,
        longitude=LON,
        current_weather=True,
    )
        "https://api.openweathermap.org/data/3.0/onecall",
        lat=LAT,
        lon=LON,
        appid=key,
        units="metric",
        exclude="minutely,hourly,alerts",
    )


def get_air_quality() -> Optional[dict]:
    key = os.getenv("AIRVISUAL_KEY")
    if not key:
        return None
    return _get(
        "https://api.airvisual.com/v2/nearest_city",
        lat=LAT,
        lon=LON,
        key=key,
    )


def get_pollen() -> Optional[dict]:
    key = os.getenv("TOMORROW_KEY")
    if not key:
        return None
    data = _get(
        "https://api.tomorrow.io/v4/timelines",
        apikey=key,
        location=f"{LAT},{LON}",
        fields="treeIndex,grassIndex,weedIndex",
        timesteps="1d",
        units="metric",
    )
    try:
        entry = data["data"]["timelines"][0]["intervals"][0]["values"]
        return {
            "tree": entry.get("treeIndex"),
            "grass": entry.get("grassIndex"),
            "weed": entry.get("weedIndex"),
        }
    except Exception:
        return None


def get_sst() -> Optional[dict]:
    data = _get(
        "https://marine-api.open-meteo.com/v1/marine",
        latitude=LAT,
        longitude=LON,
        hourly="sea_surface_temperature",
        timezone="UTC",
    )
    try:
        temp = float(data["hourly"]["sea_surface_temperature"][0])
        return {"sst": round(temp, 1)}
    except Exception:
        return None


def get_geomagnetic() -> Optional[dict]:
    data = _get("https://services.swpc.noaa.gov/products/noaa-planetary-k-index.json")
    if not data or len(data) < 2:
        return None
    try:
        kp = float(data[-1][1])
        return {"kp": kp}
    except Exception:
        return None


def get_schumann() -> Optional[dict]:
    data = _get("https://api.glcoherence.org/v1/earth")
    try:
        return {"amp": data["amplitude_1"], "freq": data["frequency_1"]}
    except Exception:
        return None


# 🔮 simple astro event ------------------------------------------------------

def get_astro() -> Optional[dict]:
    """Простейший пример: ловим соединение Венеры‑Сатурна ±3°."""
    today = datetime.utcnow()
    jd = swe.julday(today.year, today.month, today.day)
    lon_ven = swe.calc_ut(jd, swe.VENUS)[0]  # долгота в °
    lon_sat = swe.calc_ut(jd, swe.SATURN)[0]
    diff = abs((lon_ven - lon_sat + 180) % 360 - 180)
    if diff < 3:
        return {"event": "Конъюнкция Венеры и Сатурна (фокус на отношениях)"}
    return None


# ---------------------------------------------------------------------------
# Collect raw ---------------------------------------------------------------
# ---------------------------------------------------------------------------

def collect() -> Dict[str, Any]:
    return {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "weather": get_weather(),
        "air": get_air_quality(),
        "pollen": get_pollen(),
        "sst": get_sst(),
        "geomagnetic": get_geomagnetic(),
        "schumann": get_schumann(),
        "astro": get_astro(),
    }


# ---------------------------------------------------------------------------
# OpenAI prettifier ----------------------------------------------------------
# ---------------------------------------------------------------------------

def prettify(raw: Dict[str, Any]) -> str:
    client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])

    system_msg = """
Ты — VayboМетр‑поэт. Сформатируй HTML‑дайджест для Telegram строго так:

<b>🗺️ Локация</b>
Город: Limassol
Страна: Cyprus

<b>🌬️ Качество воздуха</b>
AQI: <число> (<загрязнитель>)
Комментарий: <коротко>

<b>☀️ Погода</b>
Температура: <°C>
Облачность: <описание>
Давление: <hPa>
Ветер: <м/с> (<направление>)

<b>🌊 Температура моря</b>
Сейчас: <°C> — опусти блок, если данных нет

<b>🌿 Пыльца</b>
Деревья/Травы/Сорняки: <0‑5>/<0‑5>/<0‑5> — опусти, если нет

<b>🌌 Геомагнитика</b>
Kp‑индекс: <число> — спокойная/буря — опусти, если нет

<b>📈 Резонанс Шумана</b>
Амплитуда: <значение> — опусти блок, если нет

<b>🔮 Астрология</b>
<событие> — опусти, если None

<b>✅ Рекомендация</b>
Короткая позитивная фраза.

Правила: только <b> теги, никаких <html>/<body>, символ \n = перенос.
"""

    prompt = "Сформатируй красиво на основе данных:\n" + json.dumps(raw, ensure_ascii=False, indent=2)

    resp = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": system_msg},
            {"role": "user", "content": prompt},
        ],
        temperature=0.35,
    )
    text = resp.choices[0].message.content.strip()

    # sanitize for Telegram
    text = re.sub(r"^```[\s\S]*?\n|\n```$", "", text)
    text = text.replace("\\n", "\n")
    text = re.sub(r"(?i)<!DOCTYPE[^>]*>", "", text)
    text = re.sub(r"(?i)</?(html|body)[^>]*>", "", text)
    return text.strip()


# ---------------------------------------------------------------------------
# Telegram sender -----------------------------------------------------------
# ---------------------------------------------------------------------------

async def send(html: str) -> None:
    bot = Bot(token=os.environ["TELEGRAM_TOKEN"])
    await bot.send_message(
        chat_id=os.environ["CHANNEL_ID"],
        text=html[:4096],
        parse_mode="HTML",
        disable_web_page_preview=True,
    )


# ---------------------------------------------------------------------------
# main ----------------------------------------------------------------------
# ---------------------------------------------------------------------------

async def main() -> None:
    raw = collect()
    html = prettify(raw)
    await send(html)


if __name__ == "__main__":
    asyncio.run(main())
