#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import json
import asyncio
from pathlib import Path

import pendulum
from telegram import Bot, error as tg_err

# ────────────── Configuration ───────────────────
TOKEN   = os.getenv("TELEGRAM_TOKEN", "")
CHAT_ID = int(os.getenv("CHANNEL_ID", 0))
TZ      = pendulum.timezone("Asia/Nicosia")

def build_monthly_lines(data: dict) -> list[str]:
    """
    Формирует список строк сообщения:
    1) Заголовок с месяцем и годом
    2) Для каждой даты: "D MMMM — фаза: первый совет"
    3) Сводка по категориям благоприятных/неблагоприятных дней
    """
    lines: list[str] = []

    # 1) Header
    first_date = next(iter(data))
    month_year = pendulum.parse(first_date).in_tz(TZ).format("MMMM YYYY").upper()
    lines.append(f"🌙 <b>Лунный календарь на {month_year}</b>")
    lines.append("")

    # 2) Daily lines
    for date_str, rec in data.items():
        d = pendulum.parse(date_str).in_tz(TZ)
        day_label = d.format("D MMMM")
        phase     = rec.get("phase", "")
        advice    = rec.get("advice", [])
        first_tip = advice[0] if advice else ""
        lines.append(f"{day_label} — {phase}: {first_tip}")
    lines.append("")

    # 3) Summary of favorable/unfavorable days
    fav_general = data[first_date]["favorable_days"].get("general", [])
    unf_general = data[first_date]["unfavorable_days"].get("general", [])
    lines.append(f"✅ Общие благоприятные дни месяца: {', '.join(map(str, fav_general))}")
    if unf_general:
        lines.append(f"❌ Общие неблагоприятные дни месяца: {', '.join(map(str, unf_general))}")

    # Other categories
    category_icons = {
        "haircut":  "✂️ Стрижки",
        "travel":   "✈️ Путешествия",
        "shopping": "🛍️ Покупки",
        "health":   "❤️ Здоровье",
    }
    for cat, label in category_icons.items():
        fav = data[first_date]["favorable_days"].get(cat, [])
        if fav:
            lines.append(f"{label}: {', '.join(map(str, fav))}")

    return lines

async def main() -> None:
    # Load lunar_calendar.json
    path = Path(__file__).parent / "lunar_calendar.json"
    if not path.exists():
        print("❌ lunar_calendar.json not found.")
        return

    data = json.loads(path.read_text(encoding="utf-8"))
    lines = build_monthly_lines(data)

    # Разбиваем на чанки по ~3900 символов
    chunks: list[str] = []
    buf = ""
    for line in lines:
        # +1 для перевода строки
        if len(buf) + len(line) + 1 > 3900:
            chunks.append(buf)
            buf = ""
        buf += line + "\n"
    if buf:
        chunks.append(buf)

    bot = Bot(token=TOKEN)
    try:
        for chunk in chunks:
            await bot.send_message(
                CHAT_ID,
                chunk,
                parse_mode="HTML",
                disable_web_page_preview=True,
            )
        print("✅ Monthly calendar delivered")
    except tg_err.TelegramError as e:
        print(f"❌ Telegram error: {e}")

if __name__ == "__main__":
    asyncio.run(main())
