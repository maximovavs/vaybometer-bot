# world_en/fx_intl.py
import datetime as dt
import time
import requests

HDR = {
    "User-Agent": "WorldVibeMeterBot/1.0 (+https://github.com/)",
    "Accept": "application/json,text/plain",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
}

def _safe_get(url, params=None, timeout=25, retries=2):
    """GET с мягкими повторами; при ошибке возвращает None (не бросает исключение)."""
    params = params or {}
    for i in range(retries + 1):
        try:
            r = requests.get(url, params=params, timeout=timeout, headers=HDR)
            # некоторые провайдеры отдают 5xx -> не поднимаем исключение, а пробуем фолбэк
            if r.status_code >= 400:
                continue
            return r.json()
        except Exception:
            if i < retries:
                time.sleep(0.6 * (i + 1))
            else:
                return None
    return None

# ---------- exchangerate.host ----------

def _ts_exchangerate_host(base, symbols, days=5):
    end = dt.date.today()
    start = end - dt.timedelta(days=days)
    js = _safe_get(
        "https://api.exchangerate.host/timeseries",
        {"base": base, "symbols": ",".join(symbols),
         "start_date": start.isoformat(), "end_date": end.isoformat()},
        timeout=25
    )
    if not js:
        return {}
    return js.get("rates", {}) or {}

def _latest_exchangerate_host(base, symbols):
    js = _safe_get(
        "https://api.exchangerate.host/latest",
        {"base": base, "symbols": ",".join(symbols)},
        timeout=20
    )
    if not js:
        return {}
    return js.get("rates", {}) or {}

# ---------- альтернативы без ключей ----------

def _latest_open_erapi(base):
    # https://open.er-api.com/v6/latest/USD
    js = _safe_get(f"https://open.er-api.com/v6/latest/{base}", timeout=20)
    if not js or js.get("result") != "success":
        return {}
    return js.get("rates", {}) or {}

def _latest_frankfurter_to_usd(symbols):
    # frankfurter.app = base EUR; конвертируем к USD (если нужен)
    js = _safe_get("https://api.frankfurter.app/latest",
                   {"from": "EUR", "to": ",".join(set(symbols + ["USD"]))},
                   timeout=20)
    if not js:
        return {}
    data = js.get("rates", {}) or {}
    eur_usd = data.get("USD")
    if not eur_usd:
        return {}
    out = {}
    for s, v in data.items():
        if s == "USD":
            continue
        # курс S к USD: (EUR->S)/(EUR->USD)
        out[s] = v / eur_usd
    return out

# ---------- публичный API ----------

def fetch_rates(base: str, symbols: list[str]):
    """
    Возвращает:
    {
      "base": base, "asof": "...", "prev": "...",
      "items": { "EUR": {"rate": float|None, "chg_pct": float|None}, ... , base: {1.0, 0.0} }
    }
    Всегда безопасный объект, без исключений.
    """
    items = {}

    # 1) timeseries (даёт %)
    rates = _ts_exchangerate_host(base, symbols)
    if rates:
        days_sorted = sorted(rates.keys())
        last, prev = None, None
        for d in reversed(days_sorted):
            if last is None:
                last = d
            elif prev is None:
                prev = d
                break
        for s in symbols:
            cur = (rates.get(last, {}) or {}).get(s)
            prv = (rates.get(prev, {}) or {}).get(s) if prev else None
            chg = None
            try:
                if cur is not None and prv not in (None, 0):
                    chg = (cur - prv) / prv * 100.0
            except Exception:
                chg = None
            items[s] = {"rate": cur, "chg_pct": chg}
        items[base] = {"rate": 1.0, "chg_pct": 0.0}
        # если есть хотя бы один курс — считаем успехом
        if any(v["rate"] is not None for v in items.values()):
            return {"base": base, "asof": last, "prev": prev, "items": items}

    # 2) latest exchangerate.host (без %)
    latest = _latest_exchangerate_host(base, symbols)
    if latest:
        for s in symbols:
            items[s] = {"rate": latest.get(s), "chg_pct": None}
        items[base] = {"rate": 1.0, "chg_pct": 0.0}
        return {"base": base, "asof": "latest", "prev": None, "items": items}

    # 3) open.er-api.com (без %)
    open_api = _latest_open_erapi(base)
    if open_api:
        for s in symbols:
            items[s] = {"rate": open_api.get(s), "chg_pct": None}
        items[base] = {"rate": 1.0, "chg_pct": 0.0}
        return {"base": base, "asof": "erapi-latest", "prev": None, "items": items}

    # 4) frankfurter.app → пересчёт к USD (если base=USD)
    if base.upper() == "USD":
        frk = _latest_frankfurter_to_usd(symbols)
        if frk:
            for s in symbols:
                items[s] = {"rate": frk.get(s), "chg_pct": None}
            items[base] = {"rate": 1.0, "chg_pct": 0.0}
            return {"base": base, "asof": "frkf-latest", "prev": None, "items": items}

    # 5) полный фолбэк: пустые курсы, но валидная структура
    items = {s: {"rate": None, "chg_pct": None} for s in symbols}
    items[base] = {"rate": 1.0, "chg_pct": 0.0}
    return {"base": base, "asof": "n/a", "prev": None, "items": items}

def format_line(data: dict, order: list[str]):
    parts = []
    for sym in order:
        it = data["items"].get(sym, {})
        r, d = it.get("rate"), it.get("chg_pct")
        r_txt = f"{sym} {r:.4f}" if isinstance(r, (int, float)) else f"{sym} —"
        d_txt = f"({d:+.2f}%)" if isinstance(d, (int, float)) else "(—)"
        parts.append(f"{r_txt} {d_txt}")
    return " • ".join(parts)
