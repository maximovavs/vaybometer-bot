"""Automated daily VayboМетр poster – v1.2 (Tomorrow.io pollen)

Делает:
1. Берёт погоду, качество воздуха, пыльцу (через Tomorrow.io), K‑index, Шумана,
   SST (заглушка) — всё по Лимассолу.
2. Отдаёт JSON‑слепок в OpenAI ➜ получает красивый HTML‑дайджест.
3. Публикует в Telegram канал.

GitHub Secrets (обязательные/опц.):
  OPENAI_API_KEY   – OpenAI
  TELEGRAM_TOKEN   – Telegram bot
  CHANNEL_ID       – @username или chat_id
  OWM_KEY          – OpenWeather (One Call 3.0)
  AIRVISUAL_KEY    – IQAir / AirVisual
  TOMORROW_KEY     – Tomorrow.io (пыльца)
  COPERNICUS_USER  – Copernicus Marine (optional)
  COPERNICUS_PASS  – Copernicus Marine (optional)

Python deps: requests, python-dateutil, pendulum, astropy, openai, python-telegram-bot
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
# helpers -------------------------------------------------------------------
# ---------------------------------------------------------------------------

def _get(url: str, **params) -> Optional[dict]:
    """HTTP GET → json | None (с печатью warning)."""
    try:
        r = requests.get(url, params=params, timeout=15)
        r.raise_for_status()
        return r.json()
    except Exception as exc:  # noqa: BLE001, S110
        print(f"[warn] {url} failed: {exc}", file=sys.stderr)
        return None


# ☀️ Weather ----------------------------------------------------------------

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


# 🌬 Air Quality -------------------------------------------------------------

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


# 🌿 Pollen via Tomorrow.io --------------------------------------------------

def get_pollen() -> Optional[dict]:
    key = os.getenv("TOMORROW_KEY")
    if not key:
        return None
    return _get(
        "https://api.tomorrow.io/v4/timelines",
        apikey=key,
        location=f"{LAT},{LON}",
        fields="treeIndex,grassIndex,weedIndex",  # 0‒5 scale
        timesteps="1d",
        units="metric",
    )


# 🌌 Geomagnetic -------------------------------------------------------------

def get_geomagnetic() -> Optional[dict]:
    data = _get("https://services.swpc.noaa.gov/products/noaa-planetary-k-index.json")
    if not data or len(data) < 2:
        return None
    try:
        ts, kp = data[-1]
        return {"kp": float(kp)}
    except Exception:
        return None


# 📈 Schumann ----------------------------------------------------------------

def get_schumann() -> Optional[dict]:
    return _get("https://api.glcoherence.org/v1/earth")


# 🌊 SST placeholder ---------------------------------------------------------

def get_sst() -> Optional[dict]:
    user = os.getenv("COPERNICUS_USER")
    pwd = os.getenv("COPERNICUS_PASS")
    if not user or not pwd:
        return None
    return {"sst": "нет данных"}  # TODO: motuclient


# ---------------------------------------------------------------------------
# collect -------------------------------------------------------------------
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
        "Ты — VayboМетр-поэт. Выведи дайджест ТОЛЬКО в таком виде (HTML Telegram):
"
        "<b>🗺️ Локация</b>
"
        "Город: Limassol
"
        "Страна: Cyprus

"

        "<b>🌬️ Качество воздуха</b>
"
        "AQI (US): <число> (<главный загрязнитель>)
"
        "Комментарий: <коротко человеческим языком>

"

        "<b>☀️ Погода</b>
"
        "Температура: <число>°C
"
        "Облачность: <описание>
"
        "Давление: <hPa>
"
        "Ветер: <число> м/с (<направление>)

"

        "<b>🌊 Температура моря</b>
"
        "Сейчас: <число>°C | если нет — 'нет данных'

"

        "<b>🌿 Пыльца</b> | опусти блок, если нет данных
"
        "Деревья: <0-5> | Травы: <0-5> | Сорняки: <0-5>

"

        "<b>🌌 Геомагнитика</b> | опусти, если нет данных
"
        "Kp‑индекс: <число> → короткий комментарий (спокойно/буря)

"

        "<b>📈 Резонанс Шумана</b> | опусти, если нет данных
"
        "Амплитуда: <значение>

"

        "<b>✅ Рекомендация</b>
"
        "Короткая позитивная фраза.

"

        "Без ```code```, без <html>/<body>, 
 — это перенос строки."
    ) по шаблону: "
        "заголовки = <b>+эмодзи, остальные строки текст, без <html>/<body>. "
        "Если блока нет — пропусти. Цифры из JSON не менять."
    )

    user_msg = "Сформатируй красиво:\n" + json.dumps(raw, ensure_ascii=False, indent=2)

    resp = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": system_msg},
            {"role": "user", "content": user_msg},
        ],
        temperature=0.4,
    )
    text: str = resp.choices[0].message.content.strip()

    # sanitation for Telegram
    text = re.sub(r"^```[\s\S]*?\n|\n```$", "", text)  # code fence
    text = text.replace("\\n", "\n")                   # literal \n → newline
    text = re.sub(r"(?i)<!DOCTYPE[^>]*>", "", text)      # doctype
    text = re.sub(r"(?i)</?(html|body)[^>]*>", "", text)  # html/body tags
    return text.strip()


# ---------------------------------------------------------------------------
# Telegram sender -----------------------------------------------------------
# ---------------------------------------------------------------------------

async def send(html: str) -> None:
    bot = Bot(token=os.environ["TELEGRAM_TOKEN"])
    await bot.send_message(
        chat_id=os.environ["CHANNEL_ID"],
        text=html[:4096],  # Telegram limit
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
