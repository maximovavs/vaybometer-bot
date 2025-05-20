#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
utils.py  • вспомогательные функции и константы VayboMeter-бота
"""

from __future__ import annotations
import logging, math, time, random, requests, pendulum
from typing import Any, Dict, Optional, List

# ──────────────────────── Компас, облака, ветер ──────────────────────────
COMPASS = [
    "N","NNE","NE","ENE","E","ESE","SE","SSE",
    "S","SSW","SW","WSW","W","WNW","NW","NNW"
]

def compass(deg: float) -> str:
    return COMPASS[int((deg/22.5)+.5) % 16]

def clouds_word(pc: int) -> str:
    if pc < 25:  return "ясно"
    if pc < 70:  return "переменная"
    return "пасмурно"

def wind_phrase(km_h: float) -> str:
    if km_h < 2:   return "штиль"
    if km_h < 8:   return "слабый"
    if km_h < 14:  return "умеренный"
    return "сильный"

def safe(v: Any, unit: str = "") -> str:
    """None → «—»; число → форматированная строка с unit."""
    if v in (None, "None", "—"):
        return "—"
    return f"{v:.1f}{unit}" if isinstance(v, (int, float)) else f"{v}{unit}"

# ──────────────────────── AQI & PM раскраска ─────────────────────────────
def aqi_color(aqi: int | float | str) -> str:
    if aqi in ("—", "н/д"): return "⚪"
    aqi = float(aqi)
    if aqi <=  50: return "🟢"
    if aqi <= 100: return "🟡"
    if aqi <= 150: return "🟠"
    if aqi <= 200: return "🔴"
    if aqi <= 300: return "🟣"
    return "🟤"

def pm_color(pm: Optional[float | int | str], with_unit: bool = False) -> str:
    """
    Быстрая цветовая индикация концентраций PM₂.₅ / PM₁₀.
    ▸ Возвращает, напр., «🟡27» или «🟢 8 µg/м³», если with_unit=True
    """
    if pm in (None, "—", "н/д"):
        return "⚪ н/д"
    try:
        val = float(pm)
    except (TypeError, ValueError):
        return "⚪ н/д"

    if val <= 12:    col = "🟢"
    elif val <= 35:  col = "🟡"
    elif val <= 55:  col = "🟠"
    elif val <=150:  col = "🔴"
    elif val <=250:  col = "🟣"
    else:            col = "🟤"

    txt = f"{int(round(val))}"
    if with_unit:
        txt += " µg/м³"
    return f"{col}{txt}"

# ──────────────────────── «Факт дня» ─────────────────────────────────────
FACTS: Dict[str, str] = {
    # уже были:
    "05-11": "11 мая — День морского бриза на Кипре 🌬️",
    "06-08": "8 июня 2004 — транзит Венеры по диску Солнца 🌞",
    "07-20": "20 июля — на Кипре собирают первый урожай винограда 🍇",
    "10-01": "1 октября — День Кипра 🇨🇾",
    # добавленные праздники:
    "01-01": "1 января — Новый год на Кипре начинается с фейерверков над старинными крепостями 🎆",
    "01-06": "6 января — Крещение Господне: некоторые смельчаки окунаются в Средиземное море ❄️",
    "02-14": "14 февраля — День святого Валентина: любовь витает в воздухе, а рынки завалены мимозами и виноградом 💐",
    "02-25": "25 февраля — Чистый понедельник (Катаклизмос): народ отмечает праздник вод и весны 🌊",
    "03-08": "8 марта — Международный женский день: цветы и поздравления для всех дам 💐",
    "03-25": "25 марта — День независимости Греции: на северном Кипре тоже звучат марши и парады 🇬🇷",
    "04-01": "1 апреля — День смеха: на Кипре шутят с друзьями и на пляже строят песчаные замки 🤡",
    "05-01": "1 мая — День труда и цветущих маков: в горах кипят гулянья и народные танцы 🌺",
    "06-21": "21 июня — День летнего солнцестояния: самый длинный день года ☀️",
    "06-29": "29 июня — Праздник святых Петра и Павла в Пафосе: богослужения и народные гулянья 🕍",
    "08-15": "15 августа — Успение Богородицы (Панайя): крупнейший религиозный фестиваль острова ⛪️",
    "08-16": "16 августа — День Конституции Кипра: официальный выходной с парадами и концертом 📜",
    "09-21": "21 сентября — Международный день мира: флаги мира над старым городом Никосии 🕊️",
    "10-11": "11 октября — День святого Варнавы: почитается покровитель Кипрской церкви 🙏",
    "10-31": "31 октября — День святого Луки: в деревнях греют красное вино и угощают козу 😂🍷",
    "11-01": "1 ноября — День всех святых: дома украшают соломенными венками и свечами 🕯️",
    "12-24": "24 декабря — Сочельник Рождества: семьи готовят «махалло» и сладости 🎄",
    "12-25": "25 декабря — Рождество: улицы Никосии сверкают гирляндами и ярмарками 🎁",
    "12-26": "26 декабря — Boxing Day: распродажи и семейные посиделки в прибрежных кафе 🛍️",
    "12-31": "31 декабря — Канун Нового года: пляжи и набережные заполняются гуляками до утра 🥂",
}

def get_fact(d: pendulum.Date) -> str:
    """
    Возвращает специальный факт для даты d (MM-DD), 
    либо стандартный приветственный факт.
    """
    return FACTS.get(
        d.format("MM-DD"),
        "На Кипре в году ≈ 340 солнечных дней ☀️"
    )

# ──────────────────────── Иконки & цвета ────────────────────────────────
WEATHER_ICONS = {
    "ясно":"☀️","переменная":"🌤️","пасмурно":"☁️",
    "дождь":"🌧️","туман":"🌁"
}
AIR_EMOJI = {
    "хороший":"🟢","умеренный":"🟡","вредный":"🟠",
    "оч. вредный":"🔴","опасный":"🟣","н/д":"⚪"
}

K_COLOR = {
    "green":  range(0, 4),
    "yellow": range(4, 6),
    "red":    range(6, 10),
}
def kp_emoji(kp: float) -> str:
    k = int(round(kp))
    if k in K_COLOR["green"]:   return "🟢"
    if k in K_COLOR["yellow"]:  return "🟡"
    return "🔴"

# ──────────────────────── Тренд давления ────────────────────────────────
def pressure_trend(w: Dict[str, Any]) -> str:
    """
    ↑ если ближайший час > +2 гПа, ↓ < −2 гПа, иначе →.
    w — объект, полученный из get_weather().
    """
    hp = w.get("hourly", {}).get("surface_pressure", [])
    if len(hp) < 2:
        return "→"
    diff = hp[1] - hp[0]
    if   diff >= 2:  return "↑"
    elif diff <= -2: return "↓"
    return "→"

# ──────────────────────── HTTP-обёртки ───────────────────────────────────
_HEADERS = {
    "User-Agent": "VayboMeter/1.0 (+https://github.com/)",
    "Accept":     "application/json",
}

def _get_retry(url: str, retries: int = 2, **params) -> Optional[dict]:
    attempt = 0
    while attempt <= retries:
        try:
            r = requests.get(url, params=params, timeout=15, headers=_HEADERS)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            attempt += 1
            if attempt > retries:
                logging.warning("%s – %s (attempts=%d)",
                                url.split("/")[2], e, attempt)
                return None
            time.sleep(0.5 * attempt)

def _get(url: str, **params) -> Optional[dict]:
    """Старый интерфейс для совместимости: 2 повторные попытки."""
    return _get_retry(url, retries=2, **params)

# ──────────────────────── module self-test ──────────────────────────────
if __name__ == "__main__":
    print("pm_color demo:", pm_color(8), pm_color(27), pm_color(78, True), pm_color(None))
    print("AQI demo:", aqi_color(42), aqi_color(160), aqi_color("—"))
    print("Fact today:", get_fact(pendulum.today()))
