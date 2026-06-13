#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Build and optionally send a sanitized VayboMeter post.

Used for safe FORMAT_V2 tests and controlled manual routing. Scheduled production
runs stay on the legacy path unless the workflow explicitly enables FORMAT_V2.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import re

import pendulum
from telegram import Bot, constants

from post_common import build_message
from post_safety import sanitize_post_text, split_telegram_text, validation_summary

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

TOKEN = os.getenv("TELEGRAM_TOKEN", "").strip()
TZ_STR = os.getenv("TZ", "Asia/Nicosia")

SEA_LABEL = "Морские города"
OTHER_LABEL = "Континентальные города"
SEA_CITIES_ORDERED = [
    ("Limassol", (34.707, 33.022)),
    ("Pafos", (34.776, 32.424)),
    ("Ayia Napa", (34.988, 34.012)),
    ("Larnaca", (34.916, 33.624)),
]
OTHER_CITIES_ALL = {
    "Nicosia": (35.170, 33.360),
    "Troodos": (34.916, 32.823),
}


def _env_on(name: str, default: bool = False) -> bool:
    val = os.getenv(name)
    if val is None:
        return default
    return str(val).strip().lower() in ("1", "true", "yes", "on")


def _plain(text: str) -> str:
    return re.sub(r"</?b>", "", str(text or "")).strip()


def _num(pattern: str, text: str) -> float | None:
    m = re.search(pattern, _plain(text), flags=re.I)
    if not m:
        return None
    try:
        return float(m.group(1).replace(",", "."))
    except Exception:
        return None


def _cy_place(city: str) -> str:
    c = str(city or "").strip()
    if not c:
        return ""
    return {"Никосия": "в Никосии", "Тродос": "на Тродосе"}.get(c, f"в {c}")


def _cyprus_conditions(v2_text: str) -> dict[str, float | bool | str | None]:
    lines = [x.strip() for x in str(v2_text or "").splitlines() if x.strip()]
    temp_line = next((x for x in lines if x.startswith("🌡 Теплее всего")), "")
    wind_line = next((x for x in lines if x.startswith("💨")), "")
    uv_line = next((x for x in lines if x.startswith("☀️")), "")
    air_line = next((x for x in lines if x.startswith("🏭")), "")

    warm = re.search(r"Теплее всего\s*[—-]\s*([^()]+)\(([-+]?\d+(?:[\.,]\d+)?)°\)", _plain(temp_line))
    cool = re.search(r"прохладнее\s*[—-]\s*([^()]+)\(([-+]?\d+(?:[\.,]\d+)?)°\)", _plain(temp_line))
    return {
        "warm_city": warm.group(1).strip() if warm else "",
        "cool_city": cool.group(1).strip() if cool else "",
        "warm_t": float(warm.group(2).replace(",", ".")) if warm else None,
        "cool_t": float(cool.group(2).replace(",", ".")) if cool else None,
        "wind": _num(r"Ветер:\s*(\d+(?:[\.,]\d+)?)", wind_line),
        "gust": _num(r"порывы\s+до\s*(\d+(?:[\.,]\d+)?)", wind_line),
        "uv": _num(r"УФ\s*(\d+(?:[\.,]\d+)?)", uv_line),
        "aqi": _num(r"AQI\s*(\d+(?:[\.,]\d+)?)", air_line),
    }


def _cyprus_feels_line(v2_text: str) -> str:
    c = _cyprus_conditions(v2_text)
    warm_city = str(c.get("warm_city") or "")
    cool_city = str(c.get("cool_city") or "")
    warm_t = c.get("warm_t")
    wind = c.get("wind")
    gust = c.get("gust")
    uv = c.get("uv")

    parts: list[str] = []
    warm_place = _cy_place(warm_city)
    cool_place = _cy_place(cool_city)
    if isinstance(warm_t, (int, float)) and warm_place:
        if warm_t >= 31:
            parts.append(f"жарко {warm_place}")
        elif warm_t >= 28:
            parts.append(f"очень тепло {warm_place}")
        else:
            parts.append(f"тепло {warm_place}")
    if cool_place:
        parts.append(f"свежее {cool_place}")
    if isinstance(gust, (int, float)) and gust >= 15:
        parts.append("у моря порывы ощутимы")
    elif isinstance(wind, (int, float)) and wind >= 5:
        parts.append("ветер заметный у моря")
    if isinstance(uv, (int, float)) and uv >= 8:
        parts.append("на солнце высокая нагрузка")
    elif isinstance(uv, (int, float)) and uv >= 6:
        parts.append("SPF обязателен")
    return "🌡 Ощущается: " + "; ".join(parts[:4]) + "." if parts else ""


def _cyprus_best_window_line(v2_text: str) -> str:
    c = _cyprus_conditions(v2_text)
    uv = c.get("uv")
    gust = c.get("gust")

    if isinstance(uv, (int, float)) and uv >= 8:
        tail = "днём — тень"
        if isinstance(gust, (int, float)) and gust >= 15:
            tail += ", у моря — защищённые места"
        return f"🕒 Лучшее окно: до 11:00 и после 18:30; {tail}."
    if isinstance(uv, (int, float)) and uv >= 6:
        return "🕒 Лучшее окно: до 12:00 и ближе к закату; в полдень лучше тень."
    if isinstance(gust, (int, float)) and gust >= 15:
        return "🕒 Лучшее окно: спокойные утренние часы; у моря — по фактическому ветру."
    return "🕒 Лучшее окно: позднее утро и время перед закатом."


def _cyprus_smart_plan_line(v2_text: str) -> str:
    c = _cyprus_conditions(v2_text)
    warm_t = c.get("warm_t")
    uv = c.get("uv")
    gust = c.get("gust")
    hot = isinstance(warm_t, (int, float)) and warm_t >= 31
    high_uv = isinstance(uv, (int, float)) and uv >= 8
    windy = isinstance(gust, (int, float)) and gust >= 15

    if hot and high_uv and windy:
        return "✅ План: дела и прогулка до 11:00; 11–16 — тень/помещение; SPF 50 и вода с собой; у моря — защищённые места."
    if high_uv and windy:
        return "✅ План: активность до 11:00 или после 18:30; 11–16 — тень; SPF 50, вода; у моря — защищённые места."
    if hot and high_uv:
        return "✅ План: основные дела до 11:00; 11–16 — тень/помещение; SPF 50 и вода; прогулка ближе к закату."
    if high_uv:
        return "✅ План: SPF 50, вода с собой; полдень провести в тени; прогулка утром или ближе к закату."
    if windy:
        return "✅ План: у моря выбирать защищённые места; лёгкие вещи закрепить; прогулку сверять с фактическим ветром."
    return ""


def _cyprus_score_line(v2_text: str) -> str:
    c = _cyprus_conditions(v2_text)
    warm_t = c.get("warm_t")
    uv = c.get("uv")
    gust = c.get("gust")
    wind = c.get("wind")
    aqi = c.get("aqi")

    score = 10.0
    reasons: list[str] = []
    if isinstance(warm_t, (int, float)):
        if warm_t >= 35:
            score -= 2.0; reasons.append("сильная жара")
        elif warm_t >= 32:
            score -= 1.4; reasons.append("жара")
        elif warm_t >= 30:
            score -= 0.8; reasons.append("тепло")
    if isinstance(uv, (int, float)):
        if uv >= 9:
            score -= 1.5; reasons.append("очень высокий УФ")
        elif uv >= 8:
            score -= 1.3; reasons.append("высокий УФ")
        elif uv >= 6:
            score -= 0.7; reasons.append("УФ заметный")
    if isinstance(gust, (int, float)):
        if gust >= 18:
            score -= 1.1; reasons.append("порывы у моря")
        elif gust >= 15:
            score -= 0.8; reasons.append("ветер у моря")
    elif isinstance(wind, (int, float)) and wind >= 6:
        score -= 0.5; reasons.append("ветер")
    if isinstance(aqi, (int, float)) and aqi > 80:
        score -= 0.8; reasons.append("воздух похуже")

    score = max(1.0, min(10.0, score))
    label = "отлично" if score >= 8.5 else "хорошо" if score >= 7 else "с оговорками" if score >= 5.5 else "бережный режим"
    if reasons:
        return f"✨ VayboMeter: {score:.1f}/10 — {label}; " + ", ".join(reasons[:3]) + "."
    return f"✨ VayboMeter: {score:.1f}/10 — {label} для обычных дел и прогулок."


def _inject_after_anchor(v2_text: str, line_to_add: str, anchors: tuple[str, ...]) -> str:
    if not line_to_add:
        return v2_text
    lines = str(v2_text or "").splitlines()
    out: list[str] = []
    inserted = False
    for line in lines:
        out.append(line)
        if not inserted and line.strip().startswith(anchors):
            out.append(line_to_add)
            inserted = True
    return "\n".join(out)


def _replace_plan(v2_text: str, new_plan: str) -> str:
    if not new_plan:
        return v2_text
    lines = str(v2_text or "").splitlines()
    out: list[str] = []
    replaced = False
    for line in lines:
        if not replaced and line.strip().startswith("✅"):
            out.append(new_plan)
            replaced = True
        else:
            out.append(line)
    if not replaced:
        out.append(new_plan)
    return "\n".join(out)


def _inject_morning_feels(v2_text: str, mode: str) -> str:
    if not (mode.startswith("morn") and _env_on("MORNING_FEELS_LIKE")):
        return v2_text
    feels = _cyprus_feels_line(v2_text)
    if not feels:
        return v2_text
    lines = str(v2_text or "").splitlines()
    out: list[str] = []
    inserted = False
    for line in lines:
        out.append(line)
        if not inserted and line.strip().startswith("💨"):
            out.append(feels)
            inserted = True
    if not inserted:
        for i, line in enumerate(out):
            if line.strip().startswith("🌡"):
                out.insert(i + 1, feels)
                inserted = True
                break
    return "\n".join(out)


def _inject_morning_best_window(v2_text: str, mode: str) -> str:
    if not (mode.startswith("morn") and _env_on("MORNING_BEST_WINDOW")):
        return v2_text
    window = _cyprus_best_window_line(v2_text)
    if not window:
        return v2_text
    if "🌡 Ощущается:" in v2_text:
        return _inject_after_anchor(v2_text, window, ("🌡 Ощущается:",))
    return _inject_after_anchor(v2_text, window, ("💨", "🌡"))


def _inject_morning_score(v2_text: str, mode: str) -> str:
    if not (mode.startswith("morn") and _env_on("MORNING_VAYBOMETER_SCORE")):
        return v2_text
    score = _cyprus_score_line(v2_text)
    if "🕒 Лучшее окно:" in v2_text:
        return _inject_after_anchor(v2_text, score, ("🕒 Лучшее окно:",))
    if "🌡 Ощущается:" in v2_text:
        return _inject_after_anchor(v2_text, score, ("🌡 Ощущается:",))
    return _inject_after_anchor(v2_text, score, ("💨", "🌡"))


def _inject_morning_smart_plan(v2_text: str, mode: str) -> str:
    if not (mode.startswith("morn") and _env_on("MORNING_SMART_PLAN")):
        return v2_text
    return _replace_plan(v2_text, _cyprus_smart_plan_line(v2_text))


def resolve_chat_id(args_chat: str, to_test: bool) -> int:
    chat = (args_chat or "").strip()
    if chat:
        return int(chat)
    if to_test:
        chat = os.getenv("CHANNEL_ID_TEST", "").strip()
        if not chat:
            raise SystemExit("--to-test задан, но CHANNEL_ID_TEST не определён")
        return int(chat)
    raise SystemExit("Safe runner refuses production send. Use --to-test or --chat-id explicitly.")


class _TodayPatch:
    def __init__(self, base_date: pendulum.DateTime):
        self.base_date = base_date
        self._orig_today = None
        self._orig_now = None

    def __enter__(self):
        self._orig_today = pendulum.today
        self._orig_now = pendulum.now

        def _fake(dt: pendulum.DateTime, tz_arg=None):
            return dt.in_tz(tz_arg) if tz_arg else dt

        pendulum.today = lambda tz_arg=None: _fake(self.base_date, tz_arg)  # type: ignore[assignment]
        pendulum.now = lambda tz_arg=None: _fake(self.base_date, tz_arg)    # type: ignore[assignment]
        logging.info("Дата зафиксирована как %s (%s)", self.base_date.to_datetime_string(), self.base_date.timezone_name)
        return self

    def __exit__(self, *args):
        if self._orig_today:
            pendulum.today = self._orig_today  # type: ignore[assignment]
        if self._orig_now:
            pendulum.now = self._orig_now      # type: ignore[assignment]
        return False


async def main() -> None:
    parser = argparse.ArgumentParser(description="Safe post builder for Cyprus VayboMeter")
    parser.add_argument("--mode", choices=["morning", "evening"], default=os.getenv("POST_MODE", "evening"))
    parser.add_argument("--date", default=os.getenv("WORK_DATE", ""))
    parser.add_argument("--for-tomorrow", action="store_true")
    parser.add_argument("--to-test", action="store_true")
    parser.add_argument("--chat-id", default="")
    parser.add_argument("--format-v2", action="store_true", help="Build scenario-style FORMAT_V2 text after legacy sanitizing.")
    parser.add_argument("--send", action="store_true", help="Actually send to CHANNEL_ID_TEST / --chat-id. Omit for dry-run.")
    parser.add_argument("--no-test-label", action="store_true", help="Do not prepend the 'Test safe post' label when sending.")
    args = parser.parse_args()

    mode = (args.mode or "evening").strip().lower()
    os.environ["POST_MODE"] = mode
    use_format_v2 = bool(args.format_v2 or _env_on("FORMAT_V2"))
    os.environ["FORMAT_V2"] = "1" if use_format_v2 else "0"

    tz = pendulum.timezone(TZ_STR)
    base_date = pendulum.parse(args.date).in_tz(tz) if args.date else pendulum.now(tz)
    if args.for_tomorrow:
        base_date = base_date.add(days=1)

    with _TodayPatch(base_date):
        raw_msg = build_message(
            region_name="Кипр",
            sea_label=SEA_LABEL,
            sea_cities=SEA_CITIES_ORDERED,
            other_label=OTHER_LABEL,
            other_cities=OTHER_CITIES_ALL,
            tz=TZ_STR,
            mode=mode,
        )

    legacy_result = sanitize_post_text(raw_msg)
    final_result = legacy_result
    final_label = "SAFE MESSAGE"

    if use_format_v2:
        from format_v2 import build_format_v2
        v2_raw = build_format_v2("Кипр", mode, legacy_result.text)
        v2_raw = _inject_morning_feels(v2_raw, mode)
        v2_raw = _inject_morning_best_window(v2_raw, mode)
        v2_raw = _inject_morning_score(v2_raw, mode)
        v2_raw = _inject_morning_smart_plan(v2_raw, mode)
        final_result = sanitize_post_text(v2_raw)
        final_label = "FORMAT_V2 MESSAGE"
        print("\n===== FORMAT_V2 RAW BEGIN =====\n")
        print(v2_raw)
        print("\n===== FORMAT_V2 RAW END =====\n")
        print("\n===== FORMAT_V2 SAFETY SUMMARY =====\n")
        print(validation_summary(final_result))

    chunks = split_telegram_text(final_result.text)

    print("\n===== RAW MESSAGE BEGIN =====\n")
    print(raw_msg)
    print("\n===== RAW MESSAGE END =====\n")
    print("\n===== LEGACY SAFETY SUMMARY =====\n")
    print(validation_summary(legacy_result))
    print(f"\n===== {final_label} BEGIN =====\n")
    print(final_result.text)
    print(f"\n===== {final_label} END =====\n")

    if not args.send:
        logging.info("SAFE DRY-RUN: отправка пропущена, format_v2=%s, chunks=%d", use_format_v2, len(chunks))
        return

    if not TOKEN:
        raise SystemExit("TELEGRAM_TOKEN не задан")
    chat_id = resolve_chat_id(args.chat_id, args.to_test)
    bot = Bot(token=TOKEN)
    for idx, chunk in enumerate(chunks, start=1):
        if args.no_test_label:
            text = chunk
        else:
            prefix = f"<b>Test safe post {idx}/{len(chunks)}</b>\n" if len(chunks) > 1 else "<b>Test safe post</b>\n"
            text = prefix + chunk
        await bot.send_message(
            chat_id=chat_id,
            text=text,
            parse_mode=constants.ParseMode.HTML,
            disable_web_page_preview=True,
        )
    logging.info("SAFE TEST sent: chat=%s chunks=%d format_v2=%s", chat_id, len(chunks), use_format_v2)


if __name__ == "__main__":
    asyncio.run(main())
