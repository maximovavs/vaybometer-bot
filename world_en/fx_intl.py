#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fx_intl.py — международные курсы валют для WorldVibeMeter (без ЦБ РФ).
Провайдеры:
  1) Frankfurter (ECB)  https://api.frankfurter.app
  2) exchangerate.host  https://exchangerate.host

Функции:
  fetch_rates(base, symbols) -> dict         # текущие и вчерашние значения + %change
  format_line(data, order) -> str            # строка для телеграма: 💱 USD 1.00 • EUR 0.94 (-0.1%) • CNY 7.13 (+0.2%)
  main()                                     # CLI: печатает строку или json

Пример:
  from world_en.fx_intl import fetch_rates, format_line
  data = fetch_rates("USD", ["EUR","CNY","JPY"])
  txt  = format_line(data, order=["USD","EUR","CNY","JPY"])
"""

from __future__ import annotations
import os
import json
import time
import math
import typing as t
import datetime as dt
import requests

HEADERS = {
    "User-Agent": "WorldVibeMeterBot/1.0 (+https://github.com/)",
    "Accept": "application/json",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
}

FrankBase = "https://api.frankfurter.app"
HostBase  = "https://api.exchangerate.host"


def _get(url: str, params: dict | None = None, retries: int = 2) -> dict | None:
    att = 0
    while att <= retries:
        try:
            r = requests.get(url, params=params or {}, timeout=15, headers=HEADERS)
            r.raise_for_status()
            return r.json()
        except Exception:
            att += 1
            if att > retries:
                return None
            time.sleep(0.6 * att)


def _prev_business_day(utc_today: dt.date | None = None) -> dt.date:
    """Возвращает предыдущий рабочий день (пн–пт)."""
    if utc_today is None:
        utc_today = dt.datetime.utcnow().date()
    d = utc_today - dt.timedelta(days=1)
    while d.weekday() >= 5:  # 5,6 = сб, вс
        d -= dt.timedelta(days=1)
    return d


def _round_auto(x: float) -> str:
    """
    Умеренное форматирование курса:
      <1  → 4 знака, <10 → 3, иначе → 2.
    """
    if x is None or (isinstance(x, float) and (math.isnan(x) or math.isinf(x))):
        return "—"
    if x < 1:
        q = 4
    elif x < 10:
        q = 3
    else:
        q = 2
    return f"{x:.{q}f}"


def _pct(curr: float | None, prev: float | None) -> float | None:
    if curr is None or prev is None or prev == 0:
        return None
    return (curr - prev) / prev * 100.0


def _via_frankfurter(base: str, symbols: list[str], prev_day: dt.date) -> dict | None:
    # текущие
    latest = _get(f"{FrankBase}/latest", {"from": base, "to": ",".join(symbols)})
    if not latest or "rates" not in latest:
        return None
    # вчерашний рабочий день
    prev = _get(f"{FrankBase}/{prev_day.isoformat()}", {"from": base, "to": ",".join(symbols)})
    if not prev or "rates" not in prev:
        # бывает, что дата закрытия другая; попробуем timeseries за 2 дня
        ts = _get(f"{FrankBase}/latest", {"from": base, "to": ",".join(symbols)})
        prev = None

    return {
        "provider": "frankfurter",
        "base": latest.get("base"),
        "date": latest.get("date"),
        "rates": latest["rates"],
        "prev_date": prev_day.isoformat(),
        "prev_rates": prev.get("rates") if prev else {},
    }


def _via_host(base: str, symbols: list[str], prev_day: dt.date) -> dict | None:
    latest = _get(f"{HostBase}/latest", {"base": base, "symbols": ",".join(symbols)})
    if not latest or not latest.get("success", True):
        return None
    prev = _get(f"{HostBase}/{prev_day.isoformat()}", {"base": base, "symbols": ",".join(symbols)})
    return {
        "provider": "exchangerate.host",
        "base": base,
        "date": latest.get("date"),
        "rates": latest.get("rates", {}),
        "prev_date": prev_day.isoformat(),
        "prev_rates": (prev or {}).get("rates", {}),
    }


def fetch_rates(base: str = "USD", symbols: list[str] = ["EUR", "CNY", "JPY"]) -> dict:
    """
    Возвращает структуру:
    {
      "base": "USD",
      "date": "2025-09-23",
      "provider": "frankfurter|exchangerate.host",
      "items": {
        "USD": {"rate": 1.0, "prev": 1.0, "chg_pct": 0.0},
        "EUR": {"rate": 0.94, "prev": 0.94, "chg_pct": -0.1},
        ...
      }
    }
    """
    base = base.upper().strip()
    symbols = [s.upper().strip() for s in symbols if s.upper().strip() != base]
    prev_day = _prev_business_day()

    raw = _via_frankfurter(base, symbols, prev_day) or _via_host(base, symbols, prev_day)
    if not raw:
        # последний шанс — только текущие с host
        raw = _via_host(base, symbols, prev_day) or {"base": base, "rates": {}, "prev_rates": {}}
    items: dict[str, dict] = {}

    # базовую валюту тоже покажем
    items[base] = {"rate": 1.0, "prev": 1.0, "chg_pct": 0.0}

    for s in symbols:
        r = (raw.get("rates") or {}).get(s)
        p = (raw.get("prev_rates") or {}).get(s)
        items[s] = {
            "rate": float(r) if r is not None else None,
            "prev": float(p) if p is not None else None,
            "chg_pct": _pct(r, p),
        }

    return {
        "provider": raw.get("provider", "unknown"),
        "base": raw.get("base", base),
        "date": raw.get("date"),
        "prev_date": raw.get("prev_date", prev_day.isoformat()),
        "items": items,
    }


def format_line(data: dict, order: list[str] | None = None, prefix: str = "💱 ") -> str:
    """
    Собирает компактную строку:
      💱 USD 1.00 • EUR 0.94 (-0.1%) • CNY 7.13 (+0.2%)
    """
    if not data:
        return prefix + "n/a"
    items = data["items"]
    base = data["base"]
    if order is None:
        order = [base] + [c for c in items.keys() if c != base]

    parts = []
    for c in order:
        it = items.get(c)
        if not it:
            continue
        rate_txt = _round_auto(it["rate"]) if it["rate"] is not None else "—"
        if it["chg_pct"] is None:
            chg_txt = ""
        else:
            sign = "+" if it["chg_pct"] > 0 else ""
            chg_txt = f" ({sign}{it['chg_pct']:.2f}%)"
        parts.append(f"{c} {rate_txt}{chg_txt}")

    return prefix + " • ".join(parts)


def main():
    import argparse
    ap = argparse.ArgumentParser(description="FX Intl (ECB/exchangerate.host)")
    ap.add_argument("--base", default=os.getenv("FX_BASE", "USD"))
    ap.add_argument("--symbols", default=os.getenv("FX_SYMBOLS", "EUR,CNY,JPY"))
    ap.add_argument("--json", action="store_true", help="print raw json")
    ns = ap.parse_args()

    symbols = [s.strip().upper() for s in ns.symbols.split(",") if s.strip()]
    data = fetch_rates(ns.base.upper(), symbols)
    if ns.json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
    else:
        order = [ns.base.upper()] + [s for s in symbols if s != ns.base.upper()]
        print(format_line(data, order))


if __name__ == "__main__":
    main()
