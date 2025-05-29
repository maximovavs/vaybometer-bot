#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Месячный астрокалендарь в Telegram-канал
"""

import os, json, html, asyncio
from pathlib import Path
from collections import OrderedDict, defaultdict
import pendulum
from telegram import Bot, error as tg_err

TOKEN   = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = int(os.getenv("CHANNEL_ID", "0"))
TZ      = pendulum.timezone("Asia/Nicosia")

EMOJI = {
    "Новолуние":"🌑","Растущий серп":"🌒","Первая четверть":"🌓","Растущая Луна":"🌔",
    "Полнолуние":"🌕","Убывающая Луна":"🌖","Последняя четверть":"🌗","Убывающий серп":"🌘"
}

def fmt_range(ds, de):
    s, e = pendulum.parse(ds, tz=TZ), pendulum.parse(de, tz=TZ)
    if s.month != e.month:
        return f"{s.day} {s.format('MMM')}–{e.day} {e.format('MMM')}"
    return f"{s.day}–{e.day}\u00A0{s.format('MMM', locale='ru')}".lower()

def build_message(data: dict):
    ordered, voc_list = OrderedDict(), []
    for day, rec in data.items():
        name = rec["phase_name"]
        if name not in ordered:
            ordered[name] = {"emoji": EMOJI.get(name,""), "dates":[day], "desc":rec["long_desc"]}
        else:
            ordered[name]["dates"].append(day)

        v = rec["void_of_course"]
        if v["start"] and v["end"]:
            voc_list.append((v["start"], v["end"]))

    first_date = next(iter(data))
    month_ru   = pendulum.parse(first_date).format("MMMM YYYY", locale="ru").upper()
    out = [f"<b>🌙 Лунный календарь на {month_ru}</b>\n"]

    for seg in ordered.values():
        seg["dates"].sort()
        rng = fmt_range(seg["dates"][0], seg["dates"][-1])
        out.append(f"{seg['emoji']} <b>{rng}</b>")
        out.append(f"<i>{html.escape(seg['desc'])}</i>\n")

    any_day = next(iter(data.values()))
    fav, unf = any_day["favorable_days"], any_day["unfavorable_days"]
    def lst(L): return ", ".join(map(str,L)) if L else "—"

    out.append(f"✅ <b>Благоприятные дни:</b> {lst(fav['general']['favorable'])}")
    out.append(f"❌ <b>Неблагоприятные:</b> {lst(unf['general']['unfavorable'])}\n")

    icons = {"haircut":"✂️ Стрижки","travel":"✈️ Путешествия",
             "shopping":"🛍️ Покупки","health":"❤️ Здоровье"}
    for k,lbl in icons.items():
        out.append(f"{lbl}: {lst(fav[k]['favorable'])}")

    # --- пояснение + перечень VoC ---
    out.append(
        "\n<i>Void-of-Course — временной отрезок, когда Луна завершила все ключевые "
        "аспекты в знаке и ещё не вошла в следующий. В это время энергия рассеяна, "
        "поэтому старт важных дел лучше перенести.</i>\n")
    if voc_list:
        out.append("🕳️ <b>Void-of-Course в месяце:</b>")
        for s,e in voc_list:
            st = pendulum.parse(s).format('DD.MM HH:mm')
            en = pendulum.parse(e).format('DD.MM HH:mm')
            out.append(f"• {st} → {en}")
    return "\n".join(out)

async def main():
    fn = Path("lunar_calendar.json")
    if not fn.exists():
        print("❌ lunar_calendar.json not found")
        return
    txt = build_message(json.loads(fn.read_text("utf-8")))
    bot = Bot(TOKEN)
    try:
        await bot.send_message(CHAT_ID, txt, parse_mode="HTML",
                               disable_web_page_preview=True)
        print("✅ monthly post sent")
    except tg_err.TelegramError as e:
        print("❌ Telegram error:", e)

if __name__ == "__main__":
    asyncio.run(main())