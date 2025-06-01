#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
astro.py  • формирует блок «Астрособытия» для ежедневного поста.
Возвращает список строк, которые редактор бота вставляет в сообщение.

Теперь включает:
• VoC (длительностью ≥ 15 мин)
• Метку «благоприятный / неблагоприятный день»
• Категории: ✂️ стрижка, ✈️ путешествия, 🛍 покупки, ❤️ здоровье
• Фазу Луны + 3 совета
• next_event
"""

from __future__ import annotations
import pendulum
from typing import Any, Dict, List, Optional
from lunar import get_day_lunar_info  # функция из lunar.py

# Установим часовой пояс
TZ = pendulum.timezone("Asia/Nicosia")


def _today_info() -> Optional[Dict[str, Any]]:
    """
    Возвращает словарь с данными из lunar_calendar.json для текущей даты.
    """
    today = pendulum.now(TZ).date()
    return get_day_lunar_info(today)


def _format_voc(rec: Dict[str, Any]) -> Optional[str]:
    """
    Форматирует период Void-of-Course (если он длится ≥ 15 минут).
    Возвращает строку вида "⚫️ VoC 14:23–16:05" или None.
    """
    voc = rec.get("void_of_course", {})
    if not voc or not voc.get("start") or not voc.get("end"):
        return None

    t1 = pendulum.parse(voc["start"]).in_tz(TZ)
    t2 = pendulum.parse(voc["end"]).in_tz(TZ)
    # Если период меньше 15 минут, игнорируем
    if (t2 - t1).in_minutes() < 15:
        return None

    return f"⚫️ VoC {t1.format('HH:mm')}–{t2.format('HH:mm')}"


def _format_general_day(rec: Dict[str, Any]) -> Optional[str]:
    """
    Определяет, является ли текущий день:
    • благоприятным ("✅ Благоприятный день")
    • неблагоприятным ("❌ Неблагоприятный день")
    или None, если нейтрально.
    """
    day = pendulum.now(TZ).day
    gen = rec.get("favorable_days", {}).get("general", {})
    if day in gen.get("favorable", []):
        return "✅ Благоприятный день"
    if day in gen.get("unfavorable", []):
        return "❌ Неблагоприятный день"
    return None


# Словарь эмодзи для категорий
CAT_EMO = {
    "haircut":  "✂️",
    "travel":   "✈️",
    "shopping": "🛍️",
    "health":   "❤️",
}


def _format_categories(rec: Dict[str, Any]) -> List[str]:
    """
    Для каждой категории (стрижки, путешествия, покупки, здоровье)
    проверяет, попадает ли текущая дата в список благоприятных / неблагоприятных дней.
    Возвращает список строк вида "✂️ Стрижка — благоприятно" или "🛍️ Покупки — неблагоприятно".
    """
    day = pendulum.now(TZ).day
    fav = rec.get("favorable_days", {})
    lines: List[str] = []

    for cat, emoji in CAT_EMO.items():
        label = cat.capitalize()  # 'haircut' → 'Haircut'
        f_list = fav.get(cat, {}).get("favorable", [])
        u_list = fav.get(cat, {}).get("unfavorable", [])
        if day in f_list:
            lines.append(f"{emoji} {label} — благоприятно")
        elif day in u_list:
            lines.append(f"{emoji} {label} — неблагоприятно")

    return lines


def astro_events() -> List[str]:
    """
    Формирует готовый к печати список строк:
    1) Дополнительные маркеры:
       • Void-of-Course
       • Благоприятный / неблагоприятный день
       • Категории (стрижка, путешествия, покупки, здоровье)
    2) Фаза Луны + советы на сегодня (по три строки)
    3) Ближайшее событие ("→ Через X дн. ...")
    Возвращает [] если данных нет.
    """
    info = _today_info()
    if not info:
        return []

    phase  = info.get("phase", "").strip()
    advice = info.get("advice", [])

    events: List[str] = []

    # 1) Extra markers: VoC и общий «благоприятный/неблагоприятный» день
    for extra in (_format_voc(info), _format_general_day(info)):
        if extra:
            events.append(extra)

    # 2) Отметки по категориям
    events.extend(_format_categories(info))

    # 3) Фаза Луны и сами советы
    if phase and advice:
        # Сначала сам текст фазы
        events.append(phase)
        # Затем по одной строке на каждый совет:
        for adv in advice:
            events.append(adv.strip())

    # 4) «Ближайшее событие»
    nxt = info.get("next_event", "").strip()
    if nxt:
        events.append(nxt)

    return events


# Локальный тест
if __name__ == "__main__":
    from pprint import pprint
    pprint(astro_events())