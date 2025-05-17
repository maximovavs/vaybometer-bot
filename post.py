#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
post.py  • формирует и отправляет ежедневный анонс погоды/здоровья
"""

from __future__ import annotations

import os, asyncio, logging, requests
from typing import Optional, Tuple, Dict, Any

import pendulum
from telegram import Bot, error as tg_err

from utils import (
    compass, clouds_word, wind_phrase, safe,
    pressure_trend, get_fact, aqi_color,
    WEATHER_ICONS, AIR_EMOJI
)
from weather  import get_weather
from air      import get_air, get_pollen, get_sst, get_kp
from schumann import get_schumann
from astro    import astro_events
from gpt      import gpt_blurb    # та же заглушка, берёт советы из CULPRITS

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

# ─────────── CONSTANTS ────────────────────────────────────────────
TZ           = pendulum.timezone("Asia/Nicosia")
TODAY        = pendulum.now(TZ).date()
TOMORROW     = TODAY.add(days=1)
TOKEN        = os.environ["TELEGRAM_TOKEN"]
CHAT_ID      = int(os.environ["CHANNEL_ID"])
UNSPLASH_KEY = os.getenv("UNSPLASH_KEY")

POLL_QUESTION = "Как сегодня ваше самочувствие? 🤔"
POLL_OPTIONS  = ["🔥 Полон(а) энергии", "🙂 Нормально",
                 "😴 Слегка вялый(ая)", "🤒 Всё плохо"]

CITIES: dict[str, tuple[float, float]] = {
    "Limassol": (34.707, 33.022),
    "Larnaca" : (34.916, 33.624),
    "Nicosia" : (35.170, 33.360),
    "Pafos"   : (34.776, 32.424),
}

# ─────────── FETCH T° TOMORROW (надёжный) ────────────────────────
from utils import _get                      # retry-обёртка уже есть

def fetch_tomorrow_temps(lat: float, lon: float) -> Tuple[float | None, float | None]:
    """Возвращает (t_max, t_min) на завтра. fallback → get_weather()."""
    date = TOMORROW.to_date_string()
    j = _get(
        "https://api.open-meteo.com/v1/forecast",
        latitude=lat, longitude=lon, timezone="UTC",
        daily="temperature_2m_max,temperature_2m_min",
        start_date=date, end_date=date
    )
    if j and "daily" in j:
        try:
            d = j["daily"]["temperature_2m_max"][0]
            n = j["daily"]["temperature_2m_min"][0]
            return d, n
        except Exception:
            pass   # упадём к плану Б

    # fallback: тащим из get_weather()
    w = get_weather(lat, lon)
    if not w:
        return None, None
    daily = w["daily"]
    if isinstance(daily, dict):             # open-meteo dict-массы
        d = daily["temperature_2m_max"][1] if len(daily["temperature_2m_max"]) > 1 else daily["temperature_2m_max"][0]
        n = daily["temperature_2m_min"][1] if len(daily["temperature_2m_min"]) > 1 else daily["temperature_2m_min"][0]
    else:                                   # list (open-meteo|openweather)
        blk = daily[1] if len(daily) > 1 else daily[0]
        if "temp" in blk:                   # openweather
            d, n = blk["temp"]["max"], blk["temp"]["min"]
        else:                               # open-meteo list-of-dicts
            d, n = blk["temperature_2m_max"][-1], blk["temperature_2m_min"][0]
    return d, n

# ─────────── BUILD MESSAGE ───────────────────────────────────────
def build_msg() -> str:
    P: list[str] = []

    # —— Температуры Limassol (на завтра)
    lim_lat, lim_lon = CITIES["Limassol"]
    day_max, night_min = fetch_tomorrow_temps(lim_lat, lim_lon)
    if day_max is None or night_min is None:
        raise RuntimeError("Не удалось получить температуру Limassol")

    # —— Текущие условия Limassol
    w = get_weather(lim_lat, lim_lon)
    if not w:
        raise RuntimeError("Источники погоды недоступны")

    cur = w["current"]                       # всегда есть после унификации
    wind_kmh  = cur["windspeed"]
    wind_deg  = cur["winddirection"]
    press     = cur["pressure"]
    cloud_w   = clouds_word(cur.get("clouds", 0))
    strong    = w.get("strong_wind", False)
    fog       = w.get("fog_alert",   False)

    # —— Средняя t° по 4 городам
    temps_all: list[tuple[float, float]] = []
    for la, lo in CITIES.values():
        d, n = fetch_tomorrow_temps(la, lo)
        if d is not None and n is not None:
            temps_all.append((d, n))
    avg_day   = sum(x[0] for x in temps_all) / len(temps_all)
    avg_night = sum(x[1] for x in temps_all) / len(temps_all)

    # —— Шапка
    icon = WEATHER_ICONS.get(cloud_w, "🌦️")
    P.append(f"{icon} Добрый вечер! Погода на завтра на Кипре ({TOMORROW.format('DD.MM.YYYY')})")
    P.append(f"🌡 Средняя темп.: {avg_day:.0f} °C")
    P.append(f"📈 Темп. днём/ночью: {day_max:.1f}/{night_min:.1f} °C")
    P.append(f"🌤 Облачность: {cloud_w}")
    P.append(f"💨 Ветер: {wind_phrase(wind_kmh)} ({wind_kmh:.1f} км/ч, {compass(wind_deg)})")
    trend = pressure_trend(w)
    P.append(f"🔽 Давление: {press:.0f} гПа {trend}")
    if strong: P.append("⚠️ Возможны порывы ветра >30 км/ч")
    if fog:    P.append("🌁 Возможен туман — осторожно на дороге")
    P.append("———")

    # —— Рейтинг городов
    city_t: list[tuple[str, float, float]] = []
    for city, (la, lo) in CITIES.items():
        d, n = fetch_tomorrow_temps(la, lo)
        if d is None or n is None:
            continue
        city_t.append((city, d, n))
    city_t.sort(key=lambda x: x[1], reverse=True)

    medals = ["🥇", "🥈", "🥉", "4️⃣"]
    P.append("🎖️ Рейтинг городов (дн./ночь)")
    for i, (c, d, n) in enumerate(city_t[:4]):
        P.append(f"{medals[i]} {c}: {d:.1f}/{n:.1f} °C")
    P.append("———")

    # —— AQI + Pollen
    air = get_air() or {"aqi":"н/д","lvl":"н/д","pm25":"н/д","pm10":"н/д"}
    pm   = lambda v: f"{v:.0f}" if v not in (None, "н/д") else "н/д"
    P.append("🏙️ Качество воздуха")
    P.append(f"{AIR_EMOJI.get(air['lvl'],'⚪')} {air['lvl']} (AQI {air['aqi']}) | "
             f"PM2.5: {pm(air['pm25'])} µg/м³ | PM10: {pm(air['pm10'])} µg/м³")

    pol = get_pollen()
    if pol:
        idx = ["нет","низкий","умеренный","высокий","оч. высокий","экстрим"].__getitem__
        P.append("🌿 Пыльца")
        P.append(f"Деревья – {idx(round(pol['treeIndex']))} | "
                 f"Травы – {idx(round(pol['grassIndex']))} | "
                 f"Сорняки – {idx(round(pol['weedIndex']))}")
    P.append("———")

    # —— Геомагнитка · Шуман · SST · Астро
    kp, _ = get_kp()
    k_color = "🟢" if kp is not None and kp < 4 else ("🟡" if kp and kp < 6 else "🔴")
    kp_disp = f"{kp:.1f}" if kp is not None else "н/д"
    P.append(f"{k_color} Геомагнитка Kp={kp_disp}")

    sch = get_schumann()
    if "freq" in sch:
        P.append(f"🎵 Шуман: {sch['freq']:.1f} Гц – фон {'⚡️ повышен' if sch['high'] else 'в норме'}")
    else:
        P.append(f"🎵 Шуман: {sch['msg']}")

    sst = get_sst()
    if sst is not None:
        P.append(f"🌊 Темп. воды: {sst:.1f} °C (Open-Meteo)")

    astro = astro_events()
    if astro:
        P.append("🌌 Астрособытия – " + " | ".join(astro))
    P.append("———")

    # —— Culprit + tips
    culprit = "магнитные бури" if kp and kp >= 5 else \
              "туман"          if fog             else \
              "низкое давление" if press < 1007   else \
              "шальной ветер"   if strong         else \
              "мини-парад планет"
    summary, tips = gpt_blurb(culprit)
    P.append(f"📜 Вывод\n{summary}")
    P.append("———")
    P.append("✅ Рекомендации")
    P += [f"• {t}" for t in tips]
    P.append("———")
    P.append(f"📚 {get_fact(TOMORROW)}")

    return "\n".join(P)

# ─────────── SEND & MAIN ─────────────────────────────────────────
async def send_main_post(bot: Bot) -> None:
    html = build_msg()
    logging.info("Preview: %s", html.replace("\n", " | ")[:250])
    await bot.send_message(
        CHAT_ID,
        html,
        parse_mode="HTML",
        disable_web_page_preview=True
    )
    logging.info("Message sent ✓")

async def send_poll_if_friday(bot: Bot) -> None:
    if pendulum.now(TZ).weekday() == 4:
        await bot.send_poll(
            CHAT_ID, question=POLL_QUESTION, options=POLL_OPTIONS,
            is_anonymous=False, allows_multiple_answers=False
        )

async def fetch_unsplash_photo() -> Optional[str]:
    if not UNSPLASH_KEY:
        return None
    j = requests.get(
        "https://api.unsplash.com/photos/random",
        params={"query":"cyprus coast sunset","client_id":UNSPLASH_KEY},
        timeout=15
    ).json()
    return j.get("urls", {}).get("regular")

async def send_photo(bot: Bot, photo_url: str) -> None:
    await bot.send_photo(CHAT_ID, photo=photo_url, caption="Фото дня • Unsplash")

async def main() -> None:
    bot = Bot(token=TOKEN)
    await send_main_post(bot)
    await send_poll_if_friday(bot)
    if UNSPLASH_KEY and (TODAY.day % 3 == 0):
        if photo := await fetch_unsplash_photo():
            await send_photo(bot, photo)
    logging.info("All tasks done ✓")

if __name__ == "__main__":
    asyncio.run(main())
