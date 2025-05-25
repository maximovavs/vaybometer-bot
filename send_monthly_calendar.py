#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os, json
from pathlib import Path
import pendulum
from telegram import Bot

def load_calendar(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))

def build_monthly_summary(cal: dict, year: int, month: int) -> str:
    title = pendulum.date(year, month, 1).format("MMMM YYYY").upper()
    lines = [f"🌙 <b>Лунный календарь на {title}</b>\n"]
    # по дням
    for day_str, rec in cal.items():
        d = pendulum.parse(day_str)
        if d.year == year and d.month == month:
            phase = rec["phase"]
            # первый совет из списка advice
            advice = rec["advice"][0] if rec.get("advice") else ""
            lines.append(f"{d.format('D MMMM')} — {phase}: {advice}")
    lines.append("")  # пустая строка
    # сводка по категориям
    # берем списки из первой даты (одинаково для всего месяца)
    sample = next(iter(cal.values()))
    fav = sample["favorable_days"]
    unfav = sample["unfavorable_days"]
    lines.append(f"✅ Благоприятные дни месяца (общие): {', '.join(map(str, fav['general']))}")
    for cat, days in fav.items():
        if cat != "general":
            emoji = {
                "haircut": "✂️",
                "travel": "✈️",
                "shopping": "🛍️",
                "health": "💊"
            }.get(cat, "•")
            lines.append(f"{emoji} {cat.capitalize()}: {', '.join(map(str, days))}")
    # по желанию можно добавить неблагоприятные дни
    return "\n".join(lines)

def main():
    TOKEN = os.getenv("TELEGRAM_TOKEN")
    CHAT_ID = int(os.getenv("CHANNEL_ID", 0))
    bot = Bot(token=TOKEN)

    cal_path = Path(__file__).parent / "lunar_calendar.json"
    cal = load_calendar(cal_path)

    # определяем последний месяц (текущий)
    now = pendulum.now("Asia/Nicosia")
    year, month = now.year, now.month

    msg = build_monthly_summary(cal, year, month)
    bot.send_message(CHAT_ID, msg, parse_mode="HTML", disable_web_page_preview=True)

if __name__ == "__main__":
    main()
