#!/usr/bin/env python3
"""Daily build: GLD options (CBOE delayed, free) -> GEX levels converted to XAUUSD.
Writes site/data/levels.json once. The website never recomputes levels."""
import datetime as dt
import json
import re
import sys
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "site" / "data" / "levels.json"
CFG = json.loads((ROOT / "config.json").read_text())
UA = {"User-Agent": "Mozilla/5.0 (levels-bot)"}
OCC = re.compile(r"^([A-Z]+)(\d{6})([CP])(\d{8})$")
UTC = dt.timezone.utc


def get(url, **kw):
    err = None
    for _ in range(3):
        try:
            r = requests.get(url, headers=UA, timeout=30, **kw)
            r.raise_for_status()
            return r.json()
        except Exception as e:  # retry
            err = e
    raise err


def yahoo(sym, interval="15m", rng="5d"):
    j = get(f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}",
            params={"interval": interval, "range": rng})
    r = j["chart"]["result"][0]
    q = r["indicators"]["quote"][0]
    out = []
    for i, t in enumerate(r["timestamp"]):
        o, h, l, c = (q[k][i] for k in ("open", "high", "low", "close"))
        if None not in (o, h, l, c):
            out.append({"t": t, "o": o, "h": h, "l": l, "c": c})
    return out


def chain():
    sym = CFG["options_symbol"]
    j = get(f"https://cdn.cboe.com/api/global/delayed_quotes/options/{sym}.json")["data"]
    spot = float(j["current_price"])
    today = dt.datetime.now(UTC).date()
    rows = []
    for o in j["options"]:
        m = OCC.match(o["option"])
        if not m:
            continue
        exp = dt.datetime.strptime(m.group(2), "%y%m%d").date()
        dte = (exp - today).days
        k = int(m.group(4)) / 1000
        if dte < 0 or dte > CFG["max_dte"] or abs(k / spot - 1) > 0.15:
            continue
        rows.append(dict(dte=dte, cp=m.group(3), k=k,
                         oi=float(o.get("open_interest") or 0),
                         iv=float(o.get("iv") or 0) * 100,
                         gamma=float(o.get("gamma") or 0),
                         delta=float(o.get("delta") or 0)))
    if not rows:
        raise RuntimeError("empty options chain")
    return spot, rows


def skew_25d(rows):
    ds = sorted({r["dte"] for r in rows if r["dte"] >= 1})
    if not ds:
        return None
    sub = [r for r in rows if r["dte"] == ds[0] and r["iv"] > 0]
    p = [r for r in sub if r["cp"] == "P" and r["delta"] < 0]
    c = [r for r in sub if r["cp"] == "C" and r["delta"] > 0]
    if not p or not c:
        return None
    pi = min(p, key=lambda r: abs(r["delta"] + 0.25))
    ci = min(c, key=lambda r: abs(r["delta"] - 0.25))
    return round(pi["iv"] - ci["iv"], 2)


def gex(spot, rows):
    call, put, oi, ivw = {}, {}, {}, {}
    for r in rows:
        k, g = r["k"], r["gamma"] * r["oi"] * 100 * spot * spot * 0.01
        (call if r["cp"] == "C" else put)[k] = (call if r["cp"] == "C" else put).get(k, 0) + g
        oi[k] = oi.get(k, 0) + r["oi"]
        if r["iv"] > 0 and r["oi"] > 0:
            a = ivw.setdefault((k, r["cp"]), [0, 0])
            a[0] += r["iv"] * r["oi"]
            a[1] += r["oi"]
    strikes = sorted(set(call) | set(put))
    net = {k: call.get(k, 0) - put.get(k, 0) for k in strikes}

    def iv(k, cp):
        a = ivw.get((k, cp))
        return a[0] / a[1] if a else None

    skew = {}
    for k in strikes:
        p, c = iv(k, "P"), iv(k, "C")
        skew[k] = round(p - c, 2) if p is not None and c is not None else None
    return call, put, net, oi, skew, strikes


def flip_level(net, strikes, spot):
    cum, prev, cross = 0, None, []
    for k in strikes:
        cum += net[k]
        if prev is not None and (prev[1] < 0) != (cum < 0) and cum != prev[1]:
            cross.append(prev[0] + (k - prev[0]) * (-prev[1]) / (cum - prev[1]))
        prev = (k, cum)
    return min(cross, key=lambda x: abs(x - spot)) if cross else None


def key_levels(c):
    by = {}
    for x in c:
        d = dt.datetime.fromtimestamp(x["t"], UTC)
        by.setdefault(d.date(), []).append((d, x["o"], x["h"], x["l"]))
    ds, out = sorted(by), []
    if len(ds) >= 2:
        p = by[ds[-2]]
        out += [("Prev day high", max(r[2] for r in p)), ("Prev day low", min(r[3] for r in p))]
        a = [r for r in p if r[0].hour < 7]
        if a:
            out += [("Prev Asia high", max(r[2] for r in a)), ("Prev Asia low", min(r[3] for r in a))]
    mon = [d for d in ds if d.weekday() == 0]
    if mon:
        out.append(("Monday open", by[mon[-1]][0][1]))
    return [{"name": n, "level": round(v, 2)} for n, v in out]


def reaction(c, lvl, band):
    touch = held = 0
    prev = None
    for x in c:
        if prev is not None and abs(prev - lvl) > band and x["l"] - band <= lvl <= x["h"] + band:
            touch += 1
            if (prev - lvl) * (x["c"] - lvl) > 0:
                held += 1
        prev = x["c"]
    return touch, held


def judge(lvl, spot_x, regime, skew, share, net_k, candles, band):
    role = "support" if lvl < spot_x else "resistance"
    s = 0.6 if regime == "positive" else -0.6
    if skew is not None:
        sk = max(-1, min(1, skew / 3)) * 0.4
        s += -sk if role == "support" else sk
    s += 0.5 * share
    if (role == "support" and net_k < 0) or (role == "resistance" and net_k > 0):
        s += 0.3
    touch, held = reaction(candles, lvl, band)
    if touch >= 2:
        s += (held / touch - 0.5)
    if s >= 0.4:
        lean = "HOLD"
        call = "BUY" if role == "support" else "SELL"
    elif s <= -0.4:
        lean = "VIOLATE"
        call = "SELL" if role == "support" else "BUY"
    else:
        lean, call = ("HOLD" if s >= 0 else "VIOLATE"), "NO TRADE"
    conf = int(min(80, 50 + abs(s) * 25))
    if role == "support":
        inv = f"15m close below {lvl - band:.1f}" if lean == "HOLD" else f"15m close back above {lvl + band:.1f}"
    else:
        inv = f"15m close above {lvl + band:.1f}" if lean == "HOLD" else f"15m close back below {lvl - band:.1f}"
    return dict(role=role, call=call, lean=lean, confidence=conf, invalidation=inv,
                reactions=f"{held}/{touch}")


def next_run(now):
    h, m = map(int, CFG["asian_open_utc"].split(":"))
    mins = h * 60 + m + CFG["analysis_delay_minutes"]
    d = now.date()
    while True:
        t = dt.datetime.combine(d, dt.time(0), UTC) + dt.timedelta(minutes=mins)
        if t > now and t.weekday() < 5:
            return t
        d += dt.timedelta(days=1)


def build():
    now = dt.datetime.now(UTC)
    spot, rows = chain()
    call, put, net, oi, skew, strikes = gex(spot, rows)
    gld_c, xau_c = yahoo(CFG["options_symbol"]), yahoo(CFG["xau_symbol"])
    xau_spot = xau_c[-1]["c"]
    ratio = None
    if gld_c:
        t0 = gld_c[-1]["t"]
        near = min(xau_c, key=lambda x: abs(x["t"] - t0))
        if abs(near["t"] - t0) <= 1800:
            ratio = gld_c[-1]["c"] / near["c"]
    ratio = ratio or spot / xau_spot  # GLD per 1 XAUUSD; fallback if same-moment ratio unavailable
    flip = flip_level(net, strikes, spot)
    total = sum(net.values())
    above = [k for k in strikes if k >= spot and call.get(k)]
    below = [k for k in strikes if k <= spot and put.get(k)]
    call_wall = max(above, key=lambda k: call[k]) if above else None
    put_wall = max(below, key=lambda k: put[k]) if below else None
    top = sorted(strikes, key=lambda k: abs(net[k]), reverse=True)[:CFG["top_n"]]
    picks = sorted(set(top) | {k for k in (call_wall, put_wall) if k}, reverse=True)
    maxabs = max(abs(v) for v in net.values()) or 1
    band = xau_spot * CFG["band_pct"] / 100
    table = []
    for k in picks:
        x = round(k / ratio, 2)
        if flip is None:
            regime = "positive" if total >= 0 else "negative"
        else:
            regime = "positive" if k > flip else "negative"
        j = judge(x, xau_spot, regime, skew.get(k), abs(net[k]) / maxabs, net[k], xau_c, band)
        table.append(dict(strike=k, xau=x, oi=int(oi[k]), net_gex=round(net[k]),
                          regime=regime, iv_skew=skew.get(k), **j))
    try:
        paxg = float(get("https://data-api.binance.vision/api/v3/ticker/price",
                         params={"symbol": "PAXGUSDT"})["price"])
        offset = round(xau_spot - paxg, 2)
    except Exception:
        offset = 0
    return {
        "generated_at": now.isoformat(), "valid_until": next_run(now).isoformat(),
        "warning": None, "gld_spot": spot, "xau_spot": round(xau_spot, 2),
        "ratio": ratio, "paxg_offset": offset,
        "flip_gld": round(flip, 2) if flip else None,
        "flip_xau": round(flip / ratio, 2) if flip else None,
        "call_wall_xau": round(call_wall / ratio, 2) if call_wall else None,
        "put_wall_xau": round(put_wall / ratio, 2) if put_wall else None,
        "total_gex": round(total), "skew_25d": skew_25d(rows),
        "regime_now": "positive" if (flip is None and total >= 0) or (flip is not None and spot > flip) else "negative",
        "key_levels": key_levels(xau_c),
        "candles": [{"t": x["t"], "o": round(x["o"], 2), "h": round(x["h"], 2),
                     "l": round(x["l"], 2), "c": round(x["c"], 2)} for x in xau_c[-480:]],
        "ladder": [{"strike": k, "xau": round(k / ratio, 2), "net_gex": round(net[k])}
                   for k in sorted(strikes, reverse=True) if abs(k / spot - 1) <= 0.06],
        "table": table,
        "note": "Dealer sign assumed: calls long gamma, puts short gamma (OI based). GLD proxy, CBOE delayed data.",
    }


if __name__ == "__main__":
    try:
        data = build()
    except Exception as e:
        print("build failed:", e, file=sys.stderr)
        if not OUT.exists():
            sys.exit(1)
        data = json.loads(OUT.read_text())
        data["warning"] = f"Data refresh failed, showing previous levels ({type(e).__name__})."
    OUT.write_text(json.dumps(data, indent=1).replace("NaN", "null").replace("Infinity", "null"))
    print("wrote", OUT)
