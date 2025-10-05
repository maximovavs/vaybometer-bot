#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fx_intl.py — устойчивый сбор курсов валют с процентным изменением.
Порядок источников (без ключей, бесплатные):
  1) exchangerate.host /timeseries → считаем % (предпочтительно)
  2) frankfurter.app (ECB) /timeseries → считаем % (только если base=USD; пересчёт из EUR)
  3) exchangerate.host /latest → курсы без %
  4) open.er-api.com /v6/latest → курсы без %
  5) frankfurter.app /latest → курсы без % (только если base=USD; пересчёт из EUR)

API:
    fetch_rates(base: str, symbols: list[str]) -> dict
    format_line(data: dict, order: list[str]) -> str
"""

from __future__ import annotations

import datetime as dt
import time
import requests

HDR = {
    "User-Agent": "WorldVibeMeterBot/1.0 (+https://github.com/)",
    "Accept": "application/json,text/plain",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
}


# --------------------------- helpers ---------------------------

def _safe_get(url: str, params: dict | None = None, timeout: int = 25, retries: int = 2):
    """HTTP GET с мягкими повторами; при ошибке возвращает None (не бросает исключение)."""
    params = params or {}
    for i in range(retries + 1):
        try:
            r = requests.get(url, params=params, timeout=timeout, headers=HDR)
            if r.status_code >= 400:
                # некоторые провайдеры любят 5xx — не валим пайплайн
                continue
            return r.json()
        except Exception:
            if i < retries:
                time.sleep(0.6 * (i + 1))
            else:
                return None
    return None


# -------------------- exchangerate.host (base=any) --------------------

def _ts_exhost(base: str, symbols: list[str], days: int = 5) -> dict:
    """Timeseries для base/symbols за последние days; ключи словаря — ISO даты."""
    end = dt.date.today()
    start = end - dt.timedelta(days=days)
    js = _safe_get(
        "https://api.exchangerate.host/timeseries",
        {
            "base": base,
            "symbols": ",".join(symbols),
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
        },
        timeout=25,
    )
    return (js or {}).get("rates", {}) or {}


def _latest_exhost(base: str, symbols: list[str]) -> dict:
    js = _safe_get(
        "https://api.exchangerate.host/latest",
        {"base": base, "symbols": ",".join(symbols)},
        timeout=20,
    )
    return (js or {}).get("rates", {}) or {}


# -------------------- frankfurter.app (ECB, base=EUR) --------------------

def _ts_frankfurter_usd(symbols: list[str], days: int = 7) -> dict:
    """
    Возвращает timeseries в БАЗЕ USD для запрошенных symbols,
    пересчитанный из EUR-базы ECB:
        rate(USD->S) = (EUR->S) / (EUR->USD)
        rate(USD->EUR) = 1 / (EUR->USD)
    Ключи верхнего уровня — ISO даты.
    """
    end = dt.date.today()
    start = end - dt.timedelta(days=days)
    to = ",".join(sorted(set(symbols + ["USD"])))
    js = _safe_get(
        f"https://api.frankfurter.app/{start.isoformat()}..{end.isoformat()}",
        {"from": "EUR", "to": to},
        timeout=20,
    )
    rates = (js or {}).get("rates", {}) or {}
    out = {}
    for day, row in rates.items():
        eur_usd = row.get("USD")
        if not isinstance(eur_usd, (int, float)) or eur_usd == 0:
            continue
        out_day = {}
        for s in symbols:
            if s == "EUR":
                out_day[s] = 1.0 / eur_usd
            else:
                v = row.get(s)
                out_day[s] = (v / eur_usd) if isinstance(v, (int, float)) else None
        out[day] = out_day
    return out


def _latest_frankfurter_to_usd(symbols: list[str]) -> dict:
    """Последние курсы, пересчитанные к USD (см. формулы выше)."""
    js = _safe_get(
        "https://api.frankfurter.app/latest",
        {"from": "EUR", "to": ",".join(sorted(set(symbols + ["USD"])))},
        timeout=20,
    )
    data = (js or {}).get("rates", {}) or {}
    eur_usd = data.get("USD")
    if not isinstance(eur_usd, (int, float)) or eur_usd == 0:
        return {}
    out = {}
    for s in symbols:
        if s == "EUR":
            out["EUR"] = 1.0 / eur_usd
        else:
            v = data.get(s)
            out[s] = (v / eur_usd) if isinstance(v, (int, float)) else None
    return out


# -------------------- open.er-api.com (base=any) --------------------

def _latest_open_erapi(base: str) -> dict:
    js = _safe_get(f"https://open.er-api.com/v6/latest/{base}", timeout=20)
    if not js or js.get("result") != "success":
        return {}
    return js.get("rates", {}) or {}


# --------------------------- public API ---------------------------

def fetch_rates(base: str, symbols: list[str]) -> dict:
    """
    Возвращает безопасный объект курсов и изменений:

    {
      "base": base,               # базовая валюта
      "asof": "2025-10-03",       # дата источника (или 'latest', 'erapi-latest', 'frkf-latest', 'n/a')
      "prev": "2025-10-02",       # предыдущая дата для расчёта % (если была)
      "items": {
         "EUR": {"rate": float|None, "chg_pct": float|None},
         ...,
         base: {"rate": 1.0, "chg_pct": 0.0}
      }
    }
    Никогда не бросает исключения: в крайнем случае вернёт пустые курсы (None).
    """
    items: dict[str, dict] = {}

    # 1) exchangerate.host/timeseries — считаем %
    ts = _ts_exhost(base, symbols)
    if ts:
        days = sorted(ts.keys())
        last, prev = None, None
        for d in reversed(days):
            if last is None:
                last = d
            elif prev is None:
                prev = d
                break
        for s in symbols:
            cur = (ts.get(last, {}) or {}).get(s)
            prv = (ts.get(prev, {}) or {}).get(s) if prev else None
            chg = None
            try:
                if cur is not None and prv not in (None, 0):
                    chg = (cur - prv) / prv * 100.0
            except Exception:
                chg = None
            items[s] = {"rate": cur, "chg_pct": chg}
        items[base] = {"rate": 1.0, "chg_pct": 0.0}
        if any(v["rate"] is not None for v in items.values()):
            return {"base": base, "asof": last, "prev": prev, "items": items}

    # 2) frankfurter.app/timeseries — считаем % (только если base=USD)
    if base.upper() == "USD":
        tsf = _ts_frankfurter_usd(symbols, days=7)
        if tsf:
            days = sorted(tsf.keys())
            last, prev = None, None
            for d in reversed(days):
                if last is None:
                    last = d
                elif prev is None:
                    prev = d
                    break
            for s in symbols:
                cur = (tsf.get(last, {}) or {}).get(s)
                prv = (tsf.get(prev, {}) or {}).get(s) if prev else None
                chg = None
                try:
                    if cur is not None and prv not in (None, 0):
                        chg = (cur - prv) / prv * 100.0
                except Exception:
                    chg = None
                items[s] = {"rate": cur, "chg_pct": chg}
            items[base] = {"rate": 1.0, "chg_pct": 0.0}
            if any(v["rate"] is not None for v in items.values()):
                return {"base": base, "asof": last, "prev": prev, "items": items}

    # 3) exchangerate.host/latest — без %
    latest = _latest_exhost(base, symbols)
    if latest:
        for s in symbols:
            items[s] = {"rate": latest.get(s), "chg_pct": None}
        items[base] = {"rate": 1.0, "chg_pct": 0.0}
        return {"base": base, "asof": "latest", "prev": None, "items": items}

    # 4) open.er-api.com/latest — без %
    er = _latest_open_erapi(base)
    if er:
        for s in symbols:
            items[s] = {"rate": er.get(s), "chg_pct": None}
        items[base] = {"rate": 1.0, "chg_pct": 0.0}
        return {"base": base, "asof": "erapi-latest", "prev": None, "items": items}

    # 5) frankfurter.app/latest → к USD (если base=USD) — без %
    if base.upper() == "USD":
        frk = _latest_frankfurter_to_usd(symbols)
        if frk:
            for s in symbols:
                items[s] = {"rate": frk.get(s), "chg_pct": None}
            items[base] = {"rate": 1.0, "chg_pct": 0.0}
            return {"base": base, "asof": "frkf-latest", "prev": None, "items": items}

    # 6) полный фолбэк — пустые курсы, но валидная структура
    items = {s: {"rate": None, "chg_pct": None} for s in symbols}
    items[base] = {"rate": 1.0, "chg_pct": 0.0}
    return {"base": base, "asof": "n/a", "prev": None, "items": items}


def format_line(fx, order=None):
    if not fx or "items" not in fx:
        return "—"

    order = order or list(fx["items"].keys())
    items = fx["items"]

    # сколько знаков для каждой валюты
    dec = {"EUR": 4, "CNY": 4, "JPY": 2, "INR": 2, "IDR": 0, "USD": 4}

    parts = []
    for cur in order:
        it = items.get(cur, {})
        r = it.get("rate")
        chg = it.get("chg_pct")
        if r is None:
            parts.append(f"{cur} — (—)")
            continue

        # IDR с разделителями тысяч
        if cur == "IDR":
            rate_str = f"{r:,.0f}"
        else:
            rate_str = f"{r:.{dec.get(cur,4)}f}"

        delta = "—" if chg is None else f"{chg:+.2f}%"
        parts.append(f"{cur} {rate_str} ({delta})")

    return " • ".join(parts)
