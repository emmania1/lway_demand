# Lifeway Foods (LWAY) Demand & Control Dashboard

A self-updating demand-intelligence dashboard for LWAY (NASDAQ: LWAY), built
around a **dual thesis**: (1) is the kefir / cultured-dairy category momentum
real and durable, and (2) what happens to the **Danone takeover overhang +
Smolyansky family control battle** now that Danone's bid is withdrawn but its
~23% stake (and a 2026 cooperation agreement) remain. Single Python generator
→ static `index.html` → hostable on GitHub Pages.

Structure adopted from the sibling `vitl_demand` dashboard (same
config-driven Python→HTML pattern, Chart.js, standalone fetchers). The
commodity proxy is **milk** (USDA Class III + BLS CPI milk) in place of VITL's
eggs.

## What it tracks

- **The Situation** (top framing) — three columns: Danone overhang · family
  control / governance battle · category momentum
- **Quick Read** (Section 00) — KPI cards: price vs the withdrawn $27 Danone
  bid, premium/discount to bid, family + Danone ownership, consecutive
  quarters of YoY growth + a compressed takeover/governance timeline
- **The Setup** (Section 01) — Control vs Value: strategic value to an
  acquirer vs entrenchment (family stake, poison pill); stock indexed against
  the $25 / $27 bid anchors over time
- **Stock & News** (Section 02) — event-annotated price chart (Danone bids,
  withdrawal, cooperation agreement, earnings beats, proxy filings) + topic
  mix over time + article log
- **The Milk Market** (Section 03) — USDA Class III milk + BLS CPI milk; the
  input-cost line that drives gross-margin pressure for a dairy processor
- **Brand & Community** (Section 04) — kefir / cultured-dairy share-of-voice
  vs a competitor set, Reddit mention volume, recent posts + videos feeds
- **Financial Momentum** (Section 05) — quarterly revenue growth streak,
  gross-margin trajectory, valuation snapshot
- **Demand vs Stock** (Section 06) — z-scored composite consumer demand
  (weekly Reddit + monthly YouTube) vs LWAY daily close

## Data pipeline

Each fetcher is a standalone, idempotent Python script in `scripts/`.
All outputs land in `data/` as CSVs. Analyst-maintained seed CSVs (events,
financials, valuation, ownership, catalysts) also live in `data/` and are
edited by hand — the generator reconciles real fetched data over them.

| Fetcher                        | Source                              | Auth              | Output                                |
|--------------------------------|-------------------------------------|-------------------|---------------------------------------|
| `fetch_stock_price.py`         | yfinance (curl_cffi Chrome session) | none              | `data/lway_stock.csv`                 |
| `fetch_reddit_arctic.py`       | Arctic Shift public archive         | none              | `data/reddit_mentions_weekly.csv`     |
| `fetch_competitor_mentions.py` | Arctic Shift (one query per brand)  | none              | `data/competitor_mentions_weekly.csv` |
| `fetch_google_news.py`         | GDELT ArtList → Google News RSS     | none              | `data/news_articles.csv`              |
| `fetch_milk_prices.py`         | BLS public API + USDA Class III seed| none              | `data/bls_cpi_milk_monthly.csv` + `data/usda_milk_monthly.csv` |
| `fetch_youtube.py`             | YouTube Data API v3                 | `YOUTUBE_API_KEY` | `data/youtube_monthly.csv`            |

All Reddit fetchers honor a wall-clock deadline (partial data on timeout is
fine). The YouTube fetcher is the only one that needs credentials; it no-ops
cleanly (exit 0) when `YOUTUBE_API_KEY` is unset.

## Setup

```bash
# 1. Create venv + install deps
make install

# 2. (Optional) Populate YouTube credentials
cp .env.example .env   # then add YOUTUBE_API_KEY=...

# 3. Refresh all data + regenerate dashboard
make refresh-data        # full pipeline incl. YouTube
make refresh-fast        # everything except YouTube
make generate            # regenerate index.html from existing CSVs only
```

The VITL venv (`../vitl_demand/venv`) has identical deps and can be reused for
quick runs if you don't want a second virtualenv.

## Anchor facts (as of 2026-06-03 — verify before relying on KPIs)

- Danone bids: **$25.00** (Sep 2024) → **$27.00** (Nov 15 2024); both rejected
- Poison pill adopted Nov 6 2024; **extended Oct 29 2025 → expires Oct 29 2026**
- **Danone withdrew its proposal Sep 18 2025**; still holds ~23%; signed a
  cooperation agreement reflected in the 2026 board slate
- Smolyansky family (Edward + Ludmila) ≈ **26.17%**; Edward running a proxy
  campaign vs CEO Julie Smolyansky
- **2026 Annual Meeting: June 17 2026** (near-term governance catalyst)
- Q1 2026 (ended Mar 31 2026): net sales **$63.0M (+37%)**, GM **27.5%**
  (+360bps), net income $4.7M; **26 consecutive quarters** of YoY growth;
  FY27 Adj-EBITDA target **$45–50M**; Waukesha WI capacity doubling by Q4 2026

These are baked into `generate_lway_dashboard.py` constants and the seed CSVs.

## Project layout

```
lway_demand/
├── config/
│   ├── products.csv               # kefir / soft cheese / probiotic SKUs
│   ├── retailers.csv              # distribution banners
│   └── reddit_subreddits.csv      # gut-health / kefir / investing subs
├── scripts/
│   ├── _arctic.py                 # shared Arctic Shift pager
│   ├── fetch_stock_price.py
│   ├── fetch_reddit_arctic.py
│   ├── fetch_competitor_mentions.py
│   ├── fetch_google_news.py
│   ├── fetch_milk_prices.py
│   ├── fetch_youtube.py
│   └── generate_lway_dashboard.py # builds index.html
├── data/                          # fetcher outputs + analyst seed CSVs
├── index.html                     # the dashboard
├── Makefile
├── requirements.txt
└── .env.example
```
