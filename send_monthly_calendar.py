#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Отправка «большого» поста-резюме на месяц в Telegram-канал.

• читает lunar_calendar.json, сформированный gen_lunar_calendar.py
• собирает текст: краткие фазы, длинные описания, сводки + VoC
• фильтрует Void-of-Course короче MIN_VOC_MINUTES
• постит в канал, прикрепляя эмодзи-иконку к заголовку
"""

import os, json, asyncio, html
from pathlib import Path
from typing import Dict, Any, List

import pendulum
from telegram import Bot, constants

# ── настройки ──────────────────────────────────────────────────────────────
TZ                = pendulum.timezone("Asia/Nicosia")
CAL_FILE          = "lunar_calendar.json"
MIN_VOC_MINUTES   = 15       # VoC короче этого не показываем
MOON_EMOJI        = "🌙"

TOKEN   = os.getenv("TELEGRAM_TOKEN", "")
CHAT_ID = os.getenv("CHANNEL_ID",  "")    # канал или чат

if not TOKEN or not CHAT_ID:
    raise RuntimeError("TELEGRAM_TOKEN / CHANNEL_ID не заданы в переменных среды")

# ── helpers ────────────────────────────────────────────────────────────────
def _parse_dt(s: str, year: int):
    """
    Принимает ISO-8601 или «DD.MM HH:mm» и возвращает pendulum.DateTime в TZ.
    """
    try:
        return pendulum.parse(s).in_tz(TZ)
    except Exception:
        try:
            dmy, hm  = s.split()
            day, mon = map(int, dmy.split("."))
            hh,  mm  = map(int, hm.split(":"))
            return pendulum.datetime(year, mon, day, hh, mm, tz=TZ)
        except Exception as e:
            raise ValueError(f"Не удалось разобрать дату '{s}': {e}")

def build_phase_blocks(data: Dict[str, Any]) -> str:
    """
    Группируем подряд идущие дни с одинаковым phase_name.
    В заголовке блока оставляем символ фазы, диапазон дат и перечисляем знаки.
    """
    lines: List[str] = []
    days = sorted(data.keys())

    i = 0
    while i < len(days):
        start = days[i]
        rec   = data[start]
        name  = rec["phase_name"]
        emoji = rec["phase"].split()[0]          # первый токен — эмодзи фазы
        signs = {rec["sign"]}
        j = i
        while j + 1 < len(days) and data[days[j + 1]]["phase_name"] == name:
            j += 1
            signs.add(data[days[j]]["sign"])

        # диапазон дат + знаки
        d1 = pendulum.parse(start).format("D")
        d2 = pendulum.parse(days[j]).format("D MMM", locale="ru")
        date_span = f"{d1}–{d2}" if i != j else d2
        signs_str = ", ".join(sorted(signs, key=lambda s: ["Овен","Телец","Близнецы","Рак","Лев","Дева","Весы","Скорпион","Стрелец","Козерог","Водолей","Рыбы"].index(s)))

        # длинное описание из первого дня блока
        desc = rec.get("long_desc", "").strip()
        lines.append(f"<b>{emoji} {date_span}</b> <i>({signs_str})</i>\n<i>{html.escape(desc)}</i>\n")

        i = j + 1
    return "\n".join(lines)

def build_fav_blocks(rec: Dict[str, Any]) -> str:
    fav = rec["favorable_days"]
    def fmt(cat): return ", ".join(map(str, fav[cat]["favorable"]))
    def unf(cat): return ", ".join(map(str, fav[cat]["unfavorable"]))

    parts = [
        f"✅ <b>Благоприятные дни:</b> {fmt('general')}",
        f"❌ <b>Неблагоприятные:</b> {unf('general')}",
        f"✂️ Haircut: {fmt('haircut')}",
        f"✈️ Travel: {fmt('travel')}",
        f"🛍️ Shopping: {fmt('shopping')}",
        f"❤️ Health: {fmt('health')}",
    ]
    return "\n".join(parts)

def build_voc_list(data: Dict[str, Any], year: int) -> str:
    voc_lines: List[str] = []
    for d in sorted(data.keys()):
        rec = data[d]["void_of_course"]
        if not rec or not rec["start"] or not rec["end"]:
            continue
        t1 = _parse_dt(rec["start"], year)
        t2 = _parse_dt(rec["end"],   year)
        if (t2 - t1).in_minutes() < MIN_VOC_MINUTES:
            continue
        voc_lines.append(f"• {t1.format('DD.MM HH:mm')}  →  {t2.format('DD.MM HH:mm')}")
    if not voc_lines:
        return ""
    return f"<b>⚫️ Void-of-Course:</b>\n" + "\n".join(voc_lines)

def build_message(data: Dict[str, Any]) -> str:
    first_day = pendulum.parse(sorted(data.keys())[0])
    header = f"{MOON_EMOJI} <b>Лунный календарь на {first_day.format('MMMM YYYY', locale='ru').upper()}</b>\n"

    phases = build_phase_blocks(data)
    fav    = build_fav_blocks(next(iter(data.values())))
    voc    = build_voc_list(data, first_day.year)

    return "\n".join([header, phases, fav, "", voc,
                      "\n<i>Void-of-Course — период, когда Луна завершила все аспекты в знаке и ещё не вошла в следующий; энергия рассеяна, новые начинания лучше отложить.</i>"])

# ── main ──────────────────────────────────────────────────────────────────
async def main():
    data = json.loads(Path(CAL_FILE).read_text("utf-8"))
    text = build_message(data)

    bot  = Bot(TOKEN, parse_mode=constants.ParseMode.HTML)
    await bot.send_message(chat_id=CHAT_ID, text=text)

if __name__ == "__main__":
    asyncio.run(main())