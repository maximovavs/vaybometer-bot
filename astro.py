#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
astro.py • формирует блок «Астрособытия» для ежедневного поста.
"""

from __future__ import annotations
import pendulum
from typing import Any, Dict, List, Optional
from lunar import get_day_lunar_info          # читает lunar_calendar.json

TZ = pendulum.timezone("Asia/Nicosia")

# ─── helpers ──────────────────────────────────────────────────────────
def _today_info() -> Optional[Dict[str, Any]]:
    return get_day_lunar_info(pendulum.now(TZ).date())

def _parse_voc_time(text: str) -> Optional[pendulum.DateTime]:
    """
    Приходит строка «DD.MM HH:mm».  Добавляем текущий год и
    парсим строго по шаблону, чтобы избежать ParserError.
    """
    try:
        year = pendulum.now(TZ).year
        return pendulum.from_format(f"{year} {text}", "YYYY DD.MM HH:mm", tz=TZ)
    except Exception:
        return None

def _format_voc(rec: Dict[str, Any]) -> Optional[str]:
    voc = rec.get("void_of_course", {})
    start, end = voc.get("start"), voc.get("end")
    if not (start and end):
        return None

    t1 = _parse_voc_time(start)
    t2 = _parse_voc_time(end)
    if not (t1 and t2):
        return None

    # «микро-VoC» (короче 15 мин) прячем
    if (t2 - t1).in_minutes() < 15:
        return None

    return f"⚫️ VoC {t1.format('DD.MM HH:mm')}–{t2.format('HH:mm')}"

def _format_general_day(rec: Dict[str, Any]) -> Optional[str]:
    day = pendulum.now(TZ).day
    gen = rec.get("favorable_days", {}).get("general", {})
    if day in gen.get("favorable", []):
        return "✅ Благоприятный день"
    if day in gen.get("unfavorable", []):
        return "❌ Неблагоприятный день"
    return None

CAT_EMO = {
    "haircut":   "✂️",
    "travel":    "✈️",
    "shopping":  "🛍",
    "health":    "❤️",
}

def _format_categories(rec: Dict[str, Any]) -> List[str]:
    day   = pendulum.now(TZ).day
    fav   = rec.get("favorable_days", {})
    lines: List[str] = []

    for cat, emoji in CAT_EMO.items():
        f_list = fav.get(cat, {}).get("favorable", [])
        u_list = fav.get(cat, {}).get("unfavorable", [])
        if day in f_list:
            lines.append(f"{emoji} {cat.capitalize()} — благоприятно")
        elif day in u_list:
            lines.append(f"{emoji} {cat.capitalize()} — неблагоприятно")
    return lines

# ─── public API ──────────────────────────────────────────────────────
def astro_events() -> List[str]:
    info = _today_info()
    if not info:
        return []

    phase  = info.get("phase", "").strip()
    advice = info.get("advice", [])

    events: List[str] = []

    # дополнительные маркеры
    for extra in (_format_voc(info), _format_general_day(info)):
        if extra:
            events.append(extra)

    events.extend(_format_categories(info))

    # фаза + советы
    if phase and advice:
        events.append(f"{phase} – {advice[0].strip()}")
        for adv in advice[1:]:
            events.append(f"{adv.strip()}")

    # ближайшее крупное событие
    nxt = info.get("next_event", "").strip()
    if nxt:
        events.append(nxt)

    return events

# ─── debug ───────────────────────────────────────────────────────────
if __name__ == "__main__":
    from pprint import pprint
    pprint(astro_events())
