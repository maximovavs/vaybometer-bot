#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Production runner for the separate Cyprus daytime FX post with Market Pulse.

This is deliberately separate from morning/evening weather posts.
It keeps the existing FX anti-duplicate/cache behavior and appends BTC/ETH/Gold.
"""
from __future__ import annotations

import argparse
import asyncio
import importlib
import json
import logging
import os
from typing import Any

import pendulum
import requests
from telegram import Bot, constants

from post_cy import (
    TOKEN,
    TZ_STR,
    _build_fx_message_eur,
    _compute_fx_force,
    _fx_cache_paths,
    _normalize_cbr_date,
    _save_inter_cache,
    resolve_chat_id,
)

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")


def _to_float(x: Any) -> float | None:
    try:
        return float(x)
    except Exception:
        return None


def _fmt_usd_compact(value: float | None) -> str:
    if value is None:
        return "н/д"
    if abs(value) >= 1000:
        return f"${value / 1000:.1f}K"
    if abs(value) >= 100:
        return f"${value:.0f}"
    return f"${value:.2f}"


def _fmt_pct(value: float | None) -> str:
    if value is None:
        return ""
    sign = "+" if value >= 0 else "−"
    return f" ({sign}{abs(value):.1f}% 24ч)"


def _fetch_crypto() -> list[str]:
    try:
        r = requests.get(
            "https://api.coingecko.com/api/v3/simple/price",
            params={
                "ids": "bitcoin,ethereum",
                "vs_currencies": "usd",
                "include_24hr_change": "true",
            },
            timeout=10,
            headers={"User-Agent": "VayboMeter/1.0"},
        )
        r.raise_for_status()
        data = r.json() or {}
    except Exception as e:
        logging.warning("Market Pulse: crypto unavailable: %s", e)
        return []

    out: list[str] = []
    btc = data.get("bitcoin") or {}
    eth = data.get("ethereum") or {}
    btc_usd = _to_float(btc.get("usd"))
    eth_usd = _to_float(eth.get("usd"))
    if btc_usd is not None:
        out.append(f"₿ BTC {_fmt_usd_compact(btc_usd)}{_fmt_pct(_to_float(btc.get('usd_24h_change')))}")
    if eth_usd is not None:
        out.append(f"Ξ ETH {_fmt_usd_compact(eth_usd)}{_fmt_pct(_to_float(eth.get('usd_24h_change')))}")
    return out


def _fetch_gold_from_stooq(symbol: str) -> float | None:
    try:
        r = requests.get(
            "https://stooq.com/q/l/",
            params={"s": symbol, "f": "sd2t2ohlcv", "h": "", "e": "csv"},
            timeout=10,
            headers={"User-Agent": "VayboMeter/1.0"},
        )
        r.raise_for_status()
        lines = [x.strip() for x in r.text.splitlines() if x.strip()]
        if len(lines) < 2:
            return None
        parts = lines[-1].split(",")
        if len(parts) <= 6 or parts[6].upper() == "N/D":
            return None
        return _to_float(parts[6])
    except Exception:
        return None


def _fetch_gold_from_yahoo(symbol: str) -> float | None:
    try:
        r = requests.get(
            f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}",
            params={"range": "1d", "interval": "1d"},
            timeout=10,
            headers={"User-Agent": "VayboMeter/1.0"},
        )
        r.raise_for_status()
        result = (((r.json() or {}).get("chart") or {}).get("result") or [None])[0] or {}
        meta = result.get("meta") or {}
        price = _to_float(meta.get("regularMarketPrice"))
        if price is not None:
            return price
        quote = (((result.get("indicators") or {}).get("quote") or [None])[0] or {})
        for raw in reversed(quote.get("close") or []):
            price = _to_float(raw)
            if price is not None:
                return price
    except Exception:
        return None
    return None


def _fetch_gold() -> list[str]:
    price = None
    for symbol in ("xauusd", "gc.f"):
        price = _fetch_gold_from_stooq(symbol)
        if price is not None:
            break
    if price is None:
        for symbol in ("GC=F", "XAUUSD=X"):
            price = _fetch_gold_from_yahoo(symbol)
            if price is not None:
                break
    if price is not None:
        return [f"🥇 Gold {_fmt_usd_compact(price)}"]
    return ["🥇 Gold н/д"]


def build_market_pulse_block() -> str:
    items = _fetch_crypto() + _fetch_gold()
    if not items:
        return ""
    return "📊 <b>Market Pulse</b>\n• " + "\n• ".join(items) + "\n<i>Пульс рынка, не инвестсовет.</i>"


def inject_market_pulse(fx_text: str, block: str) -> str:
    if not block or "<b>Market Pulse</b>" in fx_text:
        return fx_text
    marker = "\n\n#"
    if marker in fx_text:
        return fx_text.replace(marker, "\n\n" + block + marker, 1)
    return fx_text.rstrip() + "\n\n" + block


def _should_publish(fx_cache_path, cbr_date: str, force_publish: bool) -> bool:
    if not cbr_date:
        logging.info("FX: cbr_date не определена — антидубль по ЦБ пропущен.")
        return True

    should = None
    try:
        fx = importlib.import_module("fx")
        if hasattr(fx, "should_publish_again"):
            should = fx.should_publish_again(fx_cache_path, cbr_date)  # type: ignore[attr-defined]
    except Exception as e:
        logging.warning("FX: skip-check через fx.py не сработал: %s", e)
        should = None

    if should is None:
        try:
            if not fx_cache_path.exists():
                should = True
            else:
                obj = json.loads(fx_cache_path.read_text("utf-8"))
                last = (obj or {}).get("last_cbr_date") or (obj or {}).get("cbr_date") or (obj or {}).get("date")
                should = str(last or "").strip() != str(cbr_date).strip()
        except Exception:
            should = True

    if not should and not force_publish:
        logging.info("FX: курсы ЦБ РФ не обновились — пост пропущен.")
        return False
    if not should and force_publish:
        logging.info("FX: ЦБ РФ не обновился, но force_publish=1 — публикуем.")
    return True


def _save_caches(fx_cache_path, inter_cache_path, cbr_date: str | None, text: str, date_local, inter_today) -> None:
    try:
        _save_inter_cache(inter_cache_path, date_local.to_date_string(), inter_today)
    except Exception as e:
        logging.warning("FX: save inter cache failed: %s", e)

    try:
        fx = importlib.import_module("fx")
        if cbr_date and hasattr(fx, "save_fx_cache"):
            fx.save_fx_cache(fx_cache_path, cbr_date, text)  # type: ignore[attr-defined]
        else:
            fx_cache_path.parent.mkdir(parents=True, exist_ok=True)
            fx_cache_path.write_text(json.dumps({"cbr_date": cbr_date, "text": text}, ensure_ascii=False), "utf-8")
    except Exception as e:
        logging.warning("FX: save cache failed: %s", e)


async def main() -> None:
    parser = argparse.ArgumentParser(description="Cyprus separate FX post with Market Pulse")
    parser.add_argument("--date", default="")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--to-test", action="store_true")
    parser.add_argument("--chat-id", default="")
    args = parser.parse_args()

    if not TOKEN:
        raise SystemExit("TELEGRAM_TOKEN is not set")

    tz = pendulum.timezone(TZ_STR)
    date_local = pendulum.parse(args.date).in_tz(tz) if args.date else pendulum.now(tz)
    fx_cache_path, inter_cache_path = _fx_cache_paths(args.to_test)

    fx_text, rates, inter_today = _build_fx_message_eur(date_local, tz, inter_cache_path)
    text = inject_market_pulse(fx_text, build_market_pulse_block())
    raw_date = rates.get("as_of") or rates.get("date") or rates.get("cbr_date")
    cbr_date = _normalize_cbr_date(raw_date)
    force_publish = _compute_fx_force(args.to_test)

    if not _should_publish(fx_cache_path, str(cbr_date or ""), force_publish):
        return

    if args.dry_run:
        logging.info("DRY-RUN (fx-market-pulse):\n%s", text)
        return

    chat_id = resolve_chat_id(args.chat_id, args.to_test)
    bot = Bot(token=TOKEN)
    await bot.send_message(
        chat_id=chat_id,
        text=text,
        parse_mode=constants.ParseMode.HTML,
        disable_web_page_preview=True,
    )
    _save_caches(fx_cache_path, inter_cache_path, cbr_date, text, date_local, inter_today)
    logging.info("FX Market Pulse sent: chat=%s", chat_id)


if __name__ == "__main__":
    asyncio.run(main())
