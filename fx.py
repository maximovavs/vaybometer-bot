#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fx.py — курсы валют для ежедневных постов.

Источник: ЦБ РФ (https://www.cbr-xml-daily.ru/daily_json.js)
Обновляется обычно около 11:30 мск.

Публичный интерфейс:
- fetch_cbr_daily() -> dict                         # сырой JSON ЦБ
- parse_cbr_rates(data) -> dict                     # нормализованные курсы
- format_rates_line(rates) -> str                   # строка для поста
- should_publish_again(cache_path, cbr_date) -> bool# постить ли снова в 12:00
- get_rates(date, tz) -> dict                       # удобный словарь для поста (ЦБ РФ)
- save_fx_cache(cache_path, cbr_date, text) -> None # записать факт публикации

— для FX-поста (EUR-база):
- get_intermarket_eur() -> {'USD':..,'GBP':..,'TRY':..,'ILS':..}
- get_ecb_eur_rates() -> (dict, 'YYYY-MM-DD')       # официальные курсы ЕЦБ
  (опционально) get_ecb_official() -> dict          # только словарь без даты

Кэш кладём в fx_cache.json. Если существует папка "data", используем "data/fx_cache.json".
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, Any, Optional, Tuple

import json
import requests
import pendulum
import xml.etree.ElementTree as ET

CBR_URL = "https://www.cbr-xml-daily.ru/daily_json.js"

# Где хранить кэш
FX_CACHE_PATH = Path("fx_cache.json")
if Path("data").is_dir():
    FX_CACHE_PATH = Path("data") / "fx_cache.json"

# ЕЦБ — заголовки
ECB_HEADERS = {
    "User-Agent": "VayboMeter/1.0 (+https://t.me/vaybometer)",
    "Accept": "application/xml,text/xml,application/json;q=0.9,*/*;q=0.8",
}

CODES_EUR_BASE = ("USD", "GBP", "TRY", "ILS")


# ─────────────────────────── сетевые функции (ЦБ) ───────────────────────────
def fetch_cbr_daily(timeout: float = 10.0) -> Dict[str, Any]:
    """Тянет JSON с дневными курсами ЦБ. Возвращает {} при ошибке."""
    try:
        r = requests.get(CBR_URL, timeout=timeout, headers={"User-Agent": "VayboMeter/1.0"})
        r.raise_for_status()
        return r.json()
    except Exception:
        return {}


# ─────────────────────────── парсинг & формат ───────────────────────────────
def _get_safe_val(d: Dict[str, Any], key: str, default: Optional[float] = None) -> Optional[float]:
    try:
        v = d.get(key)
        return float(v) if v is not None else default
    except Exception:
        return default


def parse_cbr_rates(data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Превращает JSON ЦБ в компактный словарь:
    {
      "date": "YYYY-MM-DD",
      "USD": {"value": 94.12, "prev": 94.47, "delta": -0.35},
      "EUR": {...},
      "CNY": {...}
    }
    """
    if not data:
        return {}

    date_iso = data.get("Date") or ""
    try:
        date_utc = pendulum.parse(date_iso)  # в ответе есть таймзона
        date_out = date_utc.in_tz("Europe/Moscow").format("YYYY-MM-DD")
    except Exception:
        date_out = pendulum.now("Europe/Moscow").format("YYYY-MM-DD")

    out: Dict[str, Any] = {"date": date_out}
    valute = data.get("Valute", {})

    for code in ("USD", "EUR", "CNY"):
        row = valute.get(code) or {}
        value = _get_safe_val(row, "Value")
        prev  = _get_safe_val(row, "Previous")
        delta = (value - prev) if (value is not None and prev is not None) else None
        out[code] = {"value": value, "prev": prev, "delta": delta}

    return out


def _fmt_delta(x: Optional[float]) -> str:
    if x is None:
        return "0.00"
    sign = "−" if x < 0 else ""
    return f"{sign}{abs(x):.2f}"


def format_rates_line(rates: Dict[str, Any]) -> str:
    """
    Делает компактную строку вида:
    USD: 94.12 ₽ (−0.35) • EUR: 101.43 ₽ (−0.27) • CNY: 12.90 ₽ (0.00)
    """
    def item(code: str) -> str:
        r = rates.get(code) or {}
        v = r.get("value")
        try:
            vs = f"{float(v):.2f}"
        except Exception:
            vs = "—"
        return f"{code}: {vs} ₽ ({_fmt_delta(r.get('delta'))})"

    return " • ".join([item("USD"), item("EUR"), item("CNY")])


# ───────────────────────────── публикационный кэш ───────────────────────────
def _read_cache(path: Path) -> Dict[str, Any]:
    try:
        return json.loads(path.read_text("utf-8"))
    except Exception:
        return {}


def should_publish_again(cache_path: Path = FX_CACHE_PATH, cbr_date: str = "") -> bool:
    """
    True, если ЦБ обновил дату (значит, в 12:00 можно публиковать).
    Если дата в кэше совпадает с cbr_date — False (не дублируем пост).
    """
    if not cbr_date:
        return True
    cached = _read_cache(cache_path)
    last = cached.get("last_cbr_date", "")
    return last != cbr_date


def save_fx_cache(cache_path: Path = FX_CACHE_PATH, cbr_date: str = "", text: str = "") -> None:
    """Сохраняет дату последней публикации и текст (для отладки)."""
    payload = {"last_cbr_date": cbr_date, "last_text": text, "saved_at": pendulum.now("UTC").to_iso8601_string()}
    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), "utf-8")
    except Exception:
        pass


# ───────────────────────── интерфейс для поста (ЦБ РФ) ─────────────────────
def get_rates(date, tz) -> Dict[str, Any]:
    """
    Унифицированный интерфейс для поста:
    возвращает {"USD": {"value":..,"delta":..}, "EUR": {...}, "CNY": {...}, "as_of": "YYYY-MM-DD"}.
    Если сеть недоступна — {}.
    """
    raw = fetch_cbr_daily()
    if not raw:
        return {}

    parsed = parse_cbr_rates(raw)
    rates = {
        "USD": {"value": parsed.get("USD", {}).get("value"), "delta": parsed.get("USD", {}).get("delta")},
        "EUR": {"value": parsed.get("EUR", {}).get("value"), "delta": parsed.get("EUR", {}).get("delta")},
        "CNY": {"value": parsed.get("CNY", {}).get("value"), "delta": parsed.get("CNY", {}).get("delta")},
        "as_of": parsed.get("date"),
        "cbr_date": parsed.get("date"),
    }
    return rates


# ─────────────────────── межрынок (EUR-база) для FX-поста ───────────────────
def get_intermarket_eur() -> Dict[str, float]:
    """
    Возвращает межрыночные кроссы к EUR: {'USD':..., 'GBP':..., 'TRY':..., 'ILS':...}.
    Источники (мягкие фолбэки):
      1) world_en.fx_intl.fetch_rates(base='EUR', symbols=[...])
      2) exchangerate.host/latest?base=EUR&symbols=...
      3) frankfurter.app/latest (ECB, base=EUR)
    """
    symbols = list(CODES_EUR_BASE)

    # 1) Если есть твой помощник world_en/fx_intl.py — используем его
    try:
        from world_en.fx_intl import fetch_rates  # type: ignore
        fx = fetch_rates("EUR", symbols)
        items = (fx or {}).get("items", {}) or {}
        out = {}
        for s in symbols:
            v = items.get(s, {}).get("rate")
            if isinstance(v, (int, float)):
                out[s] = float(v)
        if out:
            return out
    except Exception:
        pass

    # 2) exchangerate.host
    try:
        r = requests.get(
            "https://api.exchangerate.host/latest",
            params={"base": "EUR", "symbols": ",".join(symbols)},
            timeout=12,
            headers={"User-Agent": "VayboMeter/1.0"},
        )
        if r.status_code < 400:
            rates = (r.json() or {}).get("rates", {}) or {}
            out = {k: float(v) for k, v in rates.items() if k in symbols and isinstance(v, (int, float))}
            if out:
                return out
    except Exception:
        pass

    # 3) frankfurter.app (ECB)
    try:
        r = requests.get(
            "https://api.frankfurter.app/latest",
            params={"from": "EUR", "to": ",".join(symbols)},
            timeout=12,
            headers={"User-Agent": "VayboMeter/1.0"},
        )
        if r.status_code < 400:
            rates = (r.json() or {}).get("rates", {}) or {}
            out = {k: float(v) for k, v in rates.items() if k in symbols and isinstance(v, (int, float))}
            if out:
                return out
    except Exception:
        pass

    return {}  # всё упало — вернём пусто (пост аккуратно это переживёт)


# ───────────────────────── ЕЦБ (официальные, EUR-база) ──────────────────────
def _parse_ecb_latest(xml_bytes: bytes, want=CODES_EUR_BASE) -> Tuple[Dict[str, float], Optional[str]]:
    try:
        root = ET.fromstring(xml_bytes)
        cubes = root.findall(".//{*}Cube[@time]")
        if not cubes:
            return {}, None
        c = cubes[-1]
        date = c.attrib.get("time")
        out: Dict[str, float] = {}
        for cc in c.findall("{*}Cube"):
            code = cc.attrib.get("currency")
            rate = cc.attrib.get("rate")
            if code in want and rate:
                try:
                    out[code] = float(rate)
                except Exception:
                    pass
        return out, date
    except Exception:
        return {}, None


def get_ecb_eur_rates() -> Tuple[Dict[str, float], Optional[str]]:
    """
    Возвращает (dict, 'YYYY-MM-DD') с официальными курсами ЕЦБ к EUR (USD/GBP/TRY/ILS).
    """
    try:
        r = requests.get("https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml",
                         headers=ECB_HEADERS, timeout=12)
        r.raise_for_status()
        return _parse_ecb_latest(r.content)
    except Exception:
        return {}, None


def get_ecb_official() -> Dict[str, float]:
    """Упрощённый интерфейс: только словарь курсов ЕЦБ к EUR (без даты)."""
    d, _ = get_ecb_eur_rates()
    return d


# ───────────────────────────── CLI тест (опц.) ──────────────────────────────
if __name__ == "__main__":
    # Быстрый тест ЦБ
    raw = fetch_cbr_daily()
    parsed = parse_cbr_rates(raw)
    line = format_rates_line(parsed)
    print(f"Дата ЦБ: {parsed.get('date')}\n{line}")
    if should_publish_again(FX_CACHE_PATH, parsed.get("date", "")):
        save_fx_cache(FX_CACHE_PATH, parsed.get("date", ""), line)
        print("Кэш обновлён.")
    else:
        print("Дата уже публиковалась — пропускаем.")

    # Тест межрынка
    print("\nМежрынок (EUR):", get_intermarket_eur())

    # Тест ЕЦБ
    ecb, d = get_ecb_eur_rates()
    print(f"ЕЦБ ({d}):", ecb)
