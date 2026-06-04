"""News article fetcher — GDELT ArtList primary, Google News RSS fallback.

Pulls article-level rows so the dashboard can classify each headline by topic.
ArtList mode gives title + URL + source domain + seendate per article.

Queries cover LWAY-specific (ticker, Danone, family proxy fight) + category
(kefir, gut health) per the dual thesis.

Output: data/news_articles.csv with columns:
  date, headline, url, source, topic

Topics (keyword-classified, first-match-wins; takeover + governance checked
FIRST so a Danone/Smolyansky headline tags correctly rather than as financial):
  takeover | governance | financial | launch | category | health | other
"""
from __future__ import annotations

import sys
import time
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import requests

PROJECT_ROOT = Path(__file__).resolve().parent.parent
OUT_CSV = PROJECT_ROOT / "data" / "news_articles.csv"

GDELT_URL = "https://api.gdeltproject.org/api/v2/doc/doc"
UA = "lway-demand-dashboard/1.0 (+research)"

QUERIES = [
    '"Lifeway Foods"',
    '"LWAY stock"',
    '"Danone Lifeway"',
    '"Lifeway kefir"',
    '"Smolyansky"',
    '"kefir"',
]

# Order matters — first match wins. takeover + governance BEFORE financial so a
# Danone-bid or proxy-fight article tags correctly rather than as financial.
TOPIC_KEYWORDS: list[tuple[str, list[str]]] = [
    ("takeover", [
        "danone", "takeover", "acquisition", "acquire", "buyout", "tender offer",
        "withdraws", "withdrew", "proposal", "unsolicited", "premium", "bid",
        "stake", "23%", "cooperation agreement",
    ]),
    ("governance", [
        "proxy", "poison pill", "rights plan", "smolyansky", "board seat",
        "annual meeting", "shareholder vote", "control", "entrench",
        "dissident", "nominee", "governance", "family", "ceo julie",
        "edward smolyansky", "ludmila",
    ]),
    ("financial", [
        "earnings", "revenue", "quarter", "guidance", "cfo", "net sales",
        "shares", "analyst", "upgrade", "downgrade", "beats estimates",
        "misses estimates", "stock price", "margin", "ebitda", "outlook",
        "record sales", "dividend",
    ]),
    ("launch", [
        "launches", "debut", "new product", "expands", "rolls out",
        "available at", "now at", "rollout", "expansion", "new sku",
        "new flavor", "protein kefir", "distribution",
    ]),
    ("category", [
        "kefir", "cultured dairy", "fermented", "yogurt category",
        "drinkable yogurt", "category growth", "functional dairy",
    ]),
    ("health", [
        "probiotic", "gut health", "microbiome", "gut microbiome",
        "fermented food", "immune", "digestive", "wellness",
    ]),
]


def classify(headline: str) -> str:
    h = " " + headline.lower() + " "
    for topic, kws in TOPIC_KEYWORDS:
        for kw in kws:
            if kw in h:
                return topic
    return "other"


def fetch_gdelt(query: str, start: str, end: str) -> list[dict]:
    """ArtList mode — returns article-level rows."""
    params = {
        "query": query,
        "mode": "ArtList",
        "format": "json",
        "maxrecords": 250,
        "startdatetime": datetime.strptime(start, "%Y-%m-%d").strftime("%Y%m%d000000"),
        "enddatetime": datetime.strptime(end, "%Y-%m-%d").strftime("%Y%m%d235959"),
        "sort": "DateDesc",
    }
    time.sleep(1.0)  # GDELT asks for <1 req/5s per requester
    try:
        r = requests.get(GDELT_URL, params=params, headers={"User-Agent": UA}, timeout=45)
        r.raise_for_status()
    except requests.RequestException as exc:
        print(f"  [warn] gdelt {query!r}: {exc}")
        return []
    text_head = r.text[:200].lower()
    if "limit requests" in text_head or "too short" in text_head:
        print(f"  [warn] gdelt rate/query rejected for {query!r}: {r.text[:120]}")
        return []
    try:
        articles = r.json().get("articles", [])
    except ValueError:
        print(f"  [warn] gdelt non-json for {query!r}: {r.text[:120]}")
        return []
    out = []
    for a in articles:
        d_raw = a.get("seendate", "")
        try:
            d = datetime.strptime(d_raw[:8], "%Y%m%d").strftime("%Y-%m-%d")
        except (TypeError, ValueError):
            continue
        out.append({
            "date": d,
            "headline": (a.get("title") or "").strip(),
            "url": a.get("url", ""),
            "source": (a.get("domain") or "").lower().strip(),
        })
    return out


def fetch_googlenews_rss(query: str) -> list[dict]:
    """Google News RSS — last ~30d of articles per query. Fallback only."""
    url = (
        "https://news.google.com/rss/search?q="
        + urllib.parse.quote_plus(query)
        + "&hl=en-US&gl=US&ceid=US:en"
    )
    try:
        r = requests.get(url, headers={"User-Agent": UA}, timeout=20)
        r.raise_for_status()
    except requests.RequestException as exc:
        print(f"  [warn] gnews-rss {query!r}: {exc}")
        return []
    try:
        root = ET.fromstring(r.content)
    except ET.ParseError as exc:
        print(f"  [warn] gnews-rss parse fail {query!r}: {exc}")
        return []
    out = []
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        pub = (item.findtext("pubDate") or "").strip()
        src = (item.find("source") or ET.Element("source")).text or ""
        try:
            dt = datetime.strptime(pub, "%a, %d %b %Y %H:%M:%S %Z")
        except ValueError:
            try:
                dt = datetime.strptime(pub, "%a, %d %b %Y %H:%M:%S %z")
            except ValueError:
                continue
        out.append({
            "date": dt.strftime("%Y-%m-%d"),
            "headline": title,
            "url": link,
            "source": (src or "google.news").lower().strip(),
        })
    return out


def main() -> int:
    end = datetime.today()
    start = end - timedelta(days=365 * 2)  # 24-month window
    s_str = start.strftime("%Y-%m-%d")
    e_str = end.strftime("%Y-%m-%d")

    print(f"  fetching news: {s_str} → {e_str} ({len(QUERIES)} queries)")
    all_rows: list[dict] = []
    gdelt_hits = 0
    for q in QUERIES:
        rows = fetch_gdelt(q, s_str, e_str)
        gdelt_hits += len(rows)
        all_rows.extend(rows)
        print(f"  · gdelt {q:40s}  +{len(rows)} articles")

    if gdelt_hits == 0:
        print("  [warn] no GDELT hits across any query — falling back to Google News RSS")
        for q in QUERIES:
            rows = fetch_googlenews_rss(q)
            all_rows.extend(rows)
            print(f"  · gnews {q:40s}  +{len(rows)} articles")

    if not all_rows:
        df = pd.DataFrame(columns=["date", "headline", "url", "source", "topic"])
    else:
        df = pd.DataFrame(all_rows)
        df = df[df["headline"].str.len() > 5].copy()
        df = df.drop_duplicates(subset=["url"]).reset_index(drop=True)
        df["topic"] = df["headline"].apply(classify)
        df = df.sort_values("date", ascending=False).reset_index(drop=True)

    OUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(OUT_CSV, index=False)
    print(f"\n  ✓ wrote {OUT_CSV.name}  rows={len(df)}")
    if not df.empty:
        topic_counts = df["topic"].value_counts()
        for t, c in topic_counts.items():
            print(f"    {t:14s}  {c}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
