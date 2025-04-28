"""post.py – VayboМетр v2.1 (indent‑safe)

Собирает данные по Лимассолу и шлёт красивый HTML‑дайджест в Telegram.
Источник данных:
  ☀️  Погода             — OpenWeather One Call (3.0 → 2.5) → Open‑Meteo fallback
  🌬️  Качество воздуха   — IQAir / AirVisual
  🌿  Пыльца              — Tomorrow.io
  🌊  Температура моря    — Open‑Meteo Marine API
  🌌  Геомагнитика (Kp)   — NOAA SWPC
  📈  Резонанс Шумана     — Global Coherence (может тайм‑аутиться)
  🔮  Астрология          — Swiss Ephemeris (локально)

Требуемые GitHub‑секреты:
  OPENAI_API_KEY, TELEGRAM_TOKEN, CHANNEL_ID,
  OWM_KEY, AIRVISUAL_KEY, TOMORROW_KEY

requirements.txt → добавь:
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
from openai import OpenAI
from telegram import Bot

LAT = 34.707  # Limassol Marina
LON = 33.022
LOCAL_TZ = tz.gettz("Asia/Nicosia")

# ---------------------------------------------------------------------------
# HTTP helper
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
# Data sources
# ---------------------------------------------------------------------------

def get_weather() -> Optional[dict]:
    """OpenWeather One Call 3.0 → 2.5 → Open‑Meteo."""
    key = os.getenv("OWM_KEY")
    if not key:
        return None

    # 1) One Call 3.0
    data = _get(
        "https://api.openweathermap.org/data/3.0/onecall",
        lat=LAT,
        lon=LON,
        appid=key,
        units="metric",
        exclude="minutely,hourly,alerts",
    )
    if data and data.get("current"):
        return data

    # 2) One Call 2.5 (free)
    data = _get(
        "https://api.openweathermap.org/data/2.5/onecall",
        lat=LAT,
        lon=LON,
        appid=key,
        units="metric",
        exclude="minutely,hourly,alerts",
    )
    if data and data.get("current"):
        return data

    # 3) Fallback — Open‑Meteo
    return _get(
        "https://api.open-meteo.com/v1/forecast",
        latitude=LAT,
        longitude=LON,
        current_weather=True,
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
        values = data["data"]["timelines"][0]["intervals"][0]["values"]
        return {
            "tree": values.get("treeIndex"),
            "grass": values.get("grassIndex"),
            "weed": values.get("weedIndex"),
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
    arr = _get("https://services.swpc.noaa.gov/products/noaa-planetary-k-index.json")
    if not arr:
        return None
    try:
        kp = float(arr[-1][1])
        return {"kp": kp}
    except Exception:
        return None


def get_schumann() -> Optional[dict]:
    data = _get("https://api.glcoherence.org/v1/earth")
    try:
        return {"amp": data["amplitude_1"], "freq": data["frequency_1"]}
    except Exception:
        return None


# 🔮 Astrology (simple Venus‑Saturn conjunction)

def get_astro() -> Optional[dict]:
    today = datetime.utcnow()
    jd = swe.julday(today.year, today.month, today.day)
    lon_ven = swe.calc_ut(jd, swe.VENUS)[0]
    lon_sat = swe.calc_ut(jd, swe.SATURN)[0]
    diff = abs((lon_ven - lon_sat + 180) % 360 - 180)
    if diff < 3:
        return {"event": "Конъюнкция Венеры и Сатурна — фокус на отношениях"}
    return None


# ---------------------------------------------------------------------------
# Collect raw data
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
# OpenAI prettifier
# ---------------------------------------------------------------------------

def prettify(raw: Dict[str, Any]) -> str:
    client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])

    system_msg = """
Ты — VayboМетр‑поэт. Сделай HTML‑дайджест для Telegram:\n
<b>🗺️ Локация</b>\nГород: Limassol\nСтрана: Cyprus\n\n<b>🌬️ Качество воздуха</b>\nAQI: <число> (<загрязнитель>)\nКомментарий: <коротко>\n\n<b>☀️ Погода</b>\nТемпература: <°C>\nОблачность: <описание>\nДавление: <hPa>\nВетер: <м/с> (<направление>)\n\n<b>🌊 Температура моря</b>\nСейчас: <°C> — опусти, если None\n\n<b>🌿 Пыльца</b>\nДеревья/Травы/Сорняки: <0‑5>/<0‑5>/<0‑5> — опусти, если None\n\n<b>🌌 Геомагнитика</b>\nKp‑индекс: <число> — опусти, если None\n\n<b>📈 Резонанс Шумана</b>\nАмплитуда: <значение> — опусти, если None\n\n<b>🔮 Астрология</b>\n<событие> — опусти, если None\n\n<b>✅ Рекомендация</b>\nКороткая позитивная фраза.\n\nТолько тег <b>, без <html>/<body>, символ \n = перенос"""

    prompt = "Сформатируй красиво по данным:\n" + json.dumps(raw, ensure_ascii=False, indent=2)

    resp = client
