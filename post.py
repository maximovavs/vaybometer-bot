"""post.py – VayboМетр v2.3 (stable debug)

Собирает данные о вайбе Лимассола и постит HTML‑дайджест в Telegram.
Включает подробные принты, чтобы в логах Actions было видно:
  • RAW‑JSON (обрезанный)   • первый фрагмент HTML   • успех/ошибку Telegram.

Источники данных (все бесплатные):
  ☀️  Погода             — OpenWeather One Call 3.0 ➜ 2.5 ➜ Open‑Meteo fallback
  🌬️  Качество воздуха   — IQAir / AirVisual
  🌿  Пыльца              — Tomorrow.io
  🌊  Температура моря    — Open‑Meteo Marine
  🌌  Kp‑индекс           — NOAA SWPC
  📈  Шуман               — Global Coherence (может тайм‑аутиться)
  🔮  Астрология          — Swiss‑Ephemeris (локально)

GitHub Secrets (обязательные):
  OPENAI_API_KEY   TELEGRAM_TOKEN   CHANNEL_ID ("-100…")
  OWM_KEY          AIRVISUAL_KEY    TOMORROW_KEY

requirements.txt должен содержать:
  requests python-dateutil openai python-telegram-bot pyswisseph
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
import swisseph as swe  # pip install pyswisseph
from dateutil import tz
from openai import OpenAI
from telegram import Bot, error as tg_err

LAT = 34.707  # Limassol Marina
LON = 33.022
LOCAL_TZ = tz.gettz("Asia/Nicosia")

# ---------------------------------------------------------------------------
# Tiny HTTP helper -----------------------------------------------------------
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
# External data fetchers -----------------------------------------------------
# ---------------------------------------------------------------------------

def get_weather() -> Optional[dict]:
    key = os.getenv("OWM_KEY")
    if not key:
        return None
    # 1) One Call 3.0
    data = _get("https://api.openweathermap.org/data/3.0/onecall", lat=LAT, lon=LON,
                appid=key, units="metric", exclude="minutely,hourly,alerts")
    if data and data.get("current"):
        return data
    # 2) One Call 2.5
    data = _get("https://api.openweathermap.org/data/2.5/onecall", lat=LAT, lon=LON,
                appid=key, units="metric", exclude="minutely,hourly,alerts")
    if data and data.get("current"):
        return data
    # 3) Open‑Meteo fallback
    return _get("https://api.open-meteo.com/v1/forecast", latitude=LAT, longitude=LON,
                 current_weather=True)


def get_air_quality() -> Optional[dict]:
    key = os.getenv("AIRVISUAL_KEY")
    if not key:
        return None
    return _get("https://api.airvisual.com/v2/nearest_city", lat=LAT, lon=LON, key=key)


def get_pollen() -> Optional[dict]:
    key = os.getenv("TOMORROW_KEY")
    if not key:
        return None
    data = _get("https://api.tomorrow.io/v4/timelines", apikey=key, location=f"{LAT},{LON}",
                fields="treeIndex,grassIndex,weedIndex", timesteps="1d", units="metric")
    try:
        vals = data["data"]["timelines"][0]["intervals"][0]["values"]
        return {"tree": vals.get("treeIndex"), "grass": vals.get("grassIndex"), "weed": vals.get("weedIndex")}
    except Exception:
        return None


def get_sst() -> Optional[dict]:
    data = _get("https://marine-api.open-meteo.com/v1/marine", latitude=LAT, longitude=LON,
                hourly="sea_surface_temperature", timezone="UTC")
    try:
        temp = float(data["hourly"]["sea_surface_temperature"][0])
        return {"sst": round(temp, 1)}
    except Exception:
        return None


def get_geomagnetic() -> Optional[dict]:
    arr = _get("https://services.swpc.noaa.gov/products/noaa-planetary-k-index.json")
    try:
        return {"kp": float(arr[-1][1])}
    except Exception:
        return None


def get_schumann() -> Optional[dict]:
    data = _get("https://api.glcoherence.org/v1/earth")
    try:
        return {"amp": data["amplitude_1"], "freq": data["frequency_1"]}
    except Exception:
        return None


# 🔮 simple astro ------------------------------------------------------------

def get_astro() -> Optional[dict]:
    today = datetime.utcnow()
    jd = swe.julday(today.year, today.month, today.day)
    lon_ven = swe.calc_ut(jd, swe.VENUS)[0][0]  # first value of list
    lon_sat = swe.calc_ut(jd, swe.SATURN)[0][0]
    diff = abs((lon_ven - lon_sat + 180) % 360 - 180)  # 0‒180
    if diff < 3:
        return {"event": "Конъюнкция Венеры и Сатурна — фокус на отношениях"}
    return None


# ---------------------------------------------------------------------------
# Collect raw bundle ---------------------------------------------------------
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
# GPT prettifier -------------------------------------------------------------
# ---------------------------------------------------------------------------

def prettify(raw: Dict[str, Any]) -> str:
    client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
    system_msg = (
        "Ты — VayboМетр‑поэт. Сделай компактный HTML‑дайджест (только <b>, \n). "
        "Если блока нет — опусти."
    )
    prompt = "Сформатируй:\n" + json.dumps(raw, ensure_ascii=False, indent=2)
    resp = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "system", "content": system_msg}, {"role": "user", "content": prompt}],
        temperature=0.35,
    )
    text = resp.choices[0].message.content.strip()
    text = re.sub(r"^```[\s\S]*?\n|\n```$", "", text)
    text = text.replace("\\n", "\n")
    text = re.sub(r"(?i)<!DOCTYPE[^>]*>", "", text)
    text = re.sub(r"(?i)</?(html|body)[^>]*>", "", text)
    return text


# ---------------------------------------------------------------------------
# Telegram sender -----------------------------------------------------------
# ---------------------------------------------------------------------------

async def send(html: str) -> None:
    bot = Bot(token=os.environ["TELEGRAM_TOKEN"])
    await bot.send_message(chat_id=os.environ["CHANNEL_ID"], text=html[:4096],
                           parse_mode="HTML", disable_web_page_preview=True)


# ---------------------------------------------------------------------------
# main (with debug)
# ---------------------------------------------------------------------------

async def main() -> None:
    try:
        raw = collect()
        print("RAW:", json.dumps(raw, ensure_ascii=False)[:600])
        html = prettify(raw)
        print("HTML snippet:", html[:200].replace("\n", " | "))
        await send(html)
        print("✓ sent to Telegram")
    except tg_err.TelegramError as tg_exc:
        print("✗ Telegram API error:", tg_exc, file=sys.stderr)
        raise
    except Exception as exc:
        print("✗ ERROR:", exc, file=sys.stderr)
        raise


if __name__ == "__main__":
    asyncio.run(main())
