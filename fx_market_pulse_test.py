#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Safe dry-run/test for the separate Cyprus daytime FX post with Market Pulse.

This is intentionally separate from morning/evening weather posts.
It builds the existing EUR-base FX post and appends a compact BTC/ETH/Gold
market pulse for review before any production integration.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
from typing import Any

import pendulum
import requests
from telegram import Bot, constants

from post_cy import TZ_STR, _build_fx_message_eur, _fx_cache_paths, resolve_chat_id


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
    url = "https://api.coingecko.com/api/v3/simple/price"
    try:
        r = requests.get(
            url,
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
    except Exception:
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
        closes = quote.get("close") or []
        for raw in reversed(closes):
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


async def main() -> None:
    parser = argparse.ArgumentParser(description="Safe test for Cyprus FX Market Pulse")
    parser.add_argument("--date", default="")
    parser.add_argument("--to-test", action="store_true")
    parser.add_argument("--send", action="store_true")
    parser.add_argument("--chat-id", default="")
    args = parser.parse_args()

    tz = pendulum.timezone(TZ_STR)
    date_local = pendulum.parse(args.date).in_tz(tz) if args.date else pendulum.now(tz)
    _fx_cache_path, inter_cache_path = _fx_cache_paths(to_test=True)
    fx_text, _rates, _inter = _build_fx_message_eur(date_local, tz, inter_cache_path)
    final_text = inject_market_pulse(fx_text, build_market_pulse_block())

    print("\n===== CYPRUS FX MARKET PULSE TEST BEGIN =====\n")
    print(final_text)
    print("\n===== CYPRUS FX MARKET PULSE TEST END =====\n")

    if not args.send:
        return

    token = os.getenv("TELEGRAM_TOKEN", "").strip()
    if not token:
        raise SystemExit("TELEGRAM_TOKEN is not set")
    chat_id = resolve_chat_id(args.chat_id, args.to_test)
    bot = Bot(token=token)
    await bot.send_message(
        chat_id=chat_id,
        text="<b>Test FX Market Pulse</b>\n" + final_text,
        parse_mode=constants.ParseMode.HTML,
        disable_web_page_preview=True,
    )


if __name__ == "__main__":
    asyncio.run(main())
