#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
astro.py — формирует блок «Астрособытия» для ежедневного поста бот-канала.

Особенности
-----------
• По умолчанию анализируется **завтра** (offset_days=1).
• Убирает проценты освещённости из строки фазы.
• Советы выводятся маркировкой «•», без нумерации.
• Показывает VoC ≥ 15 мин, маркеры «благоприятный / неблагоприятный» и четыре категории.
"""

from __future__ import annotations

import pendulum
from typing import Any, Dict, List, Optional

from lunar import get_day_lunar_info   # ← функция из вашего календарного модуля

# ──────────────────────────────────────────────────────────────────
TZ = pendulum.timezone("Asia/Nicosia")

CAT_EMO = {
    "haircut":  "✂️",
    "travel":   "✈️",
    "shopping": "🛍",
    "health":   "❤️",
}

# ───────── helpers ───────────────────────────────────────────────
def _rec_for(offset_days: int = 1) -> Optional[Dict[str, Any]]:
    """Получить запись календаря для даты сегодня+offset_days (UTC+3)."""
    target = pendulum.now(TZ).add(days=offset_days).date()
    return get_day_lunar_info(target)

def _fmt_voc(rec: Dict[str, Any]) -> Optional[str]:
    voc = rec.get("void_of_course") or {}
    if not (voc.get("start") and voc.get("end")):
        return None

    t1 = pendulum.parse(voc["start"]).in_tz(TZ)
    t2 = pendulum.parse(voc["end"]).in_tz(TZ)
    if (t2 - t1).in_minutes() < 15:
        return None
    return f"⚫️ VoC {t1.format('HH:mm')}–{t2.format('HH:mm')}"

def _fmt_good_bad(rec: Dict[str, Any], day: int) -> Optional[str]:
    gen = rec.get("favorable_days", {}).get("general", {})
    if day in gen.get("favorable", []):
        return "✅ Благоприятный день"
    if day in gen.get("unfavorable", []):
        return "❌ Неблагоприятный день"
    return None

def _fmt_categories(rec: Dict[str, Any], day: int) -> List[str]:
    fav = rec.get("favorable_days", {})
    lines: List[str] = []
    for cat, emo in CAT_EMO.items():
        f_list = fav.get(cat, {}).get("favorable", [])
        u_list = fav.get(cat, {}).get("unfavorable", [])
        if day in f_list:
            lines.append(f"{emo} {cat.capitalize()} — благоприятно")
        elif day in u_list:
            lines.append(f"{emo} {cat.capitalize()} — неблагоприятно")
    return lines

# ───────── public entry ──────────────────────────────────────────
def astro_events(*, offset_days: int = 1) -> List[str]:
    """
    Вернуть готовый список строк «Астрособытия» для даты = сегодня + offset_days.
    • offset_days=1 → завтра (то, что нужно вечернему посту)
    • offset_days=0 → сегодняшняя дата
    """
    rec = _rec_for(offset_days)
    if not rec:
        return []

    target_day = pendulum.now(TZ).add(days=offset_days).day

    # --- подготовка основных полей --------------------------------
    phase_full = rec.get("phase", "")
    phase_clean = phase_full.split(" (")[0].strip()    # без процента

    advice = [s.lstrip("•123. ").strip()
              for s in rec.get("advice", []) if s.strip()][:3]

    lines: List[str] = []

    # --- маркеры ---------------------------------------------------
    for extra in (_fmt_voc(rec), _fmt_good_bad(rec, target_day)):
        if extra:
            lines.append(extra)

    lines.extend(_fmt_categories(rec, target_day))

    # --- фаза + советы --------------------------------------------
    if phase_clean:
        lines.append(phase_clean)
    for tip in advice:
        lines.append(f"• {tip}")

    # --- ближайшее событие ----------------------------------------
    nxt = rec.get("next_event", "").strip()
    if nxt:
        lines.append(nxt)

    return lines


# 🔹 локальный тест ────────────────────────────────────────────────
if __name__ == "__main__":
    from pprint import pprint
    pprint(astro_events())          # завтра
    print("-" * 40)
    pprint(astro_events(offset_days=0))  # сегодня