"""
post.py – VayboМетр v3.0
Формирует Markdown-дайджест ровно в стиле «идеального» сообщения.

* Все блоки собираются в Python и складываются в строгий шаблон
* GPT используется ТОЛЬКО для абзаца «Вывод» и списка «Рекомендации»
* Никаких <br>, &nbsp;, HTML-тегов — Telegram получает Markdown
* Пустые блоки (None) отбрасываются, числовые значения конвертируются

GitHub Secrets (обязательно):
  OPENAI_API_KEY  TELEGRAM_TOKEN  CHANNEL_ID   # -100…
  OWM_KEY         AIRVISUAL_KEY   TOMORROW_KEY

requirements.txt:
  requests python-dateutil openai python-telegram-bot pyswisseph
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import requests
import swisseph as swe                     # pip install pyswisseph
from dateutil import tz
from openai import OpenAI
from telegram import Bot, error as tg_err

LAT, LON = 34.707, 33.022                  # Limassol marina
TZ = tz.gettz("Asia/Nicosia")


# ─────────────────── HTTP helper ────────────────────
def _get(url: str, **params) -> Optional[dict]:
    try:
        r = requests.get(url, params=params, timeout=20)
        r.raise_for_status()
        return r.json()
    except Exception as exc:                 # noqa: BLE001
        print(f"[warn] {url} → {exc}", file=sys.stderr)
        return None


# ─────────────────── Data sources ───────────────────
def get_weather() -> Optional[dict]:
    key = os.getenv("OWM_KEY")
    if key:
        for ver in ("3.0", "2.5"):
            data = _get(
                f"https://api.openweathermap.org/data/{ver}/onecall",
                lat=LAT, lon=LON, appid=key,
                units="metric", exclude="minutely,hourly,alerts",
            )
            if data and data.get("current"):
                return data
    # fallback: Open-Meteo
    return _get(
        "https://api.open-meteo.com/v1/forecast",
        latitude=LAT, longitude=LON,
        current_weather=True,
    )


def get_air() -> Optional[dict]:
    key = os.getenv("AIRVISUAL_KEY")
    if not key:
        return None
    return _get(
        "https://api.airvisual.com/v2/nearest_city",
        lat=LAT, lon=LON, key=key
    )


def get_pollen() -> Optional[dict]:
    key = os.getenv("TOMORROW_KEY")
    if not key:
        return None
    data = _get(
        "https://api.tomorrow.io/v4/timelines",
        apikey=key, location=f"{LAT},{LON}",
        fields="treeIndex,grassIndex,weedIndex",
        timesteps="1d", units="metric",
    )
    try:
        v = data["data"]["timelines"][0]["intervals"][0]["values"]
        return {"tree": v["treeIndex"], "grass": v["grassIndex"], "weed": v["weedIndex"]}
    except Exception:
        return None


def get_sst() -> Optional[float]:
    data = _get(
        "https://marine-api.open-meteo.com/v1/marine",
        latitude=LAT, longitude=LON,
        hourly="sea_surface_temperature", timezone="UTC",
    )
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


def get_astro_event() -> Optional[str]:
    today = datetime.utcnow()
    jd = swe.julday(today.year, today.month, today.day)
    lon_ven = swe.calc_ut(jd, swe.VENUS)[0][0]
    lon_sat = swe.calc_ut(jd, swe.SATURN)[0][0]
    diff = abs((lon_ven - lon_sat + 180) % 360 - 180)
    if diff < 3:
        return "Конъюнкция Венеры и Сатурна — фокус на отношениях"
    return None


# ───────── GPT: вывод + рекомендации ──────────
def gpt_comment(weather_json: dict) -> tuple[str, str]:
    client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
    prompt = (
        "На основе этих погодных данных дай один абзац вывода "
        "и 4–5 коротких советов для самочувствия. Формат:\n"
        "Вывод: ...\n"
        "Советы:\n- ...\n- ...\n" +
        json.dumps(weather_json, ensure_ascii=False)
    )
    rsp = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.5,
    )
    text = rsp.choices[0].message.content.strip()
    if "Советы:" in text:
        summary, tips_block = text.split("Советы:", 1)
        tips = "\n".join(f"- {t.strip('- ')}" for t in tips_block.strip().splitlines() if t.strip())
        return summary.replace("Вывод:", "").strip(), tips
    return text, ""


# ───────── Digest builder (Markdown) ──────────
def build_md(data: Dict[str, Any]) -> str:
    parts: list[str] = []

    # --- Погода ---
    w = data["weather"]
    if w:
        if "current" in w:                      # OpenWeather
            cur = w["current"]
            temp = round(cur["temp"])
            wind = cur["wind_speed"] * 3.6      # m/s → km/h
            cloud = cur.get("clouds", "—")
            pres = cur.get("pressure", "—")
            parts += [
                "☀️ **Погода**",
                f"**Температура:** днём до {temp} °C",
                f"**Облачность:** {cloud} %",
                f"**Ветер:** {wind:.1f} км/ч",
                f"**Давление:** {pres} гПа",
            ]
        else:                                   # Open-meteo
            cw = w["current_weather"]
            parts += [
                "☀️ **Погода**",
                f"**Температура:** {cw['temperature']} °C",
                f"**Ветер:** {cw['windspeed']:.1f} км/ч",
            ]

    # --- Качество воздуха ---
    air = data["air"]
    if air:
        pol = air["data"]["current"]["pollution"]
        parts += [
            "",
            "🌬️ **Качество воздуха**",
            f"**AQI:** {pol['aqius']}  |  **PM2.5:** {pol['aqius']}",
        ]

    # --- Пыльца ---
    p = data["pollen"]
    if p:
        parts += [
            "",
            "🌿 **Уровень пыльцы**",
            f"**Деревья:** {p['tree']}  |  **Травы:** {p['grass']}  |  **Сорняки:** {p['weed']}",
        ]

    # --- Геомагнитика ---
    kp = data["kp"]
    if kp is not None:
        status = "низкий" if kp < 4 else "повышенный"
        parts += [
            "",
            "🌌 **Геомагнитная активность**",
            f"**Уровень:** {status} (Kp {kp:.1f})",
        ]

    # --- Шуман ---
    sch = data["schumann"]
    if sch:
        parts += [
            "",
            "📈 **Резонанс Шумана**",
            f"**Частота:** ≈{sch['freq']:.1f} Гц",
            f"**Амплитуда:** {sch['amp']}",
        ]

    # --- Море ---
    if data["sst"]:
        parts += [
            "",
            "🌊 **Температура воды в море**",
            f"**Сейчас:** {data['sst']} °C",
        ]

    # --- Астрология ---
    if data["astro"]:
        parts += ["", "🔮 **Астрологические события**", data["astro"]]

    parts.append("---")

    # GPT-вывод / рекомендации
    summary, tips = gpt_comment(w or {})
    parts += ["### 📝 Вывод", summary, "", "---", "### ✅ Рекомендации", tips]

    return "\n".join(parts)


# ───────── Telegram send ─────────
async def send_markdown(md: str) -> None:
    bot = Bot(os.environ["TELEGRAM_TOKEN"])
    await bot.send_message(chat_id=os.environ["CHANNEL_ID"],
                           text=md[:4096], parse_mode="Markdown",
                           disable_web_page_preview=True)


# ───────── Main ─────────
async def main() -> None:
    raw = {
        "weather": get_weather(),
        "air": get_air(),
        "pollen": get_pollen(),
        "sst": get_sst(),
        "kp": get_kp(),
        "schumann": get_schumann(),
        "astro": get_astro_event(),
    }
    print("RAW slice:", json.dumps(raw, ensure_ascii=False)[:400])
    md = build_md(raw)
    print("MD snippet:", md[:200].replace("\n", " | "))
    try:
        await send_markdown(md)
        print("✓ sent")
    except tg_err.TelegramError as exc:
        print("Telegram error:", exc, file=sys.stderr)
        raise


if __name__ == "__main__":
    asyncio.run(main())
