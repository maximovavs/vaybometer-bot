# world_en/fx_intl.py
import datetime as dt
import requests

HEADERS = {
    "User-Agent": "WorldVibeMeterBot/1.0 (+https://github.com/)",
    "Accept": "application/json,text/plain",
    "Cache-Control": "no-cache", "Pragma": "no-cache",
}

def _timeseries(base, symbols, days=5):
    end = dt.date.today()
    start = end - dt.timedelta(days=days)
    url = "https://api.exchangerate.host/timeseries"
    r = requests.get(url, params={
        "base": base, "symbols": ",".join(symbols),
        "start_date": start.isoformat(), "end_date": end.isoformat()
    }, timeout=25, headers=HEADERS)
    r.raise_for_status()
    return r.json().get("rates", {})

def fetch_rates(base: str, symbols: list[str]):
    rates = _timeseries(base, symbols)
    # находим две последние даты с данными
    days_sorted = sorted(rates.keys())
    last, prev = None, None
    for d in reversed(days_sorted):
        if last is None:
            last = d
        elif prev is None:
            prev = d
            break
    items = {}
    for s in symbols:
        cur = (rates.get(last, {}) or {}).get(s)
        prv = (rates.get(prev, {}) or {}).get(s)
        chg = None
        if cur is not None and prv:
            try:
                chg = (cur - prv) / prv * 100.0
            except ZeroDivisionError:
                chg = None
        items[s] = {"rate": cur, "chg_pct": chg}
    # базовая валюта выводится как 1.000 (0.00%)
    items[base] = {"rate": 1.0, "chg_pct": 0.0}
    return {"base": base, "asof": last, "prev": prev, "items": items}

def format_line(data: dict, order: list[str]):
    def f(sym: str):
        it = data["items"].get(sym, {})
        r = it.get("rate")
        d = it.get("chg_pct")
        r_txt = f"{sym} {r:.4f}" if r is not None else f"{sym} —"
        d_txt = f"({d:+.2f}%)" if d is not None else "(—)"
        return f"{r_txt} {d_txt}"
    return " • ".join(f(s) for s in order if s in data["items"])
