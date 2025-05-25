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


def build_monthly_message(data: dict) -> str:
    """
    Формирует текст сообщения в Telegram:
    1) Заголовок с месяцем и годом
    2) Для каждой даты: "D MMMM — фаза: первый совет"
    3) Сводка по категориям благоприятных/неблагоприятных дней
    """
    # 1) Header
    first_date = next(iter(data))
    month_year = pendulum.parse(first_date).in_tz(TZ).format("MMMM YYYY").upper()
    lines = [f"🌙 <b>Лунный календарь на {month_year}</b>", ""]

    # 2) Daily lines
    for date_str, rec in data.items():
        d = pendulum.parse(date_str).in_tz(TZ)
        day_label = d.format("D MMMM")
        phase     = rec.get("phase", "")
        advice    = rec.get("advice", [])
        first_tip = advice[0] if advice else ""
        lines.append(f"{day_label} — {phase}: {first_tip}")
    lines.append("")  # blank before summary

    # 3) Summary of favorable/unfavorable days
    general_fav  = data[first_date]["favorable_days"].get("general", [])
    general_unf  = data[first_date]["unfavorable_days"].get("general", [])
    lines.append(f"✅ Общие благоприятные дни месяца: {', '.join(map(str, general_fav))}")
    if general_unf:
        lines.append(f"❌ Общие неблагоприятные дни месяца: {', '.join(map(str, general_unf))}")

    # Other categories
    category_icons = {
        "haircut": "✂️ Стрижки",
        "travel":  "✈️ Путешествия",
        "shopping":"🛍️ Покупки",
        "health":  "❤️ Здоровье",
    }
    for cat, label in category_icons.items():
        fav = data[first_date]["favorable_days"].get(cat, [])
        if fav:
            lines.append(f"{label}: {', '.join(map(str, fav))}")

    return "\n".join(lines)


async def main() -> None:
    # Load lunar_calendar.json
    path = Path(__file__).parent / "lunar_calendar.json"
    if not path.exists():
        print("❌ lunar_calendar.json not found.")
        return

    data = json.loads(path.read_text(encoding="utf-8"))
    msg  = build_monthly_message(data)

    bot = Bot(token=TOKEN)
    try:
        await bot.send_message(
            CHAT_ID,
            msg,
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
        print("✅ Monthly calendar delivered")
    except tg_err.TelegramError as e:
        print(f"❌ Telegram error: {e}")


if __name__ == "__main__":
    asyncio.run(main())
