#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
post_cy.py  •  Запуск «Cyprus daily post» для Telegram-канала.

Режимы:
  1) Обычный ежедневный пост (по умолчанию) — вызывает post_common.main_common().
  2) --fx-only           — отправляет только FX-пост (база EUR: межрынок + ЕЦБ + ЦБ РФ с динамикой).
  3) --dry-run           — ничего не отправляет (логирует текст).
  4) --date YYYY-MM-DD   — дата для заголовков/FX (по умолчанию — сейчас в TZ).
  5) --for-tomorrow      — сдвиг даты +1 день (удобно для «поста на завтра»).
  6) --to-test           — публиковать в тестовый канал (CHANNEL_ID_TEST).
  7) --chat-id ID        — явный chat_id канала (перебивает всё остальное).

ENV:
  TELEGRAM_TOKEN         — обязательно.
  CHANNEL_ID             — ID основного канала.
  CHANNEL_ID_TEST        — ID тестового канала (для --to-test).
  CHANNEL_ID_OVERRIDE    — явный chat_id (перебивает всё).
  TZ                     — таймзона (по умолчанию Asia/Nicosia).
  DISABLE_LLM_DAILY      — проксируется в post_common.
"""

from __future__ import annotations

import os
import sys
import argparse
import asyncio
import logging
from typing import Dict, Any, Tuple, Optional
from pathlib import Path

import pendulum
from telegram import Bot, constants

from post_common import main_common  # основной сборщик ежедневного сообщения

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

# ────────────────────────────── Secrets / Env ────────────────────────────────

TOKEN = os.getenv("TELEGRAM_TOKEN", "")
if not TOKEN:
    logging.error("Не задан TELEGRAM_TOKEN")
    sys.exit(1)

# ───────────────────────────── Параметры региона ────────────────────────────

SEA_LABEL   = "Морские города"
OTHER_LABEL = "Континентальные города"

# Часовой пояс — Кипр (можно переопределить переменной TZ)
TZ_STR = os.getenv("TZ", "Asia/Nicosia")

SEA_CITIES: Dict[str, Tuple[float, float]] = {
    "Limassol": (34.707, 33.022),
    "Pafos": (34.776, 32.424),
    "Ayia Napa": (34.988, 34.012),
    "Larnaca": (34.916, 33.624),
}
SEA_CITIES_ORDERED = list(SEA_CITIES.items())

OTHER_CITIES_ALL: Dict[str, Tuple[float, float]] = {
    "Nicosia": (35.170, 33.360),
    "Troodos": (34.916, 32.823),
}

# ───────────────────────────── FX helpers ────────────────────────────────────

FX_CACHE_PATH = Path("fx_cache.json")  # используется для «не дублировать, если ЦБ не обновился»

def _fmt_num(n: Optional[float], digits: int = 2) -> str:
    if n is None:
        return "—"
    try:
        s = f"{float(n):.{digits}f}"
        return s.rstrip("0").rstrip(".") if "." in s else s
    except Exception:
        return "—"

def _to_float(x) -> Optional[float]:
    try:
        return float(x)
    except Exception:
        return None

def _fmt_delta_arrow(d, digits: int = 2, eps: float = 0.005) -> str:
    """
    Компактная динамика: ↑0.34 / ↓0.12. Если почти ноль — пустая строка.
    Используем только для ЦБ РФ, чтобы не раздувать текст.
    """
    try:
        x = float(d)
    except Exception:
        return ""
    if abs(x) < eps:
        return ""
    s = f"{abs(x):.{digits}f}".rstrip("0").rstrip(".")
    return f" ↑{s}" if x > 0 else f" ↓{s}"

def _load_cbr_rates(date_local: pendulum.DateTime, tz: pendulum.Timezone) -> Dict[str, Any]:
    """
    Ожидаем fx.get_rates(date=..., tz=...) -> {'USD': {'value':..., 'delta':...}, 'EUR': {...}, 'as_of': ...}
    Возвращаем {} при ошибке.
    """
    try:
        import importlib
        fx = importlib.import_module("fx")
        return fx.get_rates(date=date_local, tz=tz) or {}  # type: ignore[attr-defined]
    except Exception as e:
        logging.warning("FX: не удалось получить курсы ЦБ РФ: %s", e)
        return {}

def _fetch_intermarket_eur() -> Dict[str, float]:
    """
    Межрыночные кроссы к EUR: USD, GBP, TRY, ILS.
    Ожидаем fx.get_intermarket_eur() -> dict(code->float). Возвращаем {} при ошибке.
    """
    try:
        import importlib
        fx = importlib.import_module("fx")
        if hasattr(fx, "get_intermarket_eur"):  # type: ignore[attr-defined]
            data = fx.get_intermarket_eur()  # type: ignore[attr-defined]
            return data or {}
    except Exception as e:
        logging.warning("FX: межрынок EUR не получен: %s", e)
    return {}

def _fetch_ecb_official() -> Tuple[Dict[str, float], Optional[str]]:
    """
    Официальные курсы ЕЦБ к EUR: USD, GBP, TRY, ILS.
    Ожидаем fx.get_ecb_eur_rates() -> (dict, as_of) ИЛИ fx.get_ecb_official() -> dict.
    """
    try:
        import importlib
        fx = importlib.import_module("fx")
        if hasattr(fx, "get_ecb_eur_rates"):  # type: ignore[attr-defined]
            d, as_of = fx.get_ecb_eur_rates()  # type: ignore[attr-defined]
            return (d or {}), (str(as_of) if as_of else None)
        if hasattr(fx, "get_ecb_official"):  # type: ignore[attr-defined]
            d = fx.get_ecb_official()  # type: ignore[attr-defined]
            return (d or {}), None
    except Exception as e:
        logging.warning("FX: ЕЦБ-курсы не получены: %s", e)
    return {}, None

def _build_fx_message_eur(date_local: pendulum.DateTime, tz: pendulum.Timezone):
    """
    Двухстрочный FX-пост (EUR-база).
      • Межрынок: USD 1.16 • GBP 0.87 • TRY 48.36 • ILS 3.80
      • Официальные: ЕЦБ — USD 1.16 • GBP 0.87 • TRY 48.36 • ILS 3.80 • ЦБ РФ — €→₽ 93.92 ↓0.13 • $→₽ 80.85 ↑0.07
    Если межрынок/ЕЦБ пусты — соответствующие куски скрываются.
    """
    NBSP = "\u00A0"  # неразрывный пробел (чтобы не ломало строку после «€→₽»)

    inter = _fetch_intermarket_eur()
    ecb, _asof = _fetch_ecb_official()
    cbr = _load_cbr_rates(date_local, tz)

    # если межрынок пуст — пробуем показать хотя бы ЕЦБ в первой строке
    if not inter and ecb:
        inter = dict(ecb)

    def _line_cross(prefix: str, data: Dict[str, float]) -> str:
        if not data:
            return ""
        parts = []
        for code in ("USD", "GBP", "TRY", "ILS"):
            v = _to_float(data.get(code))
            if v is not None:
                parts.append(f"{code} {_fmt_num(v, 2)}")
        return f"{prefix} " + " • ".join(parts) if parts else ""

    line1 = _line_cross("• Межрынок:", inter)         # может быть пустой
    line_ecb = _line_cross("ЕЦБ —", ecb)              # может быть пустой

    # ЦБ РФ (всегда показываем, если есть хотя бы одно значение)
    eur_val = _to_float(((cbr.get("EUR") or {}).get("value")))
    usd_val = _to_float(((cbr.get("USD") or {}).get("value")))
    eur_dlt = _to_float(((cbr.get("EUR") or {}).get("delta")))
    usd_dlt = _to_float(((cbr.get("USD") or {}).get("delta")))

    cbr_bits = []
    if eur_val is not None:
        cbr_bits.append(f"€→₽{NBSP}{_fmt_num(eur_val, 2)}{_fmt_delta_arrow(eur_dlt)}")
    if usd_val is not None:
        cbr_bits.append(f"$→₽{NBSP}{_fmt_num(usd_val, 2)}{_fmt_delta_arrow(usd_dlt)}")

    cbr_line = f"ЦБ РФ — " + " • ".join(cbr_bits) if cbr_bits else ""

    # Официальные: ЕЦБ (если есть) + ЦБ РФ (если есть)
    official_parts = []
    if line_ecb:
        official_parts.append(line_ecb)
    if cbr_line:
        official_parts.append(cbr_line)

    line2 = "• Официальные: " + " • ".join(official_parts) if official_parts else ""

    # Сборка финального текста (пропускаем пустые строки)
    lines = []
    if line1:
        lines.append(line1)
    if line2:
        lines.append(line2)

    title = "💱 <b>Курсы валют (база EUR)</b>"
    body = ("\n".join(lines) if lines else "• Данные временно недоступны") + "\n\n#Кипр #курсы_валют"
    return f"{title}\n{body}", cbr

def _normalize_cbr_date(raw) -> Optional[str]:
    if raw is None:
        return None
    if hasattr(raw, "to_date_string"):
        try:
            return raw.to_date_string()  # type: ignore[call-arg]
        except Exception:
            pass
    if isinstance(raw, (int, float)):
        try:
            return pendulum.from_timestamp(int(raw), tz="Europe/Moscow").to_date_string()
        except Exception:
            return None
    try:
        s = str(raw).strip()
        if "T" in s or " " in s:
            return pendulum.parse(s, tz="Europe/Moscow").to_date_string()
        pendulum.parse(s, tz="Europe/Moscow")
        return s
    except Exception:
        return None

async def _send_fx_eur_only(
    bot: Bot,
    chat_id: int,
    date_local: pendulum.DateTime,
    tz: pendulum.Timezone,
    dry_run: bool
) -> None:
    text, rates = _build_fx_message_eur(date_local, tz)
    raw_date = rates.get("as_of") or rates.get("date") or rates.get("cbr_date")
    cbr_date = _normalize_cbr_date(raw_date)

    # не постим повтор, если «дата ЦБ» та же (используем функции из fx.py, если есть)
    try:
        import importlib
        fx = importlib.import_module("fx")
        if cbr_date and hasattr(fx, "should_publish_again"):  # type: ignore[attr-defined]
            should = fx.should_publish_again(FX_CACHE_PATH, cbr_date)  # type: ignore[attr-defined]
            if not should:
                logging.info("FX: курсы ЦБ РФ не обновились — пост пропущен.")
                return
    except Exception as e:
        logging.warning("FX: skip-check не сработал, продолжаем: %s", e)

    if dry_run:
        logging.info("DRY-RUN (fx-only):\n%s", text)
        return

    await bot.send_message(chat_id=chat_id, text=text, parse_mode=constants.ParseMode.HTML, disable_web_page_preview=True)

    try:
        import importlib
        fx = importlib.import_module("fx")
        if cbr_date and hasattr(fx, "save_fx_cache"):  # type: ignore[attr-defined]
            fx.save_fx_cache(FX_CACHE_PATH, cbr_date, text)  # type: ignore[attr-defined]
    except Exception as e:
        logging.warning("FX: save cache failed: %s", e)

# ───────────────────────────── Chat selection ────────────────────────────────

def resolve_chat_id(args_chat: str, to_test: bool) -> int:
    """
    Приоритеты:
      1) --chat-id / CHANNEL_ID_OVERRIDE
      2) --to-test  → CHANNEL_ID_TEST
      3) CHANNEL_ID (основной)
      4) (совм.) CHANNEL_ID_KLG
    """
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
            logging.error("--to-test задан, но CHANNEL_ID_TEST не определён")
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
    """Контекстный менеджер для временной подмены `pendulum.today()` и `pendulum.now()`."""
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

        logging.info(
            "Дата для поста зафиксирована как %s (TZ %s)",
            self.base_date.to_datetime_string(),
            self.base_date.timezone_name,
        )
        return self

    def __exit__(self, exc_type, exc, tb):
        if self._orig_today:
            pendulum.today = self._orig_today  # type: ignore[assignment]
        if self._orig_now:
            pendulum.now = self._orig_now  # type: ignore[assignment]
        return False

# ───────────────────────────────── Main ─────────────────────────────────────

async def main_cy() -> None:
    parser = argparse.ArgumentParser(description="Cyprus daily post runner")
    parser.add_argument("--date", type=str, default="", help="Дата в формате YYYY-MM-DD (по умолчанию — сегодня в TZ)")
    parser.add_argument("--for-tomorrow", action="store_true", help="Использовать дату +1 день")
    parser.add_argument("--dry-run", action="store_true", help="Не отправлять сообщение, только лог")
    parser.add_argument("--fx-only", action="store_true", help="Отправить только FX-пост (EUR-база)")
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
            await _send_fx_eur_only(bot, chat_id, base_date, tz, dry_run=args.dry_run)
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
            tz=TZ_STR,  # post_common сам приведёт к pendulum.timezone
        )

if __name__ == "__main__":
    asyncio.run(main_cy())
