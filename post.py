"""Automated daily VayboМетр poster – v1.4 (Open‑Meteo SST)

Собирает данные для Лимассола и шлёт оформленный дайджест в Telegram:
  • Погода (OpenWeather One Call 3.0)
  • Качество воздуха (IQAir / AirVisual)
  • Пыльца (Tomorrow.io)
  • Kp‑индекс (NOAA SWPC)
  • Резонанс Шумана (HeartMath)
  • Температура моря (Open‑Meteo Marine API)

GitHub Secrets (обязательные):
  OPENAI_API_KEY   – OpenAI
  TELEGRAM_TOKEN   – Telegram bot token
  CHANNEL_ID       – @username или chat_id канала
  OWM_KEY          – OpenWeather
  AIRVISUAL_KEY    – IQAir / AirVisual
  TOMORROW_KEY     – Tomorrow.io

Python deps (workflow уже ставит): requests, python-dateutil, openai, python-telegram-bot
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import sys
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import requests
from dateutil import tz
from telegram import Bot
from openai import OpenAI

LAT = 34.707  # Limassol Marina
LON = 33.022
LOCAL_TZ = tz.gettz("Asia/Nicosia")

# ---------------------------------------------------------------------------
# HTTP helper ----------------------------------------------------------------
# ---------------------------------------------------------------------------

def _get(url: str, **params) -> Optional[dict]:
    """GET → .json | None, тихо логируя ошибку."""
    try:
        r = requests.get(url, params=params, timeout=15)
        r.raise_for_status()
        return r.json()
    except Exception as exc:  # noqa: BLE001, S110
        print(f"[warn] {url} failed: {exc}", file=sys.stderr)
        return None


# ---------------------------------------------------------------------------
# Data sources ---------------------------------------------------------------
# ---------------------------------------------------------------------------

def get_weather() -> Optional[dict]:
    key = os.getenv("OWM_KEY")
    if not key:
        return None
    return _get(
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
    return _get(
        "https://api.tomorrow.io/v4/timelines",
        apikey=key,
        location=f"{LAT},{LON}",
        fields="treeIndex,grassIndex,weedIndex",
        timesteps="1d",
        units="metric",
    )


def get_geomagnetic() -> Optional[dict]:
    data = _get("https://services.swpc.noaa.gov/products/noaa-planetary-k-index.json")
    if not data or len(data) < 2:
        return None
    try:
        ts, kp = data[-1]
        return {"kp": float(kp)}
    except Exception:
        return None


def get_schumann() -> Optional[dict]:
    return _get("https://api.glcoherence.org/v1/earth")


# 🌊 Sea‑surface temperature via Open‑Meteo (free, no key) -------------------

def get_sst() -> Optional[dict]:
    data = _get(
        "https://marine-api.open-meteo.com/v1/marine",
        latitude=LAT,
        longitude=LON,
        hourly="sea_surface_temperature",
        timezone="UTC",
    )
    try:
        sst = data["hourly"]["sea_surface_temperature"][0]
        return {"sst": round(float(sst), 1)}
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Collect all raw data -------------------------------------------------------
# ---------------------------------------------------------------------------

def collect() -> Dict[str, Any]:
    return {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "weather": get_weather(),
        "air": get_air_quality(),
        "pollen": get_pollen(),
        "geomagnetic": get_geomagnetic(),
        "schumann": get_schumann(),
        "sst": get_sst(),
    }


# ---------------------------------------------------------------------------
# OpenAI prettifier ----------------------------------------------------------
# ---------------------------------------------------------------------------

def prettify(raw: Dict[str, Any]) -> str:
    client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])

    system_msg = """
Ты — VayboМетр‑поэт. Сделай HTML‑дайджест для Telegram. Формат строго такой:

<b>🗺️ Локация</b>
Город: Limassol
Страна: Cyprus

<b>🌬️ Качество воздуха</b>
AQI (US): <число> (<главный загрязнитель>)
Комментарий: <коротко>

<b>☀️ Погода</b>
Температура: <°C>
Облачность: <описание>
Давление: <hPa>
Ветер: <м/с> (<направление>)

<b>🌊 Температура моря</b>
Сейчас: <°C>  — если нет, опусти этот блок.

<b>🌿 Пыльца</b>
Деревья/Травы/Сорняки: <0‑5>/<0‑5>/<0‑5>  — если нет, опусти.

<b>🌌 Геомагнитика</b>
Kp‑индекс: <число> (спокойно/буря) — если нет, опусти.

<b>📈 Резонанс Шумана</b>
Амплитуда: <значение> — если нет, опусти.

<b>✅ Рекомендация</b>
Короткая позитивная фраза.

Правила: без ```code```, без <html>/<body>. Символ \n = новый абзац.
"""

    user_msg = "Сформатируй красиво:\n" + json.dumps(raw, ensure_ascii=False, indent=2)

    resp = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": system_msg},
            {"role": "user", "content": user_msg},
        ],
        temperature=0.4,
    )
    text = resp.choices[0].message.content.strip()

    # sanitation
    text = re.sub(r"^```[\s\S]*?\n|\n```$", "", text)
    text = text.replace("\\n", "\n")
    text = re.sub(r"(?i)<!DOCTYPE[^>]*>", "", text)
    text = re.sub(r"(?i)</?(html|body)[^>]*>", "", text)
    return text.strip()


# ---------------------------------------------------------------------------
# Telegram ------------------------------------------------------------------
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
