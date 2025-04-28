"""Automated daily VayboМетр poster

1. Скачивает фактические данные (погода, воздух, пыльца, геомагнитика, резонанс Шумана,
   температура моря) для Лимассола.
2. Отдаёт эти данные OpenAI‑модели, чтобы получить красиво оформленный дайджест
   с эмодзи‑заголовками (HTML для Telegram).
3. Шлёт результат в канал @vaybometer.

Требуемые переменные окружения (добавь в GitHub Secrets):
  OPENAI_API_KEY     – ключ OpenAI
  TELEGRAM_TOKEN     – токен бота
  CHANNEL_ID         – id или @username канала
  OWM_KEY            – OpenWeather One Call 3.0
  AIRVISUAL_KEY      – IQAir / AirVisual (качество воздуха)
  AMBEE_KEY          – Ambee (пыльца)              [опц.]
  COPERNICUS_USER    – Copernicus Marine (SST)     [опц.]
  COPERNICUS_PASS    – Copernicus Marine пароль    [опц.]

Библиотеки: requests, python-dateutil, pendulum, astropy, openai, python-telegram-bot
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
# helpers to fetch raw data
# ---------------------------------------------------------------------------

def _get(url: str, **params) -> Optional[dict]:
    """Universal tiny wrapper that returns parsed json or None."""
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
    return _get(url, lat=LAT, lng=LON, **{"☯": ""}) if headers else None  # noqa: RUF100


def get_geomagnetic() -> Optional[dict]:
    url = "https://services.swpc.noaa.gov/products/noaa-planetary-k-index.json"
    data = _get(url)
    if not data or len(data) < 2:
        return None
    # last value is [timestamp, kp]
    try:
        ts, kp = data[-1]
        return {"kp": float(kp)}
    except Exception:
        return None


def get_schumann() -> Optional[dict]:
    url = "https://api.glcoherence.org/v1/earth"
    return _get(url)


def get_sst() -> Optional[dict]:
    user = os.getenv("COPERNICUS_USER")
    pwd = os.getenv("COPERNICUS_PASS")
    if not user or not pwd:
        return None
    try:
        marine_url = (
            "https://my.cmems-du.eu/motu-web/Motu"
        )  # Copernicus extraction would need motuclient; placeholder
        return {"sst": "unavailable (placeholder)"}
    except Exception:
        return None


# ---------------------------------------------------------------------------
# collect everything into one dict
# ---------------------------------------------------------------------------

def collect_data() -> Dict[str, Any]:
    return {
        "weather": get_weather(),
        "air": get_air_quality(),
        "pollen": get_pollen(),
        "geomagnetic": get_geomagnetic(),
        "schumann": get_schumann(),
        "sst": get_sst(),
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
    }


# ---------------------------------------------------------------------------
# OpenAI prettifier
# ---------------------------------------------------------------------------

def prettify(raw: Dict[str, Any]) -> str:
    client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])

    system_msg = (
        "Ты — вайбометр‑поэт. Форматируй данные в строгом HTML шаблоне с тегом <b> в "
        "заголовках и эмодзи, но числовые значения не менять. Если какого‑то блока нет, "
        "напиши 'нет данных'."
    )

    user_msg = f"""Сформатируй красиво по шаблону:
{json.dumps(raw, ensure_ascii=False, indent=2)}"""

    resp = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": system_msg},
            {"role": "user", "content": user_msg},
        ],
        temperature=0.4,
    )

    text = resp.choices[0].message.content.strip()

    # снять возможные ```
    text = re.sub(r"^```[\s\S]*?\n|\n```$", "", text)
    # заменить литеральные \n на реальные переводы строк
    text = text.replace("\\n", "\n")
    return text


# ---------------------------------------------------------------------------
# Telegram sender
# ---------------------------------------------------------------------------

async def send_to_telegram(html: str) -> None:
    bot = Bot(token=os.environ["TELEGRAM_TOKEN"])
    await bot.send_message(
        chat_id=os.environ["CHANNEL_ID"],
        text=html,
        parse_mode="HTML",
        disable_web_page_preview=True,
    )


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

async def main() -> None:
    raw = collect_data()
    html = prettify(raw)
    await send_to_telegram(html)


if __name__ == "__main__":  # pragma: no cover
    asyncio.run(main())
