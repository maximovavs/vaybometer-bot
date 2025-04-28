"""Automated daily VayboМетр poster – v1.1 (HTML‑safe)

Схема:
1. Собираем фактические данные (погода, AQI, пыльца, Kp, резонанс Шумана, SST).
2. Отдаём их OpenAI для лаконичного HTML‑дайджеста.
3. Скидываем в Telegram (@vaybometer).

Если какой‑то источник недоступен → пишем «нет данных».

Переменные окружения (GitHub Secrets):
  OPENAI_API_KEY     – ключ OpenAI
  TELEGRAM_TOKEN     – токен бота
  CHANNEL_ID         – id или @username канала
  OWM_KEY            – OpenWeather One Call 3.0
  AIRVISUAL_KEY      – IQAir / AirVisual
  AMBEE_KEY          – Ambee (пыльца)              [опц.]
  COPERNICUS_USER    – Copernicus Marine (SST)     [опц.]
  COPERNICUS_PASS    – Copernicus Marine пароль    [опц.]

Depends: requests, python‑dateutil, pendulum, astropy, openai, python‑telegram‑bot
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
# helpers to fetch raw data --------------------------------------------------
# ---------------------------------------------------------------------------

def _get(url: str, **params) -> Optional[dict]:
    """HTTP GET → json | None (и печать warning)."""
    try:
        r = requests.get(url, params=params, timeout=15)
        r.raise_for_status()
        return r.json()
    except Exception as exc:  # noqa: BLE001, S110
        print(f"[warn] {url} failed: {exc}", file=sys.stderr)
        return None


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
    key = os.getenv("AMBEE_KEY")
    if not key:
        return None
    headers = {"x-api-key": key}
    url = "https://api.ambeedata.com/latest/pollen/by-lat-lng"
    try:
        r = requests.get(url, params={"lat": LAT, "lng": LON}, headers=headers, timeout=15)
        r.raise_for_status()
        return r.json()
    except Exception as exc:  # noqa: BLE001
        print(f"[warn] ambee failed: {exc}", file=sys.stderr)
        return None


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


def get_sst() -> Optional[dict]:
    user = os.getenv("COPERNICUS_USER")
    pwd = os.getenv("COPERNICUS_PASS")
    if not user or not pwd:
        return None
    # TODO: мотуклиент. Пока затычка.
    return {"sst": "нет данных"}


# ---------------------------------------------------------------------------
# gather --------------------------------------------------------------------
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
# prettify via OpenAI --------------------------------------------------------
# ---------------------------------------------------------------------------

def prettify(raw: Dict[str, Any]) -> str:
    client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])

    system_msg = (
        "Ты — VayboМетр‑поэт. Сформатируй сообщение строго в HTML Telegram: "
        "используй только <b> для заголовков, эмодзи как в шаблоне, никакого <!DOCTYPE>, "
        "<html>, <body> и т.п. Цифры не искажай; если блока нет → 'нет данных'."
    )

    user_msg = (
        "Собери дайджест для Лимассола по этим данным и шаблону:\n" +
        json.dumps(raw, ensure_ascii=False, indent=2)
    )

    resp = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": system_msg},
            {"role": "user", "content": user_msg},
        ],
        temperature=0.4,
    )
    text: str = resp.choices[0].message.content.strip()

    # ─── sanitation for Telegram HTML ───────────────────────────────────────
    # 1) убрать возможные ```code```
    text = re.sub(r"^```[\s\S]*?\n|\n```$", "", text)
    # 2) литеральные \n → реальные переводы
    text = text.replace("\\n", "\n")
    # 3) убрать <!DOCTYPE>, <html>, <body>, их закрывающие теги
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
        text=html[:4096],  # safety cut
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
