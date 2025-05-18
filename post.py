#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
post.py
~~~~~~~
Формирует сообщение-сводку и шлёт его в Telegram-канал.

• средняя температура по 4 городам + отдельный рейтинг;
• стрелка тренда давления (utils.pressure_trend);
• полный блок качества воздуха с запасным API и цветными PM (utils.pm_color);
• пыльца из Open-Meteo Pollen;
• «светофор» геомагнитки (utils.kp_emoji);
• резонанс Шумана с трендом частоты;
• разнообразный «факт дня».
"""

from __future__ import annotations
import os, asyncio, logging, requests
from typing import Dict, Tuple, Optional, List

import pendulum
from telegram import Bot, error as tg_err

from utils import (
    compass, clouds_word, wind_phrase, safe, get_fact,
    WEATHER_ICONS, AIR_EMOJI,
    pressure_trend, kp_emoji, pm_color,
)
from weather   import get_weather
from air       import get_air, get_sst, get_kp
from pollen    import get_pollen
from schumann  import get_schumann, get_schumann_trend
from astro     import astro_events
from gpt       import gpt_blurb

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

# ──── константы ──────────────────────────────────────────────────
TZ         = pendulum.timezone("Asia/Nicosia")
TODAY      = pendulum.now(TZ).date()
TOMORROW   = TODAY.add(days=1)
TOKEN      = os.environ["TELEGRAM_TOKEN"]
CHAT_ID    = int(os.environ["CHANNEL_ID"])
UNSPLASH   = os.getenv("UNSPLASH_KEY")

CITIES = {
    "Limassol": (34.707, 33.022),
    "Larnaca" : (34.916, 33.624),
    "Nicosia" : (35.170, 33.360),
    "Pafos"   : (34.776, 32.424),
}

# ──── утилита для завтрашних max/min через Open-Meteo ────────────
def fetch_tomorrow_temps(lat: float, lon: float) -> Tuple[Optional[float], Optional[float]]:
    date = TOMORROW.to_date_string()
    j = requests.get(
        "https://api.open-meteo.com/v1/forecast",
        params=dict(
            latitude=lat, longitude=lon,
            timezone="UTC",
            daily="temperature_2m_max,temperature_2m_min",
            start_date=date, end_date=date,
        ),
        timeout=15,
    ).json()
    d = j.get("daily", {})
    tmax = d.get("temperature_2m_max", [None])[0]
    tmin = d.get("temperature_2m_min", [None])[0]
    return tmax, tmin

# ──── сборка сообщения ───────────────────────────────────────────
def build_msg() -> str:
    P: List[str] = []

    # 1) средние температуры
    t_vals: List[Tuple[float,float]] = []
    for la, lo in CITIES.values():
        d, n = fetch_tomorrow_temps(la, lo)
        if d is not None and n is not None:
            t_vals.append((d, n))
    avg_day   = sum(d for d,_ in t_vals) / len(t_vals)
    avg_night = sum(n for _,n in t_vals) / len(t_vals)

    # 2) базовый город (Лимассол) для текущих параметров
    lat0, lon0 = CITIES["Limassol"]
    w0 = get_weather(lat0, lon0)
    if not w0:
        raise RuntimeError("weather sources down")

    cur = w0.get("current") or w0["current_weather"]
    wind_kmh = cur["windspeed"]
    wind_deg = cur["winddirection"]
    press    = cur["pressure"]
    clouds   = clouds_word(cur.get("clouds",0))
    strong   = w0.get("strong_wind", False)
    fog      = w0.get("fog_alert",   False)

    # заголовок
    icon = WEATHER_ICONS.get(clouds,"🌦️")
    P += [
        f"{icon} <b>Добрый вечер! Погода на завтра на Кипре ({TOMORROW.format('DD.MM.YYYY')})</b>",
        f"🌡 Средняя темп.: {avg_day:.0f} °C",
        f"📈 Темп. днём/ночью: {avg_day:.1f} / {avg_night:.1f} °C",
        f"🌤 Облачность: {clouds}",
        f"💨 Ветер: {wind_phrase(wind_kmh)} ({wind_kmh:.1f} км/ч, {compass(wind_deg)})",
        f"🔽 Давление: {press:.0f} гПа {pressure_trend(w0)}",
    ]
    if strong: P.append("⚠️ Возможны порывы ветра до 30 км/ч+")
    if fog:    P.append("🌁 Ночью возможен туман — будьте внимательны на дорогах")
    P.append("———")

    # 3) рейтинг городов
    city_r: List[Tuple[str,float,float]] = []
    for c,(la,lo) in CITIES.items():
        d,n = fetch_tomorrow_temps(la,lo)
        if d is not None and n is not None:
            city_r.append((c,d,n))
    city_r.sort(key=lambda x: x[1], reverse=True)
    medals = ["🥇","🥈","🥉","4️⃣"]
    P.append("🎖️ <b>Рейтинг городов (дн./ночь)</b>")
    for i,(c,d,n) in enumerate(city_r[:4]):
        P.append(f"{medals[i]} {c}: {d:.1f}/{n:.1f} °C")
    P.append("———")

    # 4) воздух + пыльца
    air = get_air()
    P.append("🏙️ <b>Качество воздуха</b>")
    P.append(
        f"{AIR_EMOJI[air['lvl']]} {air['lvl'].capitalize()} "
        f"(AQI {air['aqi']}) | "
        f"PM₂.₅: {pm_color(air['pm25'])} | "
        f"PM₁₀: {pm_color(air['pm10'])}"
    )

    pol = get_pollen()
    if pol:
        risk = pol['risk']
        P += [
            "🌿 <b>Пыльца</b>",
            f"Деревья – {pol['tree']} | Травы – {pol['grass']} | "
            f"Сорняки – {pol['weed']} → риск: {risk}",
        ]
    P.append("———")

    # 5) геомагнитка, шуман, море
    kp, _ = get_kp()
    kp_txt = f"{kp:.1f}" if kp is not None else "н/д"
    P.append(f"{kp_emoji(kp or 0)} Геомагнитка Kp={kp_txt}")

    sch = get_schumann()
    if "freq" in sch:
        trend = get_schumann_trend(24)
        arrow = "↑" if trend==1 else "↓" if trend==-1 else "→"
        P.append(f"🎵 Шуман: {sch['freq']:.2f} Гц {arrow} – фон {'⚡️ высокий' if sch.get('high') else 'в норме'}")
    else:
        P.append("🎵 Шуман: нет данных")

    if (sst := get_sst()) is not None:
        P.append(f"🌊 Температура воды: {sst:.1f} °C")
    P.append("———")

    # 6) астрособытия
    astro = astro_events()
    if astro:
        P.append("🌌 <b>Астрособытия</b> — " + " | ".join(astro))
        P.append("———")

    # 7) вывод и советы
    culprit = "шальной ветер" if strong else "туман" if fog else "низкое давление" if press<1007 else "мини-парад планет"
    summary, tips = gpt_blurb(culprit)
    P += [f"📜 <b>Вывод</b>\n{summary}", "———", "✅ <b>Рекомендации</b>"]
    for t in tips:
        P.append(f"• {t}")
    P += ["———", f"📚 {get_fact(TOMORROW)}"]

    return "\n".join(P)

# ──── отправка ───────────────────────────────────────────────────
async def send_main(bot: Bot) -> None:
    html = build_msg()
    logging.info("Preview: %s", html.replace("\n"," | ")[:240])
    await bot.send_message(chat_id=CHAT_ID, text=html,
                           parse_mode="HTML",
                           disable_web_page_preview=True)

async def main() -> None:
    bot = Bot(TOKEN)
    await send_main(bot)

if __name__ == "__main__":
    asyncio.run(main())
