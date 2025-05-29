#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import json, html, pendulum, os
from pathlib import Path
from telegram import Bot

TZ = pendulum.timezone("Asia/Nicosia")
TOKEN   = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = int(os.getenv("CHANNEL_ID", "0"))

ICON = {            # та же таблица, что в генераторе
    "Новолуние":"🌑","Растущий серп":"🌒","Первая четверть":"🌓","Растущая Луна":"🌔",
    "Полнолуние":"🌕","Убывающая Луна":"🌖","Последняя четверть":"🌗","Убывающий серп":"🌘",
}

def build_month_message(data: dict) -> str:
    # ── заголовок ─────────────────────────────────
    first_key = next(iter(data))
    month_title = pendulum.parse(first_key).in_tz(TZ).format("MMMM YYYY").upper()
    lines = [f"🌙 <b>Лунный календарь на {month_title}</b>", ""]

    # ── группируем по (phase, sign) подряд ────────
    segments = []
    current = None
    for day in sorted(data.keys()):
        rec = data[day]
        key = (rec["phase_name"], rec["sign"])
        if current is None or current["key"] != key:
            # новый сегмент
            current = {
                "key": key,
                "icon": ICON[rec["phase_name"]],
                "sign": rec["sign"],
                "start": day,
                "end":   day,
                "desc":  rec["long_desc"],
            }
            segments.append(current)
        else:
            current["end"] = day

    # ── печать сегментов ──────────────────────────
    for seg in segments:
        d1 = pendulum.parse(seg["start"]).format("D")
        d2 = pendulum.parse(seg["end"]).format("D MMMM")
        lines.append(f"{seg['icon']} <b>{d1}–{d2} {seg['sign']}</b>")
        lines.append(f"<i>{html.escape(seg['desc'])}</i>")
        lines.append("")

    # ── сводка дней ───────────────────────────────
    cats = data[first_key]["favorable_days"]
    fav = ", ".join(map(str, cats["general"]["favorable"]))
    unf = ", ".join(map(str, cats["general"]["unfavorable"]))
    lines += [
        f"✅ <b>Благоприятные дни:</b> {fav}",
        f"❌ <b>Неблагоприятные:</b> {unf}",
        f"✂️ <b>Стрижки:</b> {', '.join(map(str, cats['haircut']['favorable']))}",
        f"✈️ <b>Путешествия:</b> {', '.join(map(str, cats['travel']['favorable']))}",
        f"🛍️ <b>Покупки:</b> {', '.join(map(str, cats['shopping']['favorable']))}",
        f"❤️ <b>Здоровье:</b> {', '.join(map(str, cats['health']['favorable']))}",
        "",
    ]

    # ── блок VoC + пояснение ──────────────────────
    voc_lines = []
    for date, rec in data.items():
        s, e = rec["void_of_course"].values()
        if s and e:
            d = pendulum.parse(date).in_tz(TZ).format("D MMM")
            voc_lines.append(f"{d}: {s} → {e}")

    if voc_lines:
        lines.append("<b>Void-of-Course (UTC+3)</b>")
        lines += voc_lines
        lines.append("")
        lines.append(
            "<i>Void-of-Course — интервал, когда Луна завершила все ключевые аспекты "
            "в знаке и ещё не вошла в следующий. Дела, запущенные в это время, часто "
            "«висят в воздухе», поэтому важные старты лучше планировать после окончания V/C.</i>"
        )

    return "\n".join(lines)

# ── entry-point ───────────────────────────────────
def main():
    data = json.loads(Path("lunar_calendar.json").read_text(encoding="utf-8"))
    msg  = build_month_message(data)

    bot = Bot(TOKEN)
    bot.send_message(CHAT_ID, msg,
                     parse_mode="HTML", disable_web_page_preview=True)

if __name__ == "__main__":
    main()