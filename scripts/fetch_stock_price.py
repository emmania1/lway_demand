"""LWAY daily close (3-year history) via yfinance + curl_cffi Chrome impersonation.

Yahoo blocks many cloud / datacenter IPs against the default yfinance session,
so we wrap requests in curl_cffi's Chrome impersonation to look like a real
browser.

Output: data/lway_stock.csv with columns: date, close, volume.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import yfinance as yf

try:
    from curl_cffi import requests as curl_requests
    _SESSION = curl_requests.Session(impersonate="chrome")
except Exception:  # noqa: BLE001
    _SESSION = None

TICKER = "LWAY"
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
OUT_CSV = DATA_DIR / "lway_stock.csv"


def _ticker(symbol: str) -> yf.Ticker:
    if _SESSION is not None:
        return yf.Ticker(symbol, session=_SESSION)
    return yf.Ticker(symbol)


def main() -> int:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    end = datetime.today()
    start = end - timedelta(days=365 * 3)
    s_str = start.strftime("%Y-%m-%d")
    e_str = (end + timedelta(days=1)).strftime("%Y-%m-%d")

    print(f"  fetching {TICKER} daily close: {s_str} → {e_str}")
    try:
        t = _ticker(TICKER)
        df = t.history(start=s_str, end=e_str, auto_adjust=True, actions=False)
    except Exception as exc:  # noqa: BLE001
        print(f"  [error] yfinance failed: {exc}", file=sys.stderr)
        return 1

    if df.empty:
        print(
            f"  [error] yfinance returned empty frame for {TICKER}. "
            "Yahoo likely blocked this IP or curl_cffi impersonation isn't installed.",
            file=sys.stderr,
        )
        return 1

    df = df.reset_index()[["Date", "Close", "Volume"]].copy()
    df.columns = ["date", "close", "volume"]
    df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")
    df["close"] = df["close"].astype(float).round(4)
    df["volume"] = df["volume"].astype("int64")

    df.to_csv(OUT_CSV, index=False)
    print(f"  ✓ wrote {OUT_CSV.name}  rows={len(df)}  range={df['date'].min()}..{df['date'].max()}")

    # Quick sanity print of the key takeover-timeline anchor dates so any
    # misalignment surfaces immediately rather than silently producing wrong KPIs.
    for label, target in [
        ("2024-11-15", "Danone raised bid to $27"),
        ("2025-09-18", "Danone withdrew proposal"),
        ("2026-04-29", "Q1 2026 print"),
    ]:
        row = df[df["date"] == label]
        if not row.empty:
            print(f"  · {label} ({target}): close=${row.iloc[0]['close']:.2f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
