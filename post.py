"""
post.py – VayboМетр Cyprus v5.0 (night-before edition)

• Сообщение формируется вечером (21:00 по Asia/Nicosia) и описывает ЗАВТРА.
• Если ≥40 % часов завтра с weathercode 45/48 → сообщаем про туман.
• Выводим, где завтра теплее всего / холоднее всего на Кипре
  (Лимассол, Ларнака, Никосия, Пафос).
"""

from __future__ import annotations
import asyncio, os, sys, math
from datetime import datetime, timedelta
from typing import Dict, Any, Optional

import requests, swisseph as swe
from openai import OpenAI
from telegram import Bot, error as tg_err

# --- География -----------------------------------------------------------
HOME = ("Лимассол", 34.707, 33.022)
CITIES = {
    "Лимассол": (34.707, 33.022),
    "Ларнака":  (34.916, 33.613),
    "Никосия":  (35.166, 33.366),
    "Пафос":    (34.776, 32.429),
}

# --- HTTP helper ---------------------------------------------------------
def _get(url: str, **params) -> Optional[dict]:
    try:
        r = requests.get(url, params=params, timeout=20)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        print(f"[warn] {url} -> {e}", file=sys.stderr)
        return None

# --- Tomorrow's weather for one point ------------------------------------
def forecast_tomorrow(lat: float, lon: float) -> dict:
    tomorrow = datetime.utcnow().date() + timedelta(days=1)
    iso_start = tomorrow.isoformat()
    iso_end   = (tomorrow + timedelta(days=1)).isoformat()
    d = _get("https://api.open-meteo.com/v1/forecast",
             latitude=lat, longitude=lon, timezone="UTC",
             hourly="temperature_2m,weathercode",
             daily="temperature_2m_max,temperature_2m_min",
             start_date=iso_start, end_date=iso_end)
    if not d:
        raise RuntimeError("No forecast")
    return d

# --- Fog probability for Limassol ---------------------------------------
def fog_risk(hourly_codes: list[int]) -> bool:
    fog_hours = sum(1 for code in hourly_codes if code in (45, 48))
    return fog_hours / 24 >= 0.40  # ≥ 40 %

# --- Hot / Cold city -----------------------------------------------------
def hot_cold() -> tuple[str, float, str, float]:
    results = []
    for name, (lat, lon) in CITIES.items():
        try:
            daily = forecast_tomorrow(lat, lon)["daily"]
            t_max = daily["temperature_2m_max"][0]
            results.append((name, t_max))
        except Exception:
            continue
    hot = max(results, key=lambda x: x[1])
    cold = min(results, key=lambda x: x[1])
    return hot[0], hot[1], cold[0], cold[1]

# --- Build message -------------------------------------------------------
def build_message() -> str:
    name, lat, lon = HOME
    data = forecast_tomorrow(lat, lon)
    daily = data["daily"]
    hourly_codes = data["hourly"]["weathercode"]
    fog = fog_risk(hourly_codes)

    hot_city, hot_temp, cold_city, cold_temp = hot_cold()

    P = []
    P += ["☀️ <b>Погода завтра</b>",
          f"<b>Темп. днём:</b> до {daily['temperature_2m_max'][0]:.0f} °C",
          f"<b>Темп. ночью:</b> около {daily['temperature_2m_min'][0]:.0f} °C"]
    if fog:
        P.append("В первой половине дня велика вероятность тумана 🌫️")
    P.append(f"Самое тёплое место: {hot_city} ({hot_temp:.1f} °C)")
    P.append(f"Самое прохладное: {cold_city} ({cold_temp:.1f} °C)")

    # короткий вывод и советы через GPT
    culprit = "туман" if fog else "капризы погоды"
    summary, tips = gpt_blurb(culprit)
    P += ["---", "<b>📝 Вывод</b>", summary,
          "", "<b>✅ Рекомендации</b>", tips]
    return "\n".join(P)

# --- GPT short block -----------------------------------------------------
def gpt_blurb(culprit: str) -> tuple[str,str]:
    prompt=(f"Одной строкой: 'Если завтра что-то пойдёт не так, вините {culprit}.'"
            " + короткий позитив (≤12 слов). Затем пустая строка и 3 весёлых bullet-совета, ≤12 слов.")
    res = OpenAI(api_key=os.getenv("OPENAI_API_KEY")).chat.completions.create(
        model="gpt-4o-mini", temperature=0.6,
        messages=[{"role":"user","content":prompt}]
    ).choices[0].message.content.strip().splitlines()
    lines=[l.strip() for l in res if l.strip()]
    summary=lines[0]
    tips="\n".join(f"- {l.lstrip('-• ').strip()}" for l in lines[1:4])
    return summary, tips

# --- Telegram send -------------------------------------------------------
async def send(text: str):
    await Bot(os.getenv("TELEGRAM_TOKEN")).send_message(
        chat_id=os.getenv("CHANNEL_ID"),
        text=text[:4096], parse_mode="HTML",
        disable_web_page_preview=True)

# --- main ----------------------------------------------------------------
async def main():
    md = build_message()
    print("Preview:", md.replace("\n"," | ")[:300])
    try:
        await send(md)
        print("✓ sent")
    except tg_err.TelegramError as e:
        print("Telegram error:", e, file=sys.stderr)
        raise

if __name__ == "__main__":
    asyncio.run(main())
