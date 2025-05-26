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
    Формирует итоговое сообщение:
    1) Заголовок с месяцем/годом
    2) Для каждого сегмента (одна фаза):
       • название фазы, момент, диапазон дат
       • Void-of-Course
       • единый совет (первый из advice)
    3) Сводка по благоприятным/неблагоприятным дням
    4) Краткое объяснение, что такое Void-of-Course
    """
    # 1) Header
    first_date = next(iter(data))
    month_year = pendulum.parse(first_date).in_tz(TZ).format("MMMM YYYY").upper()
    lines = [
        f"🌙 <b>Лунный календарь на {month_year}</b> <i>(Asia/Nicosia)</i>",
        ""
    ]

    # 2) Group by phase
    segments = []
    last_phase = None
    for date_str in sorted(data.keys()):
        rec = data[date_str]
        phase_name = rec["phase"].split(" в ")[0]
        if phase_name != last_phase:
            segments.append({
                "phase":      rec["phase"],
                "phase_time": rec.get("phase_time"),
                "dates":      [date_str],
                "vc":         rec.get("void_of_course", {}),
                "advice":     rec.get("advice", [""])[0],
            })
            last_phase = phase_name
        else:
            segments[-1]["dates"].append(date_str)

    # 3) Render each segment
    for seg in segments:
        # format moment of phase
        pt_iso = seg["phase_time"]
        if pt_iso:
            dt = pendulum.parse(pt_iso).in_tz(TZ)
            time_str = dt.format("DD.MM HH:mm")
        else:
            time_str = ""
        start = pendulum.parse(seg["dates"][0]).in_tz(TZ).format("D.MM")
        end   = pendulum.parse(seg["dates"][-1]).in_tz(TZ).format("D.MM")
        lines.append(f"<b>{seg['phase']}</b> ({time_str}; {start}–{end})")

        # Void-of-Course
        vc = seg["vc"]
        if vc.get("start") and vc.get("end"):
            lines.append(f"<b>Void-of-Course:</b> {vc['start']} → {vc['end']}")

        # Advice for the whole period
        lines.append(seg["advice"])
        lines.append("")  # blank line between segments

    # 4) Summary of favorable/unfavorable days
    first_key = first_date
    fav_gen = data[first_key]["favorable_days"].get("general", [])
    unf_gen = data[first_key]["unfavorable_days"].get("general", [])
    lines.append(f"✅ <b>Общие благоприятные дни месяца:</b> {', '.join(map(str, fav_gen))}")
    if unf_gen:
        lines.append(f"❌ <b>Общие неблагоприятные дни месяца:</b> {', '.join(map(str, unf_gen))}")
    icons = {
        "haircut":  "✂️ <b>Стрижки:</b>",
        "travel":   "✈️ <b>Путешествия:</b>",
        "shopping": "🛍️ <b>Покупки:</b>",
        "health":   "❤️ <b>Здоровье:</b>",
    }
    for cat, label in icons.items():
        fav = data[first_key]["favorable_days"].get(cat, [])
        if fav:
            lines.append(f"{label} {', '.join(map(str, fav))}")

    # 5) Final explanation
    lines.append("")
    lines.append("<b>Что такое Void-of-Course?</b>")
    lines.append(
        "Void-of-Course (период «без курса») — это временной отрезок, когда Луна завершила все основные аспекты "
        "и ещё не вошла в следующий знак зодиака. Энергия таких дней расссеивается и не подходит для старта "
        "важных дел, покупок или подписания контрактов. Лучше посвятить это время отдыху, планированию и "
        "внутренним практикам, а ключевые решения перенести на периоды после окончания V/C."
    )

    return "\n".join(lines)


async def main() -> None:
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
            disable_web_page_preview=True
        )
        print("✅ Monthly calendar delivered")
    except tg_err.TelegramError as e:
        print(f"❌ Telegram error: {e}")


if __name__ == "__main__":
    asyncio.run(main())
