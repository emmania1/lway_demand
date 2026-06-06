"""Milk market prices — BLS CPI milk (real) + USDA Class III seed cross-check.

Milk is the commodity input proxy for a dairy processor. Kefir's true input is
USDA Class II (soft/cultured products); Class III (the cheese grade) is fetched
here as a closely-correlated proxy until a live Class II series is wired. Lower
milk $/cwt = gross-margin tailwind; BLS CPI retail milk is the consumer cross-check.

Real-source attempts:
  1. BLS public API for CPI milk series APU0000709112
     ("Milk, fresh, whole, fortified, per gal") — no key required, 25 req/day
     → monthly retail milk price
  2. USDA Class III announced price → published monthly by USDA AMS Dairy
     Programs; not wired to a live endpoint in this pass. Seed CSV preserved.

Behavior: writes data/bls_cpi_milk_monthly.csv from the live BLS fetch. If the
USDA Class III seed already exists it is preserved (the analyst maintains it by
hand from the monthly USDA announcement until a live fetcher is wired).

Outputs:
  data/bls_cpi_milk_monthly.csv  (month, price, source)
  data/usda_milk_monthly.csv     (month, class_iii_price, source)  [seed-preserved]
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

import pandas as pd
import requests

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"

BLS_SERIES = "APU0000709112"  # Milk, fresh, whole, fortified, per gal (US city avg)
BLS_API = f"https://api.bls.gov/publicAPI/v2/timeseries/data/{BLS_SERIES}"
UA = "lway-demand-dashboard/1.0 (+research)"


def fetch_bls_cpi_milk() -> pd.DataFrame:
    """BLS public API — no auth, no key. Returns latest ~3-year monthly data."""
    end_year = datetime.today().year
    payload = {"seriesid": [BLS_SERIES], "startyear": str(end_year - 3),
               "endyear": str(end_year)}
    try:
        r = requests.post(BLS_API, json=payload, timeout=30, headers={"User-Agent": UA})
        r.raise_for_status()
        j = r.json()
    except (requests.RequestException, ValueError) as exc:
        print(f"  [warn] BLS CPI milk fetch failed: {exc}", file=sys.stderr)
        return pd.DataFrame()

    if j.get("status") != "REQUEST_SUCCEEDED":
        print(f"  [warn] BLS returned non-success: {j.get('message', j.get('status'))}", file=sys.stderr)
        return pd.DataFrame()

    series = j.get("Results", {}).get("series", [])
    if not series:
        return pd.DataFrame()

    rows = []
    for s in series:
        for d in s.get("data", []):
            year = d.get("year"); period = d.get("period", "")
            if not period.startswith("M"):
                continue
            mo = period[1:].zfill(2)
            try:
                price = float(d.get("value"))
            except (TypeError, ValueError):
                continue
            rows.append({"month": f"{year}-{mo}", "price": price, "source": f"BLS {BLS_SERIES}"})
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows).sort_values("month").reset_index(drop=True)
    return df


def main() -> int:
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    # ── BLS CPI milk monthly (real fetch, no key) ─────────────────────────────
    print(f"  fetching BLS CPI milk ({BLS_SERIES}) ...")
    cpi = fetch_bls_cpi_milk()
    if not cpi.empty:
        out = DATA_DIR / "bls_cpi_milk_monthly.csv"
        cpi.to_csv(out, index=False)
        print(f"  ✓ wrote {out.name}  rows={len(cpi)}  range={cpi['month'].min()}..{cpi['month'].max()}")
    else:
        print("  [warn] BLS empty — skipping CPI cross-check write")

    # ── USDA Class III — preserve seeded data ─────────────────────────────────
    classiii = DATA_DIR / "usda_milk_monthly.csv"
    if classiii.exists():
        df = pd.read_csv(classiii)
        print(f"  · usda_milk_monthly.csv  preserved  rows={len(df)}  (seed; USDA AMS fetcher TBD)")
    else:
        print(f"  [warn] {classiii.name} missing — no seed and USDA AMS fetcher TBD", file=sys.stderr)

    return 0


if __name__ == "__main__":
    sys.exit(main())
