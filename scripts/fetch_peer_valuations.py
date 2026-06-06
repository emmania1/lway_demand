#!/usr/bin/env python3
"""Fetch valuation multiples (EV/Sales, EV/EBITDA, P/E, market cap) for LWAY plus
the valuation-peer tickers listed in data/competitor_landscape.csv, via yfinance.

Writes data/peer_valuations.csv. Tagged "Green" (free + automatable). Safe to
re-run; a ticker that fails fetch is written with empty multiples (renders "—").

Run:  python3 scripts/fetch_peer_valuations.py
"""
import csv
import datetime as dt
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE.parent / "data"
LANDSCAPE = DATA / "competitor_landscape.csv"
OUT = DATA / "peer_valuations.csv"

try:
    import yfinance as yf
except Exception as e:  # pragma: no cover
    print(f"yfinance not available: {e}", file=sys.stderr)
    sys.exit(1)


def main() -> None:
    if not LANDSCAPE.exists():
        print(f"missing {LANDSCAPE}", file=sys.stderr)
        sys.exit(1)
    tickers = []
    with open(LANDSCAPE, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            tk = (r.get("ticker") or "").strip()
            rel = (r.get("relation") or "").strip()
            if tk and rel in ("self", "valuation_peer"):
                tickers.append((tk, (r.get("company") or tk).strip()))

    asof = dt.date.today().isoformat()
    rows = []
    for tk, name in tickers:
        rec = {"ticker": tk, "company": name, "ev_sales": "", "ev_ebitda": "",
               "pe": "", "market_cap": "", "as_of": asof}
        # yfinance .info intermittently returns empty — retry up to 3x before
        # giving up; a permanently-blank ticker is written empty (renders "—").
        info = {}
        for attempt in range(3):
            try:
                info = yf.Ticker(tk).info or {}
            except Exception as e:
                print(f"  {tk:5s} attempt {attempt + 1} error: {type(e).__name__}: {str(e)[:60]}", file=sys.stderr)
                info = {}
            if info.get("enterpriseToRevenue") or info.get("enterpriseToEbitda") or info.get("marketCap"):
                break
            time.sleep(1.5)
        rec["ev_sales"] = info.get("enterpriseToRevenue") or ""
        rec["ev_ebitda"] = info.get("enterpriseToEbitda") or ""
        rec["pe"] = info.get("trailingPE") or ""
        rec["market_cap"] = info.get("marketCap") or ""
        status = "ok" if (rec["ev_ebitda"] or rec["ev_sales"]) else "BLANK -> renders '—'"
        print(f"  {tk:5s} EV/S={rec['ev_sales']} EV/EBITDA={rec['ev_ebitda']} PE={rec['pe']}  [{status}]")
        rows.append(rec)

    with open(OUT, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["ticker", "company", "ev_sales", "ev_ebitda",
                                          "pe", "market_cap", "as_of"])
        w.writeheader()
        w.writerows(rows)
    print(f"wrote {OUT} ({len(rows)} rows, as of {asof})")


if __name__ == "__main__":
    main()
