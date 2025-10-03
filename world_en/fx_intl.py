# world_en/fx_intl.py
import datetime as dt, time, requests

HDR = {"User-Agent":"WorldVibeMeterBot/1.0 (+https://github.com/)",
       "Accept":"application/json,text/plain","Cache-Control":"no-cache","Pragma":"no-cache"}

def _safe_get(url, params=None, timeout=25, retries=2):
    params = params or {}
    for i in range(retries+1):
        try:
            r = requests.get(url, params=params, timeout=timeout, headers=HDR)
            if r.status_code >= 400:
                continue
            return r.json()
        except Exception:
            if i < retries: time.sleep(0.6*(i+1))
            else: return None
    return None

# ---------- exchangerate.host ----------
def _ts_exhost(base, symbols, days=5):
    end = dt.date.today(); start = end - dt.timedelta(days=days)
    js = _safe_get("https://api.exchangerate.host/timeseries",
                   {"base":base,"symbols":",".join(symbols),
                    "start_date":start.isoformat(),"end_date":end.isoformat()}, 25)
    return (js or {}).get("rates",{}) or {}

def _latest_exhost(base, symbols):
    js = _safe_get("https://api.exchangerate.host/latest",
                   {"base":base,"symbols":",".join(symbols)}, 20)
    return (js or {}).get("rates",{}) or {}

# ---------- Frankfurter (ECB) ----------
def _ts_frankfurter_usd(symbols, days=5):
    """
    Возвращает timeseries в БАЗЕ USD для запрошенных symbols,
    пересчитав из EUR-базы ECB: rate(USD->S) = (EUR->S) / (EUR->USD).
    """
    end = dt.date.today(); start = end - dt.timedelta(days=days)
    to = ",".join(sorted(set(symbols+["USD"])))
    js = _safe_get(f"https://api.frankfurter.app/{start.isoformat()}..{end.isoformat()}",
                   {"from":"EUR","to":to}, 20)
    rates = (js or {}).get("rates",{}) or {}
    out = {}
    for day, d in rates.items():
        eur_usd = d.get("USD")
        if not eur_usd:  # без EUR->USD не пересчитаем
            continue
        out[day] = {}
        for s in symbols:
            v = d.get(s)
            out[day][s] = (v/eur_usd) if isinstance(v,(int,float)) else None
    return out

def _latest_open_erapi(base):
    js = _safe_get(f"https://open.er-api.com/v6/latest/{base}", timeout=20)
    if not js or js.get("result")!="success": return {}
    return js.get("rates",{}) or {}

# ---------- API ----------
def fetch_rates(base: str, symbols: list[str]):
    items = {}
    # 1) timeseries exhost -> даёт %
    ts = _ts_exhost(base, symbols)
    if ts:
        days = sorted(ts.keys()); last=prev=None
        for d in reversed(days):
            if not last: last=d
            elif not prev: prev=d; break
        for s in symbols:
            cur = (ts.get(last,{}) or {}).get(s)
            prv = (ts.get(prev,{}) or {}).get(s) if prev else None
            chg = None
            try:
                if cur is not None and prv not in (None,0): chg=(cur-prv)/prv*100.0
            except Exception: chg=None
            items[s]={"rate":cur,"chg_pct":chg}
        items[base]={"rate":1.0,"chg_pct":0.0}
        if any(v["rate"] is not None for v in items.values()):
            return {"base":base,"asof":last,"prev":prev,"items":items}

    # 2) timeseries Frankfurter (ECB) -> тоже считаем %
    if base.upper()=="USD":
        tsf = _ts_frankfurter_usd(symbols, days=7)
        if tsf:
            days = sorted(tsf.keys()); last=prev=None
            for d in reversed(days):
                if not last: last=d
                elif not prev: prev=d; break
            for s in symbols:
                cur = (tsf.get(last,{}) or {}).get(s)
                prv = (tsf.get(prev,{}) or {}).get(s) if prev else None
                chg = None
                try:
                    if cur is not None and prv not in (None,0): chg=(cur-prv)/prv*100.0
                except Exception: chg=None
                items[s]={"rate":cur,"chg_pct":chg}
            items[base]={"rate":1.0,"chg_pct":0.0}
            if any(v["rate"] is not None for v in items.values()):
                return {"base":base,"asof":last,"prev":prev,"items":items}

    # 3) latest exhost
    latest = _latest_exhost(base, symbols)
    if latest:
        for s in symbols: items[s]={"rate":latest.get(s),"chg_pct":None}
        items[base]={"rate":1.0,"chg_pct":0.0}
        return {"base":base,"asof":"latest","prev":None,"items":items}

    # 4) open.er-api latest
    er = _latest_open_erapi(base)
    if er:
        for s in symbols: items[s]={"rate":er.get(s),"chg_pct":None}
        items[base]={"rate":1.0,"chg_pct":0.0}
        return {"base":base,"asof":"erapi-latest","prev":None,"items":items}

    # 5) пустой фолбэк
    items = {s:{"rate":None,"chg_pct":None} for s in symbols}
    items[base]={"rate":1.0,"chg_pct":0.0}
    return {"base":base,"asof":"n/a","prev":None,"items":items}

def format_line(data: dict, order: list[str]):
    parts=[]
    for sym in order:
        it=data["items"].get(sym,{})
        r,d=it.get("rate"),it.get("chg_pct")
        r_txt=f"{sym} {r:.4f}" if isinstance(r,(int,float)) else f"{sym} —"
        d_txt=f"({d:+.2f}%)" if isinstance(d,(int,float)) else "(—)"
        parts.append(f"{r_txt} {d_txt}")
    return " • ".join(parts)
