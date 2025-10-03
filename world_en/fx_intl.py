# world_en/fx_intl.py
import datetime as dt, requests

HDR = {"User-Agent":"WorldVibeMeterBot/1.0","Accept":"application/json"}

def _ts_exchangerate_host(base, symbols, days=5):
    end = dt.date.today(); start = end - dt.timedelta(days=days)
    r = requests.get("https://api.exchangerate.host/timeseries",
        params={"base":base,"symbols":",".join(symbols),
                "start_date":start.isoformat(),"end_date":end.isoformat()},
        timeout=25, headers=HDR)
    r.raise_for_status(); return r.json().get("rates",{})

def _latest_exchangerate_host(base, symbols):
    r = requests.get("https://api.exchangerate.host/latest",
        params={"base":base,"symbols":",".join(symbols)},
        timeout=20, headers=HDR)
    r.raise_for_status(); return r.json().get("rates",{})

def _latest_frankfurter_to_usd(symbols):
    # frankfurter.app = base EUR; конвертим к USD
    r = requests.get("https://api.frankfurter.app/latest",
        params={"from":"EUR","to":",".join(set(symbols+['USD']))},
        timeout=20, headers=HDR)
    r.raise_for_status(); data = r.json().get("rates",{})
    eur_usd = data.get("USD")
    out = {}
    if eur_usd:
        for s,v in data.items():
            if s=="USD": continue
            # 1 USD = ? S :  (1 EUR / EURUSD) * S  -> курс S к USD
            out[s] = v / eur_usd
    return out

def fetch_rates(base: str, symbols: list[str]):
    # 1) пробуем timeseries (есть две даты -> считаем %)
    rates = _ts_exchangerate_host(base, symbols)
    days = sorted(rates.keys())
    last, prev = None, None
    for d in reversed(days):
        if last is None: last = d
        elif prev is None: prev = d; break
    items = {}
    if last:
        for s in symbols:
            cur = (rates.get(last, {}) or {}).get(s)
            prv = (rates.get(prev, {}) or {}).get(s) if prev else None
            chg = ((cur - prv)/prv*100.0) if (cur is not None and prv not in (None,0)) else None
            items[s] = {"rate": cur, "chg_pct": chg}
        items[base] = {"rate": 1.0, "chg_pct": 0.0}
        # если всё None — падаем на фолбэк
        if any(v["rate"] is not None for v in items.values()):
            return {"base":base,"asof":last,"prev":prev,"items":items}

    # 2) latest у exchangerate.host
    latest = _latest_exchangerate_host(base, symbols)
    if latest:
        for s in symbols:
            items[s] = {"rate": latest.get(s), "chg_pct": None}
        items[base] = {"rate": 1.0, "chg_pct": 0.0}
        return {"base":base,"asof":"latest","prev":None,"items":items}

    # 3) фолбэк frankfurter (EUR-база) -> в USD
    last2 = _latest_frankfurter_to_usd(symbols)
    if last2:
        for s in symbols:
            items[s] = {"rate": last2.get(s), "chg_pct": None}
        items[base] = {"rate": 1.0, "chg_pct": 0.0}
        return {"base":base,"asof":"latest(frkf)","prev":None,"items":items}

    # 4) совсем пусто
    return {"base":base,"asof":"n/a","prev":None,
            "items":{s:{"rate":None,"chg_pct":None} for s in symbols}|{base:{"rate":1.0,"chg_pct":0.0}}}

def format_line(data: dict, order: list[str]):
    out = []
    for sym in order:
        it = data["items"].get(sym, {})
        r, d = it.get("rate"), it.get("chg_pct")
        r_txt = f"{sym} {r:.4f}" if isinstance(r,(int,float)) else f"{sym} —"
        d_txt = f"({d:+.2f}%)" if isinstance(d,(int,float)) else "(—)"
        out.append(f"{r_txt} {d_txt}")
    return " • ".join(out)
