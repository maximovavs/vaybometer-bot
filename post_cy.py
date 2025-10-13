#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
post_cy.py  •  Запуск «Cyprus daily post» для Telegram-канала.

Режимы:
  1) Обычный ежедневный пост (по умолчанию) — вызывает post_common.main_common().
  2) --fx-only           — отправляет только блок «Курсы валют» (база EUR).
  3) --dry-run           — ничего не отправляет (полезно для теста workflow).
  4) --date YYYY-MM-DD   — дата для заголовков/FX (по умолчанию — сегодня в TZ).
  5) --for-tomorrow      — сдвиг даты +1 день (удобно для «поста на завтра»).
  6) --to-test           — публиковать в тестовый канал (CHANNEL_ID_TEST).
  7) --chat-id ID        — явный chat_id канала (перебивает всё остальное).

ENV:
  TELEGRAM_TOKEN, CHANNEL_ID, CHANNEL_ID_TEST, CHANNEL_ID_OVERRIDE,
  TZ (по умолчанию Asia/Nicosia)
"""

from __future__ import annotations

import os
import sys
import argparse
import asyncio
import logging
from typing import Dict, Any, Tuple, Optional
from pathlib import Path
import xml.etree.ElementTree as ET

import pendulum
import requests
from telegram import Bot, constants

from post_common import main_common  # основной сборщик сообщения

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

# ────────────────────────────── Secrets / Env ────────────────────────────────

TOKEN = os.getenv("TELEGRAM_TOKEN", "")
if not TOKEN:
    logging.error("Не задан TELEGRAM_TOKEN")
    sys.exit(1)

# ───────────────────────────── Параметры региона ────────────────────────────

SEA_LABEL   = "Морские города"
OTHER_LABEL = "Континентальные города"

TZ_STR = os.getenv("TZ", "Asia/Nicosia")

SEA_CITIES: Dict[str, Tuple[float, float]] = {
    "Limassol":  (34.707, 33.022),
    "Pafos":     (34.776, 32.424),
    "Ayia Napa": (34.988, 34.012),
    "Larnaca":   (34.916, 33.624),
}
SEA_CITIES_ORDERED = list(SEA_CITIES.items())

OTHER_CITIES_ALL: Dict[str, Tuple[float, float]] = {
    "Nicosia": (35.170, 33.360),
    "Troodos": (34.916, 32.823),
}

# ───────────────────────────── FX helpers (EUR base) ─────────────────────────

FX_CACHE_PATH = Path("fx_cache.json")  # кэш для анти-дубликата ЦБ (как было)

CODES = ("USD", "GBP", "TRY", "ILS")

def _to_float(x) -> Optional[float]:
    try:
        s = str(x).replace("−", "-").replace(",", ".").strip()
        return float(s)
    except Exception:
        return None

def _fmt_num(x: Optional[float], digits: int = 2) -> str:
    return f"{x:.{digits}f}" if isinstance(x, (int, float)) else "—"

def _fetch_intermarket_eur(symbols=CODES) -> Dict[str, Any]:
    """
    Межрынок (почти-реалтайм): exchangerate.host (без ключа).
    Возвращает {'USD': 1.08, ...}. При ошибке — {}.
    """
    try:
        r = requests.get(
            "https://api.exchangerate.host/latest",
            params={"base": "EUR", "symbols": ",".join(symbols)},
            timeout=12
        )
        r.raise_for_status()
        j = r.json() or {}
        rates = j.get("rates") or {}
        return {k: _to_float(v) for k, v in rates.items()}
    except Exception as e:
        logging.warning("Intermarket fetch failed: %s", e)
        return {}

def _fetch_ecb_official(symbols=CODES) -> Tuple[Dict[str, float], Optional[str]]:
    """
    Официальные курсы ЕЦБ — daily XML.
    Возвращает (rates, date_str).
    """
    try:
        r = requests.get(
            "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml",
            timeout=12,
            headers={"User-Agent": "VayboMeter/1.0"}
        )
        r.raise_for_status()
        root = ET.fromstring(r.text)
        # ищем <Cube time="YYYY-MM-DD"><Cube currency="USD" rate="1.08"/>...
        ns = {"gesmes": "http://www.gesmes.org/xml/2002-08-01", "def": "http://www.ecb.int/vocabulary/2002-08-01/eurofxref"}
        # в этом XML иногда без пространств имён, потому парсим «напрямую»
        time_node = None
        for cube in root.iter():
            if cube.attrib.get("time"):
                time_node = cube
                break
        rates: Dict[str, float] = {}
        if time_node is not None:
            for c in time_node:
                cur = c.attrib.get("currency")
                rate = _to_float(c.attrib.get("rate"))
                if cur in symbols and rate is not None:
                    rates[cur] = rate
            date_str = time_node.attrib.get("time")
        else:
            date_str = None
        return rates, date_str
    except Exception as e:
        logging.warning("ECB fetch failed: %s", e)
        return {}, None

def _normalize_cbr_date(raw) -> Optional[str]:
    if raw is None:
        return None
    try:
        if hasattr(raw, "to_date_string"):
            return raw.to_date_string()
        s = str(raw).strip()
        if "T" in s or " " in s:
            return pendulum.parse(s, tz="Europe/Moscow").to_date_string()
        pendulum.parse(s, tz="Europe/Moscow")
        return s
    except Exception:
        return None

def _load_cbr_rates(date_local: pendulum.DateTime, tz: pendulum.Timezone) -> Dict[str, Any]:
    """
    Подключаем локальный модуль fx.py (как было) — он даёт курсы ЦБ РФ в ₽.
    Ожидаем {'USD': {'value': ...}, 'EUR': {...}, 'as_of'/'cbr_date': ...}
    """
    try:
        import importlib
        fx = importlib.import_module("fx")
        rates = fx.get_rates(date=date_local, tz=tz)  # type: ignore[attr-defined]
        return rates or {}
    except Exception as e:
        logging.warning("FX (CBR) module not found/failed: %s", e)
        return {}

def _should_skip_by_cbr_cache(rates: Dict[str, Any]) -> bool:
    """
    Не публикуем повторно, если дата ЦБ та же (как раньше).
    """
    try:
        import importlib
        fx = importlib.import_module("fx")
        raw_date = rates.get("as_of") or rates.get("date") or rates.get("cbr_date")
        cbr_date = _normalize_cbr_date(raw_date)
        if cbr_date and hasattr(fx, "should_publish_again"):  # type: ignore[attr-defined]
            return not fx.should_publish_again(FX_CACHE_PATH, cbr_date)  # type: ignore[attr-defined]
    except Exception:
        pass
    return False

def _save_cbr_cache(rates: Dict[str, Any], text: str) -> None:
    try:
        import importlib
        fx = importlib.import_module("fx")
        raw_date = rates.get("as_of") or rates.get("date") or rates.get("cbr_date")
        cbr_date = _normalize_cbr_date(raw_date)
        if cbr_date and hasattr(fx, "save_fx_cache"):  # type: ignore[attr-defined]
            fx.save_fx_cache(FX_CACHE_PATH, cbr_date, text)  # type: ignore[attr-defined]
    except Exception as e:
        logging.warning("FX cache save failed: %s", e)

def _build_fx_message_eur(date_local: pendulum.DateTime, tz: pendulum.Timezone) -> Tuple[str, Dict[str, Any]]:
    """
    💱 Курсы валют (база EUR)
    • Межрынок: USD 1.08 • GBP 0.86 • TRY 37.25 • ILS 4.02
    • Официальные: ЕЦБ — USD 1.08 • GBP 0.86 • TRY 37.24 • ILS 4.01 • ЦБ РФ — €→₽ 102.30 • $→₽ 96.10

    Возвращает (text, cbr_rates) — второе нужно для анти-дубликата.
    """
    # 1) межрынок
    inter = _fetch_intermarket_eur()
    # 2) ЕЦБ
    ecb, _ = _fetch_ecb_official()
    # 3) ЦБ РФ
    cbr = _load_cbr_rates(date_local, tz)

    def _fmt_cross_line(prefix: str, mapping: Dict[str, float]) -> str:
        parts = []
        for code in CODES:
            parts.append(f"{code} {_fmt_num(mapping.get(code), 2)}")
        return f"{prefix}: " + " • ".join(parts)

    # если межрынок пуст, подставим ЕЦБ, чтобы строка не пропала
    if not inter and ecb:
        inter = dict(ecb)

    line1 = _fmt_cross_line("• Межрынок", inter) if inter else "• Межрынок: —"

    line2_left = _fmt_cross_line("ЕЦБ", ecb) if ecb else "ЕЦБ — н/д"
    eur_rub = _fmt_num(_to_float(((cbr.get("EUR") or {}).get("value"))), 2)
    usd_rub = _fmt_num(_to_float(((cbr.get("USD") or {}).get("value"))), 2)
    line2_right = f"ЦБ РФ — €→₽ {eur_rub} • $→₽ {usd_rub}"

    title = "💱 <b>Курсы валют (база EUR)</b>"
    body = f"{line1}\n• Официальные: {line2_left} • {line2_right}\n\n#Кипр #курсы_валют"
    return f"{title}\n{body}", cbr

# ───────────────────────────── Chat selection ────────────────────────────────

def resolve_chat_id(args_chat: str, to_test: bool) -> int:
    chat_override = (args_chat or "").strip() or os.getenv("CHANNEL_ID_OVERRIDE", "").strip()
    if chat_override:
        try:
            return int(chat_override)
        except Exception:
            logging.error("Неверный chat_id (override): %r", chat_override)
            sys.exit(1)

    if to_test:
        ch_test = os.getenv("CHANNEL_ID_TEST", "").strip()
        if not ch_test:
            logging.error("--to-test задан, но CHANNEL_ID_TEST не определён в окружении")
            sys.exit(1)
        try:
            return int(ch_test)
        except Exception:
            logging.error("CHANNEL_ID_TEST должен быть числом, получено: %r", ch_test)
            sys.exit(1)

    ch_main = os.getenv("CHANNEL_ID", "").strip() or os.getenv("CHANNEL_ID_KLG", "").strip()
    if not ch_main:
        logging.error("CHANNEL_ID не задан и не указан --chat-id/override")
        sys.exit(1)
    try:
        return int(ch_main)
    except Exception:
        logging.error("CHANNEL_ID должен быть числом, получено: %r", ch_main)
        sys.exit(1)

# ─────────────────────────── Патч даты для всего поста ──────────────────────

class _TodayPatch:
    """Контекстная подмена pendulum.today()/now() на заданную дату."""
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
        pendulum.now   = lambda tz_arg=None: _fake(self.base_date, tz_arg)  # type: ignore[assignment]
        logging.info("Дата для поста зафиксирована как %s (%s)",
                     self.base_date.to_datetime_string(), self.base_date.timezone_name)
        return self

    def __exit__(self, exc_type, exc, tb):
        if self._orig_today:
            pendulum.today = self._orig_today  # type: ignore[assignment]
        if self._orig_now:
            pendulum.now = self._orig_now      # type: ignore[assignment]
        return False

# ───────────────────────────────── Main ─────────────────────────────────────

async def _send_fx_only(
    bot: Bot,
    chat_id: int,
    date_local: pendulum.DateTime,
    tz: pendulum.Timezone,
    dry_run: bool
) -> None:
    text, cbr_rates = _build_fx_message_eur(date_local, tz)

    # анти-дубликат по дате ЦБ (если FX-пост только из-за расписания)
    try:
        if _should_skip_by_cbr_cache(cbr_rates):
            logging.info("Курсы ЦБ не обновились — FX-пост пропущен.")
            return
    except Exception:
        pass

    if dry_run:
        logging.info("DRY-RUN (fx-only):\n%s", text)
        return

    await bot.send_message(chat_id=chat_id, text=text, parse_mode=constants.ParseMode.HTML, disable_web_page_preview=True)
    _save_cbr_cache(cbr_rates, text)

async def main_cy() -> None:
    parser = argparse.ArgumentParser(description="Cyprus daily post runner")
    parser.add_argument("--date", type=str, default="", help="Дата YYYY-MM-DD (по умолчанию — сегодня в TZ)")
    parser.add_argument("--for-tomorrow", action="store_true", help="Использовать дату +1 день")
    parser.add_argument("--dry-run", action="store_true", help="Не отправлять сообщение, только лог")
    parser.add_argument("--fx-only", action="store_true", help="Отправить только блок «Курсы валют»")
    parser.add_argument("--to-test", action="store_true", help="Публиковать в тестовый канал (CHANNEL_ID_TEST)")
    parser.add_argument("--chat-id", type=str, default="", help="Явный chat_id канала (перебивает остальные)")
    args = parser.parse_args()

    tz = pendulum.timezone(TZ_STR)
    base_date = pendulum.parse(args.date).in_tz(tz) if args.date else pendulum.now(tz)
    if args.for_tomorrow:
        base_date = base_date.add(days=1)

    chat_id = resolve_chat_id(args.chat_id, args.to_test)
    bot = Bot(token=TOKEN)

    with _TodayPatch(base_date):
        if args.fx_only:
            await _send_fx_only(bot, chat_id, base_date, tz, dry_run=args.dry_run)
            return

        if args.dry_run:
            logging.info("DRY-RUN: пропускаем отправку основного ежедневного поста")
            return

        await main_common(
            bot=bot,
            chat_id=chat_id,
            region_name="Кипр",
            sea_label=SEA_LABEL,
            sea_cities=SEA_CITIES_ORDERED,
            other_label=OTHER_LABEL,
            other_cities=OTHER_CITIES_ALL,
            tz=TZ_STR,
        )

if __name__ == "__main__":
    asyncio.run(main_cy())
