#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Отправка месячного сообщения «Лунный календарь».
Требует:
  • lunar_calendar.json (из gen_lunar_calendar.py)
  • TELEGRAM_TOKEN, CHANNEL_ID   – в переменных окружения
"""

import os, json, html, asyncio
from pathlib import Path
from collections import OrderedDict
import pendulum
from telegram import Bot, error as tg_err

TOKEN   = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = int(os.getenv("CHANNEL_ID", "0"))
TZ      = pendulum.timezone("Asia/Nicosia")

# сопоставляем чистое имя → emoji, как в генераторе
EMOJI = {
    "Новолуние":"🌑","Растущий серп":"🌒","Первая четверть":"🌓","Растущая Луна":"🌔",
    "Полнолуние":"🌕","Убывающая Луна":"🌖","Последняя четверть":"🌗","Убывающий серп":"🌘"
}

def fmt_range(ds: str, de: str) -> str:
    """'2025-05-01','2025-05-10' → '1–10 мая' с неразрывным пробелом перед «мая»."""
    sd = pendulum.parse(ds, tz=TZ)
    ed = pendulum.parse(de, tz=TZ)
    if sd.month != ed.month:           # случается редко (фаза тянется через месяц)
        return f"{sd.day} {sd.format('MMM')}–{ed.day} {ed.format('MMM')}"
    return f"{sd.day}–{ed.day}\u00A0{sd.format('MMM', locale='ru')}".lower()

def build_message(data: dict) -> str:
    # --- группировка по фазам ---
    ordered = OrderedDict()            # сохраняем природный порядок
    for day, rec in data.items():
        name = rec["phase_name"]
        if name not in ordered:
            ordered[name] = {
                "emoji": EMOJI.get(name,""),
                "dates": [day],
                "desc" : rec["long_desc"]
            }
        else:
            ordered[name]["dates"].append(day)

    # --- заголовок ---
    first_date = next(iter(data))
    month_ru   = pendulum.parse(first_date).format("MMMM YYYY", locale="ru").upper()
    lines = ["<b>🌙 Лунный календарь на " + month_ru + "</b>\n"]

    # --- сами сегменты ---
    for seg in ordered.values():
        seg["dates"].sort()
        rng = fmt_range(seg["dates"][0], seg["dates"][-1])
        emoji = seg["emoji"]
        lines.append(f"{emoji} <b>{rng}</b>")
        lines.append(f"<i>{html.escape(seg['desc'])}</i>\n")

    # --- сводка благоприятных дней ---
    any_day = next(iter(data.values()))        # шаблон
    fav = any_day["favorable_days"]
    unf = any_day["unfavorable_days"]

    def lst(L): return ", ".join(map(str,L)) if L else "—"

    lines.append(f"✅ <b>Благоприятные дни:</b> {lst(fav['general']['favorable'])}")
    lines.append(f"❌ <b>Неблагоприятные:</b> {lst(unf['general']['unfavorable'])}\n")

    icons = {"haircut":"✂️ Стрижки","travel":"✈️ Путешествия",
             "shopping":"🛍️ Покупки","health":"❤️ Здоровье"}
    for k, label in icons.items():
        lines.append(f"{label}: {lst(fav[k]['favorable'])}")

    # --- пояснение VoC ---
    lines.append("\n<i>Void-of-Course — временной отрезок, когда Луна завершила все "
                 "ключевые аспекты в знаке и ещё не вошла в следующий. В это время "
                 "энергия рассеяна, поэтому старт важных дел лучше перенести.</i>")

    return "\n".join(lines)

async def main():
    fn = Path("lunar_calendar.json")
    if not fn.exists():
        print("❌ lunar_calendar.json not found")
        return

    data = json.loads(fn.read_text("utf-8"))
    txt  = build_message(data)

    bot = Bot(TOKEN)
    try:
        await bot.send_message(CHAT_ID, txt,
                               parse_mode="HTML",
                               disable_web_page_preview=True)
        print("✅ monthly post sent")
    except tg_err.TelegramError as e:
        print("❌ Telegram error:", e)

if __name__ == "__main__":
    asyncio.run(main())