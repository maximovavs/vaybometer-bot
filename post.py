#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import asyncio
import logging
from typing import Any, Dict, Optional

import pendulum
from telegram import Bot, error as tg_err

from utils import (
    compass, clouds_word, wind_phrase, safe, get_fact,
    WEATHER_ICONS, AIR_EMOJI, WMO_DESCRIPTIONS
)
from weather import get_weather
from air import get_air, get_pollen
from schumann import get_schumann
from astro import astro_events
from gpt import gpt_blurb

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

# ─────────── Constants ────────────────────────────────────────────
TZ           = pendulum.timezone("Asia/Nicosia")
TODAY        = pendulum.now(TZ).date()
TOMORROW     = TODAY.add(days=1)
TOKEN        = os.environ["TELEGRAM_TOKEN"]
CHAT_ID      = int(os.environ["CHANNEL_ID"])

CITIES = {
    "Limassol": (34.707, 33.022),
    "Larnaca" : (34.916, 33.624),
    "Nicosia" : (35.170, 33.360),
    "Pafos"   : (34.776, 32.424),
}


def build_msg() -> str:
    P: list[str] = []

    # === 1) Погода на Кипре (среднее) ===
    temps = []
    for (la, lo) in CITIES.values():
        w = get_weather(la, lo)
        if w:
            # днём берем максимум, ночью минимум
            daily = w["daily"]
            if isinstance(daily, dict):
                ma = daily["temperature_2m_max"][1] if len(daily["temperature_2m_max"])>1 else daily["temperature_2m_max"][0]
                mi = daily["temperature_2m_min"][1] if len(daily["temperature_2m_min"])>1 else daily["temperature_2m_min"][0]
            else:
                blk = daily[1] if len(daily)>1 else daily[0]
                ma = blk["temperature_2m_max"][-1]
                mi = blk["temperature_2m_min"][0]
            temps.append((ma, mi))
    avg_day = sum(d for d,_ in temps)/len(temps)
    avg_night = sum(n for _,n in temps)/len(temps)

    # показываем прогноз для Лимассола
    w0 = get_weather(*CITIES["Limassol"])
    if not w0:
        raise RuntimeError("Нет погоды для Лимассола")
    # ── извлекаем текущие данные безопасно ──────────────
    cur = w0.get("current") or w0["current_weather"]

    wind_kmh = cur.get("windspeed") or cur.get("wind_speed") or 0.0
    wind_deg = cur.get("winddirection") or cur.get("wind_deg") or 0.0

    # давление бывает не в current – тогда берём из hourly
    press = (
        cur.get("pressure") or
        w0.get("hourly", {}).get("surface_pressure", [1013])[0]
    )

    # облачность тоже может отсутствовать
    clouds_pct = cur.get("clouds")
    if clouds_pct is None:
        clouds_pct = w0.get("hourly", {}).get("cloud_cover", [0])[0]
    cloud_w = clouds_word(clouds_pct)


    icon = WEATHER_ICONS.get(cloud_w, "🌦️")
    P.append(f"{icon} Добрый вечер! Погода на завтра на Кипре ({TOMORROW.format('DD.MM.YYYY')})")
    P.append(f"🌡️ Средняя темп.: {avg_day:.0f} °C")
    P.append(f"📈 Темп. днём/ночью: {avg_day:.1f} °C / {avg_night:.1f} °C")
    P.append(f"🌤 Облачность: {cloud_w}")
    P.append(f"💨 Ветер: {wind_phrase(wind_kmh)} ({wind_kmh:.1f} км/ч, {compass(wind_deg)})")
    P.append(f"🔽 Давление: {press:.0f} гПа")  # тут можно добавить ↑↓, если будет источник тренда

    P.append("———")

    # === 2) Рейтинг городов (дн./ночь) ===
    city_t = []
    for city,(la,lo) in CITIES.items():
        w = get_weather(la, lo)
        if not w: continue
        dblk = w["daily"]
        if isinstance(dblk, dict):
            d = dblk["temperature_2m_max"][1] if len(dblk["temperature_2m_max"])>1 else dblk["temperature_2m_max"][0]
            n = dblk["temperature_2m_min"][1] if len(dblk["temperature_2m_min"])>1 else dblk["temperature_2m_min"][0]
        else:
            blk = dblk[1] if len(dblk)>1 else dblk[0]
            d = blk["temperature_2m_max"][-1]
            n = blk["temperature_2m_min"][0]
        city_t.append((city, d, n))
    city_t.sort(key=lambda x: x[1], reverse=True)
    medals = ["🥇","🥈","🥉","4️⃣"]
    P.append("🎖️ Рейтинг городов (дн./ночь)")
    for i,(c,d,n) in enumerate(city_t[:4]):
        P.append(f"{medals[i]} {c}: {d:.1f}/{n:.1f} °C")

    P.append("———")

    # === 3) Качество воздуха + пыльца ===
    air = get_air() or {}
    P.append("🏙️ Качество воздуха")
    if air:
        P.append(f"{AIR_EMOJI[air['lvl']]} {air['lvl']} (AQI {air['aqi']}) | "
                 f"PM2.5: {safe(air['pm25'],'µg/м³')} | PM10: {safe(air['pm10'],'µg/м³')}")
    else:
        P.append("нет данных")
    pollen = get_pollen()
    if pollen:
        idx = lambda v: ["нет","низкий","умеренный","высокий","оч. высокий","экстрим"][int(round(v))]
        P.append("🌿 Пыльца")
        P.append(f"Деревья – {idx(pollen['treeIndex'])}, Травы – {idx(pollen['grassIndex'])}, "
                 f"Сорняки – {idx(pollen['weedIndex'])}")
    P.append("———")

    # === 4) Геомагнитка + Шуман + вода + астрособытия ===
    kp, kp_state = get_schumann  # исправьте, если get_kp
    # если get_kp:
    from air import get_kp
    kp, kp_state = get_kp()
    sch = get_schumann()
    sst = get_sst()
    astro = astro_events()

    # светофор для geomag
    emoji_k = {"спокойный":"🟢","повышенный":"🟡","буря":"🔴"}.get(kp_state, "⚪")
    P.append(f"🧲 Геомагнитка: {emoji_k} K-index {kp:.1f} ({kp_state})")

    if sch.get("high"):
        P.append(f"🎵 Шуман: {sch['freq']:.1f} Гц ⚡️ (повышено)")
    else:
        P.append(f"🎵 Шуман: {sch.get('freq','?'):.1f} Гц")

    if sst is not None:
        P.append(f"🌊 Темп. воды: {sst:.1f} °C (Open-Meteo)")

    # WMO: самый тяжёлый код из завтра
    codes = w0["daily"][1 if len(w0["daily"])>1 else 0].get("weathercode", [])
    if isinstance(codes, list): code = max(codes)
    else: code = codes
    desc = WMO_DESCRIPTIONS.get(code, "—")
    P.append(f"🔎 Макс. WMO-код: {code} — {desc}")

    if astro:
        # оставляем только фазу Луны + первое важное событие
        phase = astro[0]
        event = astro[1] if len(astro)>1 else ""
        P.append("🌌 Астрособытия: " + " | ".join([phase, event]))

    P.append("———")

    # === 5) Вывод и советы ===
    # выбираем «виновника» (пример из старого кода)
    if fog:
        culprit = "туман"
    elif kp_state == "буря":
        culprit = "магнитные бури"
    elif press < 1007:
        culprit = "низкое давление"
    elif wind_kmh > 30:
        culprit = "сильный ветер"
    else:
        culprit = "мини-парад планет"

    summary, tips = gpt_blurb(culprit)
    P.append(f"📜 Вывод\n{summary}")
    P.append("———")
    P.append("✅ Рекомендации")
    for t in tips:
        P.append(f"• {t}")
    P.append("———")
    P.append(f"📚 {get_fact(TOMORROW)}")

    return "\n".join(P)


async def main() -> None:
    bot = Bot(TOKEN)
    txt = build_msg()
    logging.info("Preview: %s", txt.replace("\n"," | ")[:200])
    try:
        await bot.send_message(
            CHAT_ID, txt, parse_mode="HTML", disable_web_page_preview=True
        )
    except tg_err.TelegramError as e:
        logging.error("Telegram error: %s", e)
        raise

if __name__ == "__main__":
    asyncio.run(main())
