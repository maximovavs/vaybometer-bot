#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Build and optionally send a sanitized VayboMeter post to the test channel.

This runner is deliberately separate from production scripts. It lets us test
format and validators without changing the scheduled production workflow.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from typing import Union

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
    parser = argparse.ArgumentParser(description="Safe test post builder for Cyprus VayboMeter")
    parser.add_argument("--mode", choices=["morning", "evening"], default=os.getenv("POST_MODE", "evening"))
    parser.add_argument("--date", default=os.getenv("WORK_DATE", ""))
    parser.add_argument("--for-tomorrow", action="store_true")
    parser.add_argument("--to-test", action="store_true")
    parser.add_argument("--chat-id", default="")
    parser.add_argument("--send", action="store_true", help="Actually send to CHANNEL_ID_TEST / --chat-id. Omit for dry-run.")
    args = parser.parse_args()

    mode = (args.mode or "evening").strip().lower()
    os.environ["POST_MODE"] = mode
    os.environ.setdefault("FORMAT_V2", "0")

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

    result = sanitize_post_text(raw_msg)
    chunks = split_telegram_text(result.text)

    print("\n===== RAW MESSAGE BEGIN =====\n")
    print(raw_msg)
    print("\n===== RAW MESSAGE END =====\n")
    print("\n===== SAFETY SUMMARY =====\n")
    print(validation_summary(result))
    print("\n===== SAFE MESSAGE BEGIN =====\n")
    print(result.text)
    print("\n===== SAFE MESSAGE END =====\n")

    if not args.send:
        logging.info("SAFE DRY-RUN: отправка пропущена, chunks=%d", len(chunks))
        return

    if not TOKEN:
        raise SystemExit("TELEGRAM_TOKEN не задан")
    chat_id = resolve_chat_id(args.chat_id, args.to_test)
    bot = Bot(token=TOKEN)
    for idx, chunk in enumerate(chunks, start=1):
        prefix = f"<b>Test safe post {idx}/{len(chunks)}</b>\n" if len(chunks) > 1 else "<b>Test safe post</b>\n"
        await bot.send_message(
            chat_id=chat_id,
            text=prefix + chunk,
            parse_mode=constants.ParseMode.HTML,
            disable_web_page_preview=True,
        )
    logging.info("SAFE TEST sent: chat=%s chunks=%d", chat_id, len(chunks))


if __name__ == "__main__":
    asyncio.run(main())
