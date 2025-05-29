#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Формирует и публикует месячное сообщение-резюме в Telegram
  • разбивка только по фазам (без группировки по знаку)
  • знаки за весь интервал выводятся через запятую
  • список VoC фильтруется: показываем только интервалы ≥ 15 минут
"""

import json, os, asyncio, textwrap
from pathlib import Path
import pendulum
from telegram import Bot          # python-telegram-bot ≥ 20,<21

TZ               = pendulum.timezone("Asia/Nicosia")
MIN_VOC_MINUTES  = 15

EMO = {                 # те же, что в генераторе
    "Новолуние":"🌑","Растущий серп":"🌒","Первая четверть":"🌓","Растущая Луна":"🌔",
    "Полнолуние":"🌕","Убывающая Луна":"🌖","Последняя четверть":"🌗","Убывающий серп":"🌘"
}

# ────────── служебные функции ──────────────────────────────────────────────
def load_calendar() -> dict:
    with open("lunar_calendar.json", encoding="utf-8") as f:
        return json.load(f)

def build_phase_blocks(data: dict) -> list[str]:
    """Последовательные отрезки с одной фазой.
       Знак Луны берём из каждого дня и собираем set → строку."""
    days = sorted(data.keys())
    blocks = []
    start = days[0]
    cur_phase = data[start]["phase_name"]
    signs     = {data[start]["sign"]}

    for prev, cur in zip(days, days[1:]):
        if data[cur]["phase_name"] == cur_phase:
            signs.add(data[cur]["sign"])
            continue                       # продолжаем тот же блок

        # фаза сменилась → завершаем блок
        blocks.append( (start, prev, cur_phase, sorted(signs),
                        data[start]["long_desc"].strip()) )
        # инициируем новый
        start, cur_phase, signs = cur, data[cur]["phase_name"], {data[cur]["sign"]}

    # последний хвост
    blocks.append( (start, days[-1], cur_phase, sorted(signs),
                    data[start]["long_desc"].strip()) )
    return blocks

def fmt_date(d: str) -> str:
    dt = pendulum.parse(d)
    return dt.format("D")       # «1», «27» … нам нужен только номер

def build_voc_list(data: dict) -> list[str]:
    """Возвращает строки «• 01.05 10:59 → 01.05 12:10» только если длительность ≥ MIN_VOC_MINUTES"""
    voc_lines = []
    for d in sorted(data.keys()):
        rec = data[d]["void_of_course"]
        if not rec or rec["start"] is None or rec["end"] is None:
            continue
        t1 = pendulum.parse(rec["start"]).in_tz(TZ)
        t2 = pendulum.parse(rec["end"  ]).in_tz(TZ)
        if (t2 - t1).in_minutes() < MIN_VOC_MINUTES:
            continue                           # слишком короткий → пропускаем
        line = f"• {t1.format('DD.MM HH:mm')}  →  {t2.format('DD.MM HH:mm')}"
        voc_lines.append(line)
    return voc_lines

def build_message(data: dict) -> str:
    blocks = build_phase_blocks(data)

    # 1) заголовок
    first_day = pendulum.parse(min(data.keys()))
    title = f"🌙 Лунный календарь на {first_day.format('MMMM YYYY', locale='ru')}\n"

    # 2) фазовые блоки
    phases_txt = []
    for start, end, name, signs, desc in blocks:
        rng   = f"{fmt_date(start)}–{fmt_date(end)} {first_day.format('MMMM', locale='ru')}"
        signs_str = ", ".join(signs)
        phases_txt.append(f"{EMO[name]} <b>{rng}</b> ({signs_str})\n<i>{desc}</i>")

    # 3) агрегированные дни (берём из любого дня – они одинаковые)
    cats   = data[start]["favorable_days"]     # любой rec
    fav    = cats["general"]["favorable"]
    un_fav = cats["general"]["unfavorable"]
    cat_lines = [
        f"✅ <b>Благоприятные дни:</b> {', '.join(map(str,fav))}",
        f"❌ <b>Неблагоприятные:</b> {', '.join(map(str,un_fav))}",
    ]
    for key, emoji in [("haircut","✂️"),("travel","✈️"),("shopping","🛍️"),("health","❤️")]:
        vals = ", ".join(map(str, cats[key]["favorable"]))
        cat_lines.append(f"{emoji} {key.capitalize()}: {vals}")

    # 4) VoC
    voc_list = build_voc_list(data)
    voc_block = ""
    if voc_list:
        voc_block = "<b>🌓 Void-of-Course:</b>\n" + "\n".join(voc_list) + \
                    "\n\n<i>Void-of-Course — период, когда Луна завершила все аспекты в знаке и ещё не вошла в следующий; энергия рассеяна, новые начинания лучше отложить.</i>"

    # собрать всё
    parts = [title, *phases_txt, *cat_lines]
    if voc_block:
        parts.append(voc_block)

    # Telegram лимит 4096 симв. – безопасно режем по абзацам
    msg = "\n\n".join(parts)
    return textwrap.shorten(msg, width=4000, placeholder="…")   # на всякий случай

# ────────── публикация ─────────────────────────────────────────────────────
async def main() -> None:
    token  = os.getenv("TELEGRAM_TOKEN")
    chat   = os.getenv("CHANNEL_ID")
    if not (token and chat):
        raise RuntimeError("TELEGRAM_TOKEN / CHANNEL_ID не заданы")

    data = load_calendar()
    text = build_message(data)

    bot = Bot(token)
    await bot.send_message(chat_id=chat,
                           text=text,
                           parse_mode="HTML",
                           disable_web_page_preview=True)

if __name__ == "__main__":
    asyncio.run(main())