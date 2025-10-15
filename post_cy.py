#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
post_cy.py  •  Cyprus daily/FX posts for Telegram.

Modes:
  --mode morning     -> утренний пост (сегодняшнее, компактные блоки)
  --mode evening     -> вечерний пост (анонс на завтра)
  --fx-only          -> публикует EUR-base FX пост (Межрынок • ЕЦБ • ЦБ РФ с динамикой)
  --dry-run          -> только лог

Также поддерживаются: --date, --for-tomorrow, --to-test, --chat-id
(режим можно задать через env POST_MODE=morning|evening; приоритет у CLI)
"""

from __future__ import annotations

import os
import sys
import argparse
import asyncio
import logging
from typing import Dict, Any, Tuple, Optional
from pathlib import Path
import json
import xml.etree.ElementTree as ET
import inspect

import pendulum
import requests
from telegram import Bot, constants

from post_common import main_common

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

# ─────────────── env ───────────────
TOKEN = os.getenv("TELEGRAM_TOKEN", "")
if not TOKEN:
    logging.error("Не задан TELEGRAM_TOKEN")
    sys.exit(1)

TZ_STR = os.getenv("TZ", "Asia/Nicosia")

SEA_LABEL   = "Морские города"
OTHER_LABEL = "Континентальные города"
SEA_CITIES = {
    "Limassol": (34.707, 33.022),
    "Pafos":    (34.776, 32.424),
    "Ayia Napa":(34.988, 34.012),
    "Larnaca":  (34.916, 33.624),
}
SEA_CITIES_ORDERED = list(SEA_CITIES.items())
OTHER_CITIES_ALL = {
    "Nicosia": (35.170, 33.360),
    "Troodos": (34.916, 32.823),
}

# ─────────────── FX helpers ───────────────
FX_CACHE_PATH     = Path("fx_cache.json")        # для «повтор/не повторять» по ЦБ
INTER_CACHE_PATH  = Path("fx_inter_cache.json")  # лёгкий кэш межрынка «вчера»

ECB_HEADERS = {
    "User-Agent": "VayboMeterBot/1.0 (+https://t.me/vaybometer)",
    "Accept": "application/xml,text/xml,application/json;q=0.9,*/*;q=0.8",
}

CODES = ("USD", "GBP", "TRY", "ILS")
NBSP = "\u00A0"

def _fmt_num(n: Optional[float], digits: int = 2) -> str:
    if n is None: return "н/д"
    try:
        s = f"{float(n):.{digits}f}"
        return s.rstrip("0").rstrip(".") if "." in s else s
    except Exception:
        return "н/д"

def _to_float(x) -> Optional[float]:
    try:
        return float(x)
    except Exception:
        return None

def _fmt_delta_arrow(d, digits: int = 2, eps: float = 0.005) -> str:
    """↑0.34 / ↓0.12; пусто, если близко к нулю."""
    try:
        x = float(d)
    except Exception:
        return ""
    if abs(x) < eps:
        return ""
    s = f"{abs(x):.{digits}f}".rstrip("0").rstrip(".")
    return f" ↑{s}" if x > 0 else f" ↓{s}"

def _fmt_delta_paren(d, digits: int = 2, eps: float = 0.005) -> str:
    """ (↑0.01) / (↓0.02) для межрынка/ЕЦБ."""
    a = _fmt_delta_arrow(d, digits=digits, eps=eps)
    return f"({a.strip()})" if a else ""

# —— кэш межрынка (вчера)
def _read_inter_cache() -> Tuple[Optional[str], Dict[str, float]]:
    try:
        if INTER_CACHE_PATH.exists():
            obj = json.loads(INTER_CACHE_PATH.read_text("utf-8"))
            if isinstance(obj, dict):
                return obj.get("date"), obj.get("values") or {}
    except Exception:
        pass
    return None, {}

def _save_inter_cache(date_str: str, values: Dict[str, float]) -> None:
    try:
        INTER_CACHE_PATH.write_text(json.dumps({"date": date_str, "values": values}, ensure_ascii=False), encoding="utf-8")
    except Exception as e:
        logging.warning("INTER cache save failed: %s", e)

# —— ЦБ РФ (через локальный модуль fx.py)
def _load_cbr_rates(date_local: pendulum.DateTime, tz: pendulum.Timezone) -> Dict[str, Any]:
    try:
        import importlib
        fx = importlib.import_module("fx")
        return fx.get_rates(date=date_local, tz=tz) or {}  # type: ignore[attr-defined]
    except Exception as e:
        logging.warning("FX: не удалось получить курсы ЦБ РФ: %s", e)
        return {}

# —— Межрынок EUR (с попыткой «вчера» через модуль fx или наш кэш)
def _fetch_intermarket_eur_with_prev(today_str: str) -> Tuple[Dict[str, float], Dict[str, float]]:
    today_vals: Dict[str,float] = {}
    prev_vals:  Dict[str,float] = {}

    # today
    try:
        import importlib
        fx = importlib.import_module("fx")
        if hasattr(fx, "get_intermarket_eur"):  # type: ignore[attr-defined]
            v = fx.get_intermarket_eur()  # type: ignore[attr-defined]
            if isinstance(v, dict):
                today_vals = {k: float(vv) for k,vv in v.items() if k in CODES and _to_float(vv) is not None}
    except Exception as e:
        logging.warning("FX: межрынок EUR сегодня не получен: %s", e)

    # prev: 1) явная функция; 2) get_intermarket_eur(date=...); 3) наш кэш
    if not prev_vals:
        try:
            import importlib, inspect as _inspect
            fx = importlib.import_module("fx")
            if hasattr(fx, "get_intermarket_eur_prev"):  # type: ignore[attr-defined]
                pv = fx.get_intermarket_eur_prev()  # type: ignore[attr-defined]
                if isinstance(pv, dict):
                    prev_vals = {k: float(vv) for k,vv in pv.items() if k in CODES and _to_float(vv) is not None}
            else:
                fn = getattr(fx, "get_intermarket_eur", None)
                if fn and "date" in (_inspect.signature(fn).parameters if callable(fn) else {}):
                    yday = (pendulum.parse(today_str).subtract(days=1)).to_date_string()
                    pv = fn(date=yday)  # type: ignore[misc]
                    if isinstance(pv, dict):
                        prev_vals = {k: float(vv) for k,vv in pv.items() if k in CODES and _to_float(vv) is not None}
        except Exception:
            pass

    if not prev_vals:
        cached_date, cached = _read_inter_cache()
        if cached_date and cached and cached_date != today_str:
            prev_vals = {k: float(vv) for k,vv in cached.items() if k in CODES and _to_float(vv) is not None}

    return today_vals, prev_vals

# —— ЕЦБ (официальные курсы к EUR) + предыдущий день из hist-90d
def _fetch_ecb_latest_and_prev() -> Tuple[Dict[str,float], Dict[str,float], Optional[str], Optional[str]]:
    urls = [
        "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml",
        "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-hist-90d.xml",
    ]
    want = set(CODES)

    latest: Dict[str,float] = {}
    prev:   Dict[str,float] = {}
    d_latest = d_prev = None

    # daily
    ok_latest = False
    try:
        r = requests.get(urls[0], headers=ECB_HEADERS, timeout=12)
        r.raise_for_status()
        root = ET.fromstring(r.content)
        cubes = root.findall(".//{*}Cube[@time]")
        if cubes:
            c = cubes[-1]; d_latest = c.attrib.get("time")
            for cc in c.findall("{*}Cube"):
                code = cc.attrib.get("currency"); rate = cc.attrib.get("rate")
                if code in want and rate:
                    v = _to_float(rate)
                    if v is not None:
                        latest[code] = v
            ok_latest = bool(latest)
    except Exception:
        pass

    # hist-90d
    try:
        r = requests.get(urls[1], headers=ECB_HEADERS, timeout=15)
        r.raise_for_status()
        root = ET.fromstring(r.content)
        cubes = root.findall(".//{*}Cube[@time]")
        if not cubes:
            return latest, prev, d_latest, d_prev
        c2 = cubes[-2] if len(cubes) >= 2 else None
        if not ok_latest:
            c1 = cubes[-1]
            d_latest = c1.attrib.get("time")
            latest = {}
            for cc in c1.findall("{*}Cube"):
                code = cc.attrib.get("currency"); rate = cc.attrib.get("rate")
                if code in want and rate:
                    v = _to_float(rate);  latest[code] = v if v is not None else latest.get(code)
        if c2 is not None:
            d_prev = c2.attrib.get("time")
            for cc in c2.findall("{*}Cube"):
                code = cc.attrib.get("currency"); rate = cc.attrib.get("rate")
                if code in want and rate:
                    v = _to_float(rate)
                    if v is not None:
                        prev[code] = v
    except Exception:
        pass

    return latest, prev, d_latest, d_prev

# —— сборка FX-поста
def _build_fx_message_eur(date_local: pendulum.DateTime, tz: pendulum.Timezone) -> tuple[str, Dict[str, Any], Dict[str, float]]:
    today_str = date_local.to_date_string()

    # Межрынок (с prev если сможем)
    inter_today, inter_prev = _fetch_intermarket_eur_with_prev(today_str)

    def _line_cross_with_delta(prefix: str, cur: Dict[str,float], prev: Dict[str,float]) -> str:
        if not cur:
            return ""
        parts = []
        for code in CODES:
            v = _to_float(cur.get(code))
            if v is None:
                continue
            delta = None
            if prev and code in prev and _to_float(prev.get(code)) is not None:
                delta = v - float(prev[code])  # натуральная дельта
            piece = f"{code} {_fmt_num(v, 2)}"
            d_piece = _fmt_delta_paren(delta, digits=2, eps=0.005) if delta is not None else ""
            parts.append(piece + (f" {d_piece}" if d_piece else ""))
        return f"{prefix} " + " • ".join(parts) if parts else ""

    line_inter = _line_cross_with_delta("• Межрынок:", inter_today, inter_prev)

    # ЕЦБ (последний и предыдущий рабочий день)
    ecb_latest, ecb_prev, d_latest, _d_prev = _fetch_ecb_latest_and_prev()
    line_ecb  = _line_cross_with_delta("• ЕЦБ:", ecb_latest, ecb_prev)

    # ЦБ РФ (к рублю + дельта)
    cbr = _load_cbr_rates(date_local, tz)
    eur_val = _to_float(((cbr.get("EUR") or {}).get("value")))
    eur_dlt = _to_float(((cbr.get("EUR") or {}).get("delta")))
    usd_val = _to_float(((cbr.get("USD") or {}).get("value")))
    usd_dlt = _to_float(((cbr.get("USD") or {}).get("delta")))

    cbr_bits = []
    if eur_val is not None:
        cbr_bits.append(f"€→₽{NBSP}{_fmt_num(eur_val, 2)}{_fmt_delta_arrow(eur_dlt)}")
    if usd_val is not None:
        cbr_bits.append(f"$→₽{NBSP}{_fmt_num(usd_val, 2)}{_fmt_delta_arrow(usd_dlt)}")
    line_cbr = "• ЦБ РФ: " + " • ".join(cbr_bits) if cbr_bits else ""

    title = "💱 <b>Курсы валют (база EUR)</b>"
    lines = [l for l in (line_inter, line_ecb, line_cbr) if l]
    if not lines:
        lines = ["• Данные временно недоступны"]

    text = f"{title}\n" + "\n".join(lines) + "\n\n#Кипр #курсы_валют"

    # для кэша межрынка сохраняем «сегодня»
    return text, cbr, inter_today

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
    text, rates, inter_today = _build_fx_message_eur(date_local, tz)
    raw_date = rates.get("as_of") or rates.get("date") or rates.get("cbr_date")
    cbr_date = _normalize_cbr_date(raw_date)

    # не дублируем, если дата ЦБ не менялась (используем функции модуля fx, если есть)
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

    # сохраним межрынок «сегодня» как «вчера» для следующего раза
    try:
        _save_inter_cache(date_local.to_date_string(), inter_today)
    except Exception:
        pass

    # сохраняем кеш ЦБ через модуль fx (если он его ведёт)
    try:
        import importlib
        fx = importlib.import_module("fx")
        if cbr_date and hasattr(fx, "save_fx_cache"):  # type: ignore[attr-defined]
            fx.save_fx_cache(FX_CACHE_PATH, cbr_date, text)  # type: ignore[attr-defined]
    except Exception as e:
        logging.warning("FX: save cache failed: %s", e)

# ─────────────── chat id resolve ───────────────
def resolve_chat_id(args_chat: str, to_test: bool) -> int:
    chat_override = (args_chat or "").strip() or os.getenv("CHANNEL_ID_OVERRIDE", "").strip()
    if chat_override:
        try: return int(chat_override)
        except Exception:
            logging.error("Неверный chat_id (override): %r", chat_override); sys.exit(1)

    if to_test:
        ch_test = os.getenv("CHANNEL_ID_TEST", "").strip()
        if not ch_test:
            logging.error("--to-test задан, но CHANNEL_ID_TEST не определён"); sys.exit(1)
        try: return int(ch_test)
        except Exception:
            logging.error("CHANNEL_ID_TEST должен быть числом, получено: %r", ch_test); sys.exit(1)

    ch_main = os.getenv("CHANNEL_ID", "").strip() or os.getenv("CHANNEL_ID_KLG", "").strip()
    if not ch_main:
        logging.error("CHANNEL_ID не задан и не указан --chat-id/override"); sys.exit(1)
    try: return int(ch_main)
    except Exception:
        logging.error("CHANNEL_ID должен быть числом, получено: %r", ch_main); sys.exit(1)

# ─────────────── pendulum date patch ───────────────
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
        pendulum.now   = lambda tz_arg=None: _fake(self.base_date, tz_arg)  # type: ignore[assignment]
        logging.info("Дата зафиксирована как %s (TZ %s)", self.base_date.to_datetime_string(), self.base_date.timezone_name)
        return self
    def __exit__(self, *a):
        if self._orig_today: pendulum.today = self._orig_today  # type: ignore[assignment]
        if self._orig_now:   pendulum.now   = self._orig_now    # type: ignore[assignment]
        return False

# ─────────────── main ───────────────
async def main_cy() -> None:
    parser = argparse.ArgumentParser(description="Cyprus daily post runner")
    parser.add_argument("--date", type=str, default="")
    parser.add_argument("--for-tomorrow", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--fx-only", action="store_true")
    parser.add_argument("--to-test", action="store_true")
    parser.add_argument("--chat-id", type=str, default="")
    parser.add_argument("--mode", choices=["morning", "evening"], help="Режим ленты (по умолчанию POST_MODE или evening)")
    args = parser.parse_args()

    tz = pendulum.timezone(TZ_STR)
    base_date = pendulum.parse(args.date).in_tz(tz) if args.date else pendulum.now(tz)
    if args.for_tomorrow:
        base_date = base_date.add(days=1)

    # Определяем режим: CLI > ENV > default
    mode = args.mode or os.getenv("POST_MODE", "").strip().lower()
    if mode not in ("morning", "evening"):
        mode = "evening"  # дефолт совместим с прежним «вечерним» форматом
    # Пробрасываем в окружение (на случай, если post_common читает из ENV)
    os.environ["POST_MODE"] = mode

    chat_id = resolve_chat_id(args.chat_id, args.to_test)
    bot = Bot(token=TOKEN)

    with _TodayPatch(base_date):
        if args.fx_only:
            await _send_fx_eur_only(bot, chat_id, base_date, tz, dry_run=args.dry_run)
            return

        if args.dry_run:
            logging.info("DRY-RUN: пропускаем отправку основного поста (%s)", mode)
            return

        # Безопасная прокидка mode в main_common: если сигнатура поддерживает, передадим, иначе — нет.
        kwargs = dict(
            bot=bot,
            chat_id=chat_id,
            region_name="Кипр",
            sea_label=SEA_LABEL,
            sea_cities=SEA_CITIES_ORDERED,
            other_label=OTHER_LABEL,
            other_cities=OTHER_CITIES_ALL,
            tz=TZ_STR,
        )

        try:
            sig = inspect.signature(main_common)  # type: ignore[arg-type]
            if "mode" in sig.parameters:
                kwargs["mode"] = mode  # type: ignore[assignment]
        except Exception:
            pass

        await main_common(**kwargs)  # type: ignore[misc]

if __name__ == "__main__":
    asyncio.run(main_cy())
