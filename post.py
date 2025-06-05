#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
post.py — вечерний пост VayboMeter-бота для Кипра.

– Публикует прогноз на завтра (температура, ветер, давление и т. д.)
– Рейтинг городов (топ-5 по дневной температуре) с SST (темп. моря) для каждого
– Качество воздуха + пыльца
– Геомагнитка + Шуман
– Астрособытия на завтра (VoC, фаза Луны, советы, next_event)
– Короткий вывод (динамический «Вините …»)
– Рекомендации (GPT-фоллбэк или health-coach) с тем же «виновником»
– Факт дня
"""

from __future__ import annotations
import os
import asyncio
import json
import logging
from pathlib import Path
from typing import Dict, Any, Tuple, List, Optional

import pendulum
from telegram import Bot, error as tg_err

from utils     import compass, clouds_word, get_fact, AIR_EMOJI, pm_color, kp_emoji
from weather   import get_weather, fetch_tomorrow_temps
from air       import get_air, get_sst, get_kp
from pollen    import get_pollen
from schumann  import get_schumann
from astro     import astro_events
from gpt       import gpt_blurb
from lunar     import get_day_lunar_info

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

# ─────────────────────────────────────────────────────────────────────────────
# Часовой пояс Кипра
TZ = pendulum.timezone("Asia/Nicosia")

# Сегодня и Завтра (в часовом поясе TZ)
TODAY    = pendulum.now(TZ).date()
TOMORROW = TODAY.add(days=1)

# Telegram-параметры
TOKEN   = os.getenv("TELEGRAM_TOKEN", "")
CHAT_ID = int(os.getenv("CHANNEL_ID", "0"))

if not TOKEN or CHAT_ID == 0:
    logging.error("Не заданы TELEGRAM_TOKEN и/или CHANNEL_ID")
    exit(1)

# Список городов Кипра и их координаты (добавлена Ayia Napa)
CITIES: Dict[str, Tuple[float, float]] = {
    "Nicosia":   (35.170, 33.360),
    "Larnaca":   (34.916, 33.624),
    "Limassol":  (34.707, 33.022),
    "Pafos":     (34.776, 32.424),
    "Troodos":   (34.916, 32.823),
    "Ayia Napa": (34.988, 34.012),
}

# Прибрежные города, из которых будем усреднять SST
COASTAL_CITIES = ["Larnaca", "Limassol", "Pafos", "Ayia Napa"]

# WMO-коды → краткое описание
WMO_DESC: Dict[int, str] = {
    0:  "☀️ ясно",
    1:  "⛅️ малооблачно",
    2:  "☁️ облачно",
    3:  "🌥 пасмурно",
    45: "🌫 туман",
    48: "🌫 изморозь",
    51: "🌦 морось",
    61: "🌧 дождь",
    71: "❄️ снег",
    95: "⛈ гроза",
}

def code_desc(code: int) -> str:
    """
    Преобразует WMO-код в русский текст.
    """
    return WMO_DESC.get(code, "—")

def pressure_arrow(hourly: Dict[str, Any]) -> str:
    """
    Сравнивает давление в начале и в конце суток (список hourly.surface_pressure).
    Если данных мало — возвращает «→».
    """
    pr = hourly.get("surface_pressure", [])
    if len(pr) < 2:
        return "→"
    delta = pr[-1] - pr[0]
    if delta > 1.0:
        return "↑"
    if delta < -1.0:
        return "↓"
    return "→"

def schumann_line(sch: Dict[str, Any]) -> str:
    """
    Форматирует строку «Шуман» с цветовой индикацией частоты и тренда:
      – 🔴 если freq < 7.6 Гц
      – 🟢 если 7.6 ≤ freq ≤ 8.1
      – 🟣 если freq > 8.1
    Добавляем амплитуду (amp) и стрелку тренда (trend).
    """
    if sch.get("freq") is None:
        return "🎵 Шуман: н/д"
    f   = sch["freq"]
    amp = sch["amp"]
    if   f < 7.6:
        emoji = "🔴"
    elif f > 8.1:
        emoji = "🟣"
    else:
        emoji = "🟢"
    return f"{emoji} Шуман: {f:.2f} Гц / {amp:.1f} pT {sch['trend']}"

def get_schumann_with_fallback() -> Dict[str, Any]:
    """
    Сначала пробуем получить «живые» данные из get_schumann().
    Если там freq == None, читаем последние 24 часа из schumann_hourly.json
    и рассчитываем тренд по последним 24 часам.
    """
    sch = get_schumann()
    if sch.get("freq") is not None:
        sch["cached"] = False
        return sch

    cache_path = Path(__file__).parent / "schumann_hourly.json"
    if cache_path.exists():
        try:
            arr = json.loads(cache_path.read_text(encoding="utf-8"))
            if arr:
                last = arr[-1]
                pts  = arr[-24:]
                freqs = [p["freq"] for p in pts if isinstance(p.get("freq"), (int, float))]
                if len(freqs) > 1:
                    avg   = sum(freqs[:-1]) / (len(freqs) - 1)
                    delta = freqs[-1] - avg
                    trend = "↑" if delta >= 0.1 else "↓" if delta <= -0.1 else "→"
                else:
                    trend = "→"
                return {
                    "freq":  round(last["freq"], 2),
                    "amp":   round(last["amp"], 1),
                    "trend": trend,
                    "cached": True,
                }
        except Exception as e:
            logging.warning("Schumann fallback parse error: %s", e)

    return sch

def build_msg() -> str:
    """
    Собирает всё сообщение «вечернего поста» для Telegram:
      1) Заголовок
      2) Усреднённая температура моря (SST) по прибрежным городам
      3) Температура моря (SST) в Limassol (отдельно)
      4) Прогноз для Limassol (avg temp, облака, ветер, давление)
      5) Рейтинг городов (топ-5 по дневным температурам) с SST для каждого
      6) Качество воздуха + Пыльца
      7) Геомагнитка + Шуман
      8) Астрособытия на завтра (VoC, фаза Луны, советы, next_event)
      9) Динамический «Вывод»: «Вините ...»
     10) Рекомендации (GPT-фоллбэк или health-coach) с тем же «виновником»
     11) Факт дня
    Каждый крупный блок разделён строкой «———» для визуальной сегментации.
    """
    P: List[str] = []

    # 1) Заголовок
    P.append(f"<b>🌅 Добрый вечер! Погода на завтра ({TOMORROW.format('DD.MM.YYYY')})</b>")

    # 2) Усреднённая температура моря (SST) по прибрежным городам
    sst_values: List[float] = []
    for ct in COASTAL_CITIES:
        lat_ct, lon_ct = CITIES[ct]
        tmp = get_sst(lat_ct, lon_ct)
        if tmp is not None:
            sst_values.append(tmp)
    if sst_values:
        avg_sst = sum(sst_values) / len(sst_values)
        P.append(f"🌊 Ср. темп. моря (Larnaca, Limassol, Pafos, Ayia Napa): {avg_sst:.1f} °C")
    else:
        P.append("🌊 Ср. темп. моря (Larnaca, Limassol, Pafos, Ayia Napa): н/д")

    # 3) Температура моря (SST) в Limassol (отдельно)
    lat_lims, lon_lims = CITIES["Limassol"]
    sst_lims = get_sst(lat_lims, lon_lims)
    if sst_lims is not None:
        P.append(f"🌊 Темп. моря (Limassol): {sst_lims:.1f} °C")
    else:
        P.append("🌊 Темп. моря (Limassol): н/д")

    # 4) Прогноз для Limassol
    day_max, night_min = fetch_tomorrow_temps(lat_lims, lon_lims, tz=TZ.name)
    w = get_weather(lat_lims, lon_lims) or {}
    cur = w.get("current", {}) or {}

    if day_max is not None and night_min is not None:
        avg_temp = (day_max + night_min) / 2
    else:
        # Если нет данных от fetch_tomorrow_temps, fallback на cur["temperature"]
        avg_temp = cur.get("temperature", 0.0)

    wind_kmh = cur.get("windspeed", 0.0)
    wind_deg = cur.get("winddirection", 0.0)
    press    = cur.get("pressure", 1013)
    clouds   = cur.get("clouds", 0)

    arrow = pressure_arrow(w.get("hourly", {}))

    P.append(
        f"🌡️ Ср. темп: {avg_temp:.0f} °C • {clouds_word(clouds)} "
        f"• 💨 {wind_kmh:.1f} км/ч ({compass(wind_deg)}) "
        f"• 💧 {press:.0f} гПа {arrow}"
    )
    P.append("———")

    # 5) Рейтинг городов (топ-5 по дневным температурам) с SST для каждого
    temps: Dict[str, Tuple[float, float, int, Optional[float]]] = {}
    for city, (la, lo) in CITIES.items():
        d, n = fetch_tomorrow_temps(la, lo, tz=TZ.name)
        if d is None:
            continue

        wcod = get_weather(la, lo) or {}
        daily_codes = wcod.get("daily", {}).get("weathercode", [])
        code_tmr: int = daily_codes[1] if (isinstance(daily_codes, list) and len(daily_codes) > 1) else 0

        # Добавляем SST (температура моря) для каждого города
        sst_city: Optional[float] = get_sst(la, lo)
        temps[city] = (d, n if n is not None else d, code_tmr, sst_city)

    if temps:
        P.append("🎖️ <b>Рейтинг городов (дн./ночь °C, погода, 🌊 SST)</b>")
        medals = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣"]
        sorted_cities = sorted(temps.items(), key=lambda kv: kv[1][0], reverse=True)[:5]
        for i, (city, (d, n, code, sst_city)) in enumerate(sorted_cities):
            desc = code_desc(code)
            if sst_city is not None:
                P.append(f"{medals[i]} {city}: {d:.1f}/{n:.1f} °C, {desc}, 🌊 {sst_city:.1f} °C")
            else:
                P.append(f"{medals[i]} {city}: {d:.1f}/{n:.1f} °C, {desc}")
        P.append("———")

    # 6) Качество воздуха + Пыльца
    air = get_air() or {}
    lvl = air.get("lvl", "н/д")
    P.append("🏭 <b>Качество воздуха</b>")
    P.append(
        f"{AIR_EMOJI.get(lvl, '⚪')} {lvl} (AQI {air.get('aqi', 'н/д')}) | "
        f"PM₂.₅: {pm_color(air.get('pm25'))} | PM₁₀: {pm_color(air.get('pm10'))}"
    )
    if (pollen := get_pollen()):
        P.append("🌿 <b>Пыльца</b>")
        P.append(
            f"Деревья: {pollen['tree']} | Травы: {pollen['grass']} | "
            f"Сорняки: {pollen['weed']} — риск {pollen['risk']}"
        )
    P.append("———")

    # 7) Геомагнитка + Шуман
    kp, kp_state = get_kp()
    if kp is not None:
        P.append(f"{kp_emoji(kp)} Геомагнитка: Kp={kp:.1f} ({kp_state})")
    else:
        P.append("🧲 Геомагнитка: н/д")

    P.append(schumann_line(get_schumann_with_fallback()))
    P.append("———")

    # 8) Астрособытия на завтра
    P.append("🌌 <b>Астрособытия</b>")
    astro_lines: List[str] = astro_events(offset_days=1, show_all_voc=True)
    if astro_lines:
        P.extend(astro_lines)
    else:
        P.append("— нет данных —")
    P.append("———")

    # ────────────────────────────────────────────────────────────────────────
    # 9) Динамический «Вывод» («Вините …»)
    #
    #  Логика выбора «виновника»:
    #   1) Если Kp ≥ 5 («буря») → «магнитные бури»
    #   2) Иначе, если t_max ≥ 30 → «жару»
    #   3) Иначе, если t_min ≤ 5 → «резкое похолодание»
    #   4) Иначе, если завтра WMO-код в {95, 71, 48} → «гроза» / «снег» / «изморозь»
    #   5) Иначе → «астрологический фактор»
    #
    #   При выборе «астрологического фактора» берём из astro_lines первую строку,
    #   содержащую «новолуние», «полнолуние» или «четверть». 
    #   Приводим к виду «фазу луны — {PhaseName, Sign}».
    culprit_text: str

    # 1) Проверяем геомагнитку
    if kp is not None and kp_state.lower() == "буря":
        culprit_text = "магнитные бури"
    else:
        # 2) Проверяем экстренную жару
        if day_max is not None and day_max >= 30:
            culprit_text = "жару"
        # 3) Проверяем резкое похолодание
        elif night_min is not None and night_min <= 5:
            culprit_text = "резкое похолодание"
        else:
            # 4) Проверяем опасный WMO-код
            daily_codes_main = w.get("daily", {}).get("weathercode", [])
            tomorrow_code = (
                daily_codes_main[1] 
                if isinstance(daily_codes_main, list) and len(daily_codes_main) > 1 
                else None
            )
            if tomorrow_code == 95:
                culprit_text = "гроза"
            elif tomorrow_code == 71:
                culprit_text = "снег"
            elif tomorrow_code == 48:
                culprit_text = "изморозь"
            else:
                # 5) Блок «астрологический фактор»
                culprit_text = None
                for line in astro_lines:
                    low = line.lower()
                    if "новолуние" in low or "полнолуние" in low or "четверть" in low:
                        clean = line
                        # Убираем эмоджи Луны
                        for ch in ("🌑", "🌕", "🌓", "🌒", "🌙"):
                            clean = clean.replace(ch, "")
                        # Убираем процент «(...)»
                        clean = clean.split("(")[0].strip()
                        # Нормализуем пробелы и запятые
                        clean = clean.replace(" ,", ",").strip()
                        # Делаем первую букву заглавной
                        clean = clean[0].upper() + clean[1:]
                        culprit_text = f"фазу луны — {clean}"
                        break
                if not culprit_text:
                    # Если не нашли фазу → общий «неблагоприятный прогноз погоды»
                    culprit_text = "неблагоприятный прогноз погоды"

    # 9) Формируем блок «Вывод»
    P.append("📜 <b>Вывод</b>")
    P.append(f"Если завтра что-то пойдёт не так, вините {culprit_text}! 😉")
    P.append("———")

    # 10) Блок «Рекомендации» (GPT-фоллбэк или health-coach)
    P.append("✅ <b>Рекомендации</b>")
    summary, tips = gpt_blurb(culprit_text)
    # Выводим только три совета (tips), без повторения фразы «Если завтра что-то пойдёт не так, вините…»
    for advice in tips[:3]:
        P.append(f"• {advice.strip()}")
    P.append("———")

    # 11) Факт дня
    P.append(f"📚 {get_fact(TOMORROW)}")

    return "\n".join(P)


async def send_main_post(bot: Bot) -> None:
    """
    Отправляет сформированное сообщение в Telegram.
    """
    html = build_msg()
    logging.info("Preview: %s", html.replace("\n", " | ")[:200])
    try:
        await bot.send_message(
            chat_id=CHAT_ID,
            text=html,
            parse_mode="HTML",
            disable_web_page_preview=True
        )
        logging.info("Сообщение отправлено ✓")
    except tg_err.TelegramError as e:
        logging.error("Telegram error: %s", e)
        raise


async def send_poll_if_friday(bot: Bot) -> None:
    """
    Если сегодня пятница, дополнительно отправляем опрос.
    """
    if pendulum.now(TZ).weekday() == 4:
        try:
            await bot.send_poll(
                chat_id=CHAT_ID,
                question="Как сегодня ваше самочувствие? 🤔",
                options=[
                    "🔥 Полон(а) энергии",
                    "🙂 Нормально",
                    "😴 Слегка вялый(ая)",
                    "🤒 Всё плохо"
                ],
                is_anonymous=False,
                allows_multiple_answers=False
            )
        except tg_err.TelegramError as e:
            logging.warning("Poll send error: %s", e)


async def main() -> None:
    bot = Bot(token=TOKEN)
    await send_main_post(bot)
    await send_poll_if_friday(bot)


if __name__ == "__main__":
    asyncio.run(main())