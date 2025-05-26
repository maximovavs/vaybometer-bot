#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
send_monthly_calendar.py
────────────────────────
Формирует и отправляет в канал компактный месячный отчёт
по лунным фазам, Void-of-Course и благоприятным дням.
"""

import os
import json
import asyncio
from pathlib import Path
from collections import OrderedDict, defaultdict
from typing import Dict, List, Any

import pendulum
from telegram import Bot, error as tg_err

# ─── Конфигурация ───────────────────────────────────────────────
TOKEN   = os.getenv("TELEGRAM_TOKEN", "")
CHAT_ID = int(os.getenv("CHANNEL_ID", 0))
TZ      = pendulum.timezone("Asia/Nicosia")

# ─── Служебные функции ──────────────────────────────────────────
def iso_to_tz(dt_iso: str) -> pendulum.DateTime:
    """ISO-строка → pendulum в целевой тайм-зоне."""
    return pendulum.parse(dt_iso).in_tz(TZ)

def short(dt: pendulum.DateTime) -> str:
    """Короткий формат D.MM HH:mm."""
    return dt.format("D.MM HH:mm")

# ─── Формирование отчёта ───────────────────────────────────────
def build_monthly_message(data: Dict[str, Any]) -> str:
    # гарантируем порядок дат
    ordered = OrderedDict(sorted(data.items()))
    first_date = next(iter(ordered))
    month_year = pendulum.parse(first_date).in_tz(TZ).format("MMMM YYYY").upper()

    lines: List[str] = [f"🌙 <b>Лунный календарь на {month_year}</b>", ""]

    # 1. Группируем по названию фазы
    segments: List[Dict[str, Any]] = []
    for date_str, rec in ordered.items():
        phase_name = rec["phase"].split(" в ")[0]  # «Новолуние», «Полнолуние» …
        if segments and segments[-1]["phase_name"] == phase_name:
            segments[-1]["dates"].append(date_str)
        else:
            segments.append({
                "phase_name": phase_name,
                "phase":      rec["phase"],         # полная строка
                "phase_time": iso_to_tz(rec["phase_time"]),
                "dates":      [date_str],
                "vc":         rec["void_of_course"],
                "advice":     rec["advice"][0] if rec.get("advice") else "",
            })

    # 2. Блоки по фазам
    for seg in segments:
        start_date = pendulum.parse(seg["dates"][0]).in_tz(TZ)
        end_date   = pendulum.parse(seg["dates"][-1]).in_tz(TZ)
        dt_range   = f"{start_date.format('D.MM')}–{end_date.format('D.MM')}" \
                     if start_date != end_date else start_date.format("D.MM")

        # Заголовок фазы с % освещённости уже внутри seg["phase"]
        lines.append(f"<b>{seg['phase']}</b> "
                     f"({seg['phase_time'].format('DD.MM HH:mm')}; {dt_range})")

        # Void-of-Course
        vc = seg["vc"]
        if vc and vc["start"] and vc["end"]:
            lines.append(f"Void-of-Course: {vc['start']} → {vc['end']}")

        # Совет
        if seg["advice"]:
            lines.append(seg["advice"])

        lines.append("")  # пустая строка-разделитель

    # 3. Сводные благоприятные / неблагоприятные дни
    fav_acc   = defaultdict(set)
    unfav_acc = defaultdict(set)
    for rec in data.values():
        for cat, arr in rec["favorable_days"].items():
            fav_acc[cat].update(arr)
        for cat, arr in rec["unfavorable_days"].items():
            unfav_acc[cat].update(arr)

    def fmt(cat_set):  # красивый вывод через запятую
        return ", ".join(map(str, sorted(cat_set))) if cat_set else "—"

    lines.append("✅ <b>Общие благоприятные дни месяца:</b> " +
                 fmt(fav_acc["general"]))
    if unfav_acc["general"]:
        lines.append("❌ <b>Общие неблагоприятные дни месяца:</b> " +
                     fmt(unfav_acc["general"]))

    cat_labels = {
        "haircut":  "✂️ Стрижки",
        "travel":   "✈️ Путешествия",
        "shopping": "🛍️ Покупки",
        "health":   "❤️ Здоровье",
    }
    for cat, label in cat_labels.items():
        if fav_acc[cat]:
            lines.append(f"{label}: {fmt(fav_acc[cat])}")

    lines.append("")  # отступ перед справкой

    # 4. Короткое пояснение о Void-of-Course
    lines.append("<i>Что такое Void-of-Course?</i>")
    lines.append(
        "Void-of-Course (период «без курса») — это время, когда Луна завершила "
        "основные аспекты в текущем знаке и до входа в следующий знак новые "
        "аспекты не образует. Энергия рассеивается: избегайте старта важных "
        "дел, покупок или подписания контрактов. Полезны отдых, планирование "
        "и внутренние практики; решающие шаги лучше перенести на время после "
        "окончания V/C."
    )

    return "\n".join(lines)

# ─── Отправка ───────────────────────────────────────────────────
async def main() -> None:
    json_path = Path(__file__).parent / "lunar_calendar.json"
    if not json_path.exists():
        print("❌ lunar_calendar.json not found — отмена.")
        return

    data = json.loads(json_path.read_text(encoding="utf-8"))
    message = build_monthly_message(data)

    bot = Bot(token=TOKEN)
    try:
        await bot.send_message(
            CHAT_ID,
            message,
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
        print("✅ Monthly calendar delivered")
    except tg_err.TelegramError as e:
        print(f"❌ Telegram error: {e}")

if __name__ == "__main__":
    asyncio.run(main())
