#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
send_monthly_calendar.py  ▸  формирует и отправляет в канал
«объёмно-концентрированный» лунный календарь на месяц.
Предполагает, что в репозитории уже лежит актуальный lunar_calendar.json.
"""

import json
import os
import asyncio
from pathlib import Path
from collections import defaultdict

import pendulum
from telegram import Bot, error as tg_err

TZ       = pendulum.timezone("Asia/Nicosia")
TOKEN    = os.getenv("TELEGRAM_TOKEN", "")
CHAT_ID  = int(os.getenv("CHANNEL_ID", 0))

# ---------------------------------------------------------------------------


def normalize_tip(text: str) -> str:
    """
    Чистим GPT-фразы вида «Конечно! Вот три совета…».
    Оставляем первую осмысленную строку.
    """
    txt = text.strip()
    for bad in ("конечно", "вот", "совет", "recommend", "tip"):
        if bad.lower() in txt.lower()[:30]:
            # убираем всё до первого «:»
            if ":" in txt:
                txt = txt.split(":", 1)[1].lstrip()
    return txt.rstrip("…").strip()


def group_by_phase(data: dict) -> list[dict]:
    """
    Проходим даты по порядку, склеиваем непрерывные дни
    с одинаковым названием фазы (без знака).
    """
    dates_sorted = sorted(data.keys())
    segments: list[dict] = []
    current = None

    for ds in dates_sorted:
        rec = data[ds]
        phase_full = rec["phase"]
        phase_name = phase_full.split(" в ")[0].split("(")[0].strip()

        if current is None or current["phase_name"] != phase_name:
            # начинаем новый сегмент
            current = {
                "phase_name": phase_name,
                "phase_full": phase_full,
                "phase_time": rec.get("phase_time"),
                "dates": [ds],
                "vc": rec.get("void_of_course") or {},
                "advice": normalize_tip(rec["advice"][0]) if rec.get("advice") else "",
            }
            segments.append(current)
        else:
            current["dates"].append(ds)

    return segments


def collect_day_lists(data: dict) -> dict[str, list[int]]:
    """Собираем уникальные дни по категориям за месяц."""
    cats = defaultdict(set)
    for rec in data.values():
        fav = rec.get("favorable_days", {})
        for cat, arr in fav.items():
            cats[f"{cat}_fav"].update(arr)
        unf = rec.get("unfavorable_days", {})
        for cat, arr in unf.items():
            cats[f"{cat}_unf"].update(arr)
    # переводим в отсортированные списки
    return {k: sorted(v) for k, v in cats.items()}


def build_month_message(data: dict) -> str:
    first_date = pendulum.parse(sorted(data.keys())[0]).in_tz(TZ)
    header = f"🌙 <b>Лунный календарь на {first_date.format('MMMM YYYY').upper()}</b>\n"
    lines  = [header]

    # --- основной блок по фазам ---
    for seg in group_by_phase(data):
        d0 = pendulum.parse(seg["dates"][0]).in_tz(TZ).format("D.MM")
        d1 = pendulum.parse(seg["dates"][-1]).in_tz(TZ).format("D.MM")
        time_iso = pendulum.parse(seg["phase_time"]).in_tz(TZ).format("DD.MM HH:mm") if seg["phase_time"] else "—"

        lines.append(f"<b>{seg['phase_full']}</b> ({time_iso}; {d0}–{d1})")

        # Void-of-Course, если есть
        vc = seg["vc"]
        if vc.get("start") and vc.get("end"):
            lines.append(f"Void-of-Course: {vc['start']} → {vc['end']}")

        # Совет
        if seg["advice"]:
            lines.append(seg["advice"])

        lines.append("—")  # разделитель

    # --- сводка дней ---
    cats = collect_day_lists(data)
    def fmt(lst): return ", ".join(map(str, lst)) if lst else "—"

    lines.append(f"✅ <b>Общие благоприятные дни месяца:</b> {fmt(cats.get('general_fav', []))}")
    if cats.get("general_unf"):
        lines.append(f"❌ <b>Общие неблагоприятные дни месяца:</b> {fmt(cats['general_unf'])}")

    cat_titles = {
        "haircut": "✂️ Стрижки",
        "travel":  "✈️ Путешествия",
        "shopping":"🛍️ Покупки",
        "health":  "❤️ Здоровье",
    }
    for key, title in cat_titles.items():
        lst = cats.get(f"{key}_fav", [])
        if lst:
            lines.append(f"{title}: {fmt(lst)}")

    # --- пояснение V/C ---
    lines += [
        "",
        "<i>Что такое Void-of-Course?</i>",
        "Void-of-Course — интервал, когда Луна завершила все ключевые аспекты в текущем знаке и ещё не вошла в следующий. "
        "Энергия рассеивается, поэтому старт важных дел, подписи контрактов и крупные покупки лучше перенести "
        "на время после окончания V/C.</i>",
    ]

    return "\n".join(lines)


async def main() -> None:
    path = Path(__file__).parent / "lunar_calendar.json"
    if not path.exists():
        print("❌ lunar_calendar.json not found.")
        return

    data = json.loads(path.read_text(encoding="utf-8"))
    msg  = build_month_message(data)

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
