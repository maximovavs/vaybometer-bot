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
from lunar import get_day_lunar_info         # функция из lunar.py

TZ = pendulum.timezone("Asia/Nicosia")

# ───────────────────────────── helpers ──────────────────────────────
def _today_info() -> Optional[Dict[str, Any]]:
    today = pendulum.now(TZ).date()
    return get_day_lunar_info(today)

def _format_voc(rec: Dict[str, Any]) -> Optional[str]:
    voc = rec.get("void_of_course", {})
    if not voc or not voc.get("start") or not voc.get("end"):
        return None
    t1 = pendulum.parse(voc["start"]).in_tz(TZ)
    t2 = pendulum.parse(voc["end"]).in_tz(TZ)
    if (t2 - t1).in_minutes() < 15:          # прячем «микро-VoC»
        return None
    return f"⚫️ VoC {t1.format('HH:mm')}–{t2.format('HH:mm')}"

def _format_general_day(rec: Dict[str, Any]) -> Optional[str]:
    day = pendulum.now(TZ).day
    gen = rec.get("favorable_days", {}).get("general", {})
    if day in gen.get("favorable", []):
        return "✅ Благоприятный день"
    if day in gen.get("unfavorable", []):
        return "❌ Неблагоприятный день"
    return None

CAT_EMO = {
    "haircut":     "✂️",
    "travel":      "✈️",
    "shopping":    "🛍",
    "health":      "❤️",
}

def _format_categories(rec: Dict[str, Any]) -> List[str]:
    """Строки вида '✂️ Стрижка — благоприятно'."""
    day = pendulum.now(TZ).day
    fav  = rec.get("favorable_days", {})
    lines: List[str] = []

    for cat, emoji in CAT_EMO.items():
        f_list = fav.get(cat, {}).get("favorable", [])
        u_list = fav.get(cat, {}).get("unfavorable", [])
        if day in f_list:
            lines.append(f"{emoji} {cat.capitalize()} — благоприятно")
        elif day in u_list:
            lines.append(f"{emoji} {cat.capitalize()} — неблагоприятно")
    return lines

# ───────────────────────── main entry point ────────────────────────
def astro_events() -> List[str]:
    """
    Формирует готовый к печати список строк:
    • доп. маркеры (VoC, благоприятный день, категории)
    • фаза Луны + советы
    • next_event
    """
    info = _today_info()
    if not info:
        return []

    phase   = info.get("phase", "").strip()
    advice  = info.get("advice", [])

    events: List[str] = []

    # --- Extra markers ------------------------------------------------------
    for extra in (_format_voc(info), _format_general_day(info)):
        if extra:
            events.append(extra)

    events.extend(_format_categories(info))

    # --- Phase & 3 tips -----------------------------------------------------
    if phase and advice:
        events.append(f"{phase} – {advice[0].strip()}")
        for adv in advice[1:]:
            events.append(f"• {adv.strip()}")

    # --- upcoming event -----------------------------------------------------
    nxt = info.get("next_event", "").strip()
    if nxt:
        events.append(nxt)

    return events


# -------------------------- локальный тест ---------------------------
if __name__ == "__main__":
    from pprint import pprint
    pprint(astro_events())