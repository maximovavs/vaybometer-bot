#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Читает lunar_calendar.json и публикует «красивый» пост на месяц.
"""

import os, json, asyncio, html
from pathlib import Path
import pendulum
from telegram import Bot, error as tg_err

TOKEN   = os.getenv("TELEGRAM_TOKEN","")
CHAT_ID = int(os.getenv("CHANNEL_ID","0"))
TZ      = pendulum.timezone("Asia/Nicosia")

EMO   = { "Новолуние":"🌑","Растущий":"🌒","Первая":"🌓","Растущая":"🌔",
          "Полнолуние":"🌕","Убывающая":"🌖","Последняя":"🌗","Убывающий":"🌘" }

def fmt_dates(lst):
    if len(lst)==1: return lst[0]
    return f"{lst[0]}–{lst[-1]}"

def build_message(data:dict)->str:
    # сортированные даты
    days = sorted(data.keys())
    year_month = pendulum.parse(days[0]).in_tz(TZ).format("MMMM YYYY").upper()
    lines=[f"<b>🌙 Лунный календарь на {html.escape(year_month)}</b>"]
    # ── группируем
    seg=[]
    cur=None
    for d in days:
        name=data[d]["phase"].split()[1]   # без emoji
        if cur is None or cur["name"]!=name:
            cur={"name":name,"emoji":data[d]["phase"].split()[0],
                 "dates":[d],"desc":data[d]["long_desc"]}
            seg.append(cur)
        else:
            cur["dates"].append(d)
    # ── вывод
    for s in seg:
        rng=fmt_dates([pendulum.parse(x).format('D MMM') for x in s["dates"]])
        lines.append("")
        lines.append(f"<b>{s['emoji']} {s['name']}</b> • {rng}")
        lines.append(f"{html.escape(s['desc'])}")
    lines.append("")
    # ── краткая таблица благоприятных
    first=data[days[0]]
    fav=first["favorable_days"]; unf=first["unfavorable_days"]
    def cat(icon,key): 
        arr=fav.get(key,[])
        if not arr: return ""
        return f"{icon} <b>{key.capitalize()}:</b> {', '.join(map(str,arr))}"
    lines.append(f"✅ <b>Благоприятные дни:</b> {', '.join(map(str,fav['general']))}")
    lines.append(f"❌ <b>Неблагоприятные:</b> {', '.join(map(str,unf['general']))}")
    lines.append(cat("✂️","haircut"))
    lines.append(cat("✈️","travel"))
    lines.append(cat("🛍️","shopping"))
    lines.append(cat("❤️","health"))
    lines.append("")
    lines.append("<i>Void-of-Course</i> — временной отрезок, когда Луна завершила все аспекты "
                 "в знаке и ещё не вошла в следующий. В это время не стартуют важные дела.")
    return "\n".join([l for l in lines if l.strip()])

async def main():
    if not Path("lunar_calendar.json").exists():
        print("No calendar.")
        return
    data=json.loads(Path("lunar_calendar.json").read_text('utf-8'))
    msg=build_message(data)
    try:
        await Bot(TOKEN).send_message(CHAT_ID,msg,parse_mode="HTML",disable_web_page_preview=True)
        print("✅ posted")
    except tg_err.TelegramError as e:
        print("❌ Telegram error:",e)

if __name__=="__main__":
    asyncio.run(main())