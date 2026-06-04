"""Brand share-of-voice across the kefir / cultured-dairy competitor set, via Arctic Shift.

Same Reddit pipeline as fetch_reddit_arctic.py but ONE query per competitor
brand. Output is keyed on brand so the dashboard can render a stacked-area
chart of weekly mentions — does Lifeway hold mindshare as the category grows,
or do the yogurt / Greek-style players capture the gut-health conversation?

Brand set:
  - lifeway            (own brand — included so the chart shows relative share)
  - chobani            (Greek yogurt category leader, also makes drinkable)
  - fage               (Greek yogurt premium)
  - siggi's            (Icelandic skyr — high-protein adjacency)
  - maple hill         (grass-fed / premium dairy)
  - wallaby            (Australian-style yogurt / kefir-adjacent)

Same wall-clock deadline; uses core subs from config/reddit_subreddits.csv.

Output: data/competitor_mentions_weekly.csv (week, brand, post_count + sentiment).
"""
from __future__ import annotations

import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _arctic import (  # noqa: E402
    fetch_one, iso_to_epoch, apply_filters, classify_sentiment,
    ARCTIC_BASE, ARCTIC_COMMENTS,
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_CSV = PROJECT_ROOT / "config" / "reddit_subreddits.csv"
OUT_CSV = PROJECT_ROOT / "data" / "competitor_mentions_weekly.csv"

# (display_brand, query). One query per brand keeps the substring match clean.
COMPETITORS: list[tuple[str, str]] = [
    ("Lifeway",      "lifeway"),
    ("Chobani",      "chobani"),
    ("Fage",         "fage"),
    ("Siggi's",      "siggi"),
    ("Maple Hill",   "maple hill"),
    ("Wallaby",      "wallaby"),
]
# 6 brands × 2 endpoints × core subs in 360s. Title + selftext only; comments
# endpoint is too heavily rate-limited to fold into a 6-brand sweep.
WALL_CLOCK_DEADLINE_SEC = 360.0
PER_BRAND_PAUSE_SEC     = 1.5


def main() -> int:
    if not CONFIG_CSV.exists():
        print(f"  [error] missing {CONFIG_CSV}", file=sys.stderr)
        return 1

    subs_df = pd.read_csv(CONFIG_CSV)
    # Narrow to core-priority subs — keeps requests reasonable across 6 brands.
    if "priority" in subs_df.columns:
        core = subs_df[subs_df["priority"].astype(str).str.lower() == "core"]
        subreddits = core["subreddit"].dropna().astype(str).tolist()
    else:
        subreddits = subs_df["subreddit"].dropna().astype(str).tolist()
    print(
        f"  fetching {len(COMPETITORS)} brands × {len(subreddits)} core subs "
        f"(36mo, title+body, {int(WALL_CLOCK_DEADLINE_SEC)}s deadline)"
    )

    end = datetime.today()
    start = end - timedelta(days=365 * 3)
    s_epoch = iso_to_epoch(start.strftime("%Y-%m-%d"))
    e_epoch = iso_to_epoch(end.strftime("%Y-%m-%d"))

    deadline = time.time() + WALL_CLOCK_DEADLINE_SEC
    all_rows: list[dict] = []
    deadline_hit = False
    for brand_display, query in COMPETITORS:
        if deadline_hit: break
        brand_rows: list[dict] = []
        for field, endpoint, label, max_p in [
            ("title",    ARCTIC_BASE, "title", 15),
            ("selftext", ARCTIC_BASE, "body",  10),
        ]:
            for sub in subreddits:
                if time.time() >= deadline:
                    print(f"  [warn] {int(WALL_CLOCK_DEADLINE_SEC)}s deadline hit during {brand_display}/{label} (sub {sub})")
                    deadline_hit = True
                    break
                rows = fetch_one(
                    sub, query, s_epoch, e_epoch, field=field,
                    max_pages=max_p, deadline=deadline, page_sleep=0.6,
                    endpoint=endpoint,
                )
                brand_rows.extend(rows)
            if deadline_hit: break
        # Apply bot/short-body filters before tagging brand
        filtered = apply_filters(brand_rows, min_body_chars=30, word_boundary_query=query)
        for r in filtered:
            r["brand"] = brand_display
        all_rows.extend(filtered)
        print(f"  · {brand_display:18s}  +{len(filtered)} (filtered from {len(brand_rows)} raw)")
        time.sleep(PER_BRAND_PAUSE_SEC)

    if not all_rows:
        out = pd.DataFrame(columns=["week", "brand", "post_count", "pos_count", "neg_count", "neu_count"])
    else:
        df = pd.DataFrame(all_rows).drop_duplicates(subset=["brand", "subreddit", "item_id", "kind"])
        df["dt"] = pd.to_datetime(df["created_utc"], unit="s", utc=True)
        df["week"] = df["dt"].dt.to_period("W-SUN").dt.end_time.dt.strftime("%Y-%m-%d")
        df["sentiment"] = df["body"].apply(classify_sentiment) if "body" in df.columns else "neutral"
        total = df.groupby(["week", "brand"]).size().reset_index(name="post_count")
        pos = df[df["sentiment"] == "positive"].groupby(["week", "brand"]).size().reset_index(name="pos_count")
        neg = df[df["sentiment"] == "negative"].groupby(["week", "brand"]).size().reset_index(name="neg_count")
        neu = df[df["sentiment"] == "neutral"].groupby(["week", "brand"]).size().reset_index(name="neu_count")
        out = total.merge(pos, on=["week", "brand"], how="left") \
                   .merge(neg, on=["week", "brand"], how="left") \
                   .merge(neu, on=["week", "brand"], how="left")
        for c in ("pos_count", "neg_count", "neu_count"):
            out[c] = out[c].fillna(0).astype(int)
        out = out.sort_values(["week", "brand"]).reset_index(drop=True)

    OUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(OUT_CSV, index=False)
    if not out.empty:
        per_brand = out.groupby("brand")["post_count"].sum().sort_values(ascending=False)
        print("\n  brand totals (36mo, core subs):")
        for b, v in per_brand.items():
            print(f"    {b:18s}  {v:>5d}")
    print(f"\n  ✓ wrote {OUT_CSV.name}  rows={len(out)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
