"""Lifeway Foods Reddit mention volume (36-month weekly), title+body+comments.

Hits the Arctic Shift public archive at arctic-shift.photon-reddit.com.
45-second wall-clock deadline per stage — partial data on timeout is fine.

Subreddits come from config/reddit_subreddits.csv.
Primary query: "lifeway" (substring search; catches "Lifeway kefir", "LWAY",
"Lifeway Foods"). A secondary "kefir" category pass tracks the broader
cultured-dairy category enthusiasm in the gut-health subs — the demand half of
the dual thesis (is the category itself accelerating, independent of the brand).

Output:
  data/reddit_mentions_weekly.csv   (week, subreddit, query, post_count + sentiment)
  data/reddit_posts_recent.csv      (recent real posts/comments feed)
  data/kefir_category_weekly.csv    (category-wide kefir mention volume)
"""
from __future__ import annotations

import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

# Local helper (same dir)
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _arctic import (  # noqa: E402
    fetch_one, iso_to_epoch, weekly_counts, apply_filters,
    classify_sentiment, ARCTIC_BASE, ARCTIC_COMMENTS,
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_CSV = PROJECT_ROOT / "config" / "reddit_subreddits.csv"
OUT_CSV = PROJECT_ROOT / "data" / "reddit_mentions_weekly.csv"
CATEGORY_OUT_CSV = PROJECT_ROOT / "data" / "kefir_category_weekly.csv"
POSTS_CSV = PROJECT_ROOT / "data" / "reddit_posts_recent.csv"

QUERY = "lifeway"
# 3-stage budget: title posts (45s) → body posts (45s) → comments (60s).
TITLE_DEADLINE_SEC   = 45.0
BODY_DEADLINE_SEC    = 45.0
COMMENT_DEADLINE_SEC = 60.0

# Kefir-category secondary pass: gut-health / fermentation subs, all 3 endpoints.
CATEGORY_SUBS = ["Kefir", "fermentation", "Probiotics", "guthealth", "Microbiome"]


def _fetch_three_endpoint(subreddits: list[str], query: str,
                          s_epoch: int, e_epoch: int,
                          title_deadline: float, body_deadline: float,
                          comment_deadline: float,
                          word_boundary_filter: str | None = None) -> list[dict]:
    """Sweep posts (title field) → posts (selftext field) → comments (body
    field) across the sub list. All three apply the standard bot/short-body
    filters via apply_filters().
    """
    all_rows: list[dict] = []

    # 1) title pass
    for sub in subreddits:
        if time.time() >= title_deadline: break
        rows = fetch_one(sub, query, s_epoch, e_epoch, field="title",
                         deadline=title_deadline, endpoint=ARCTIC_BASE)
        if rows: print(f"    · r/{sub:24s} title={len(rows)}")
        all_rows.extend(rows)

    # 2) selftext (post body) pass — narrower max_pages because long bodies are rarer
    for sub in subreddits:
        if time.time() >= body_deadline: break
        rows = fetch_one(sub, query, s_epoch, e_epoch, field="selftext",
                         max_pages=20, deadline=body_deadline, endpoint=ARCTIC_BASE)
        if rows: print(f"    · r/{sub:24s} body={len(rows)}")
        all_rows.extend(rows)

    # 3) comments pass — comments endpoint times out on big subs (millions of
    # comments); a tight max_pages cap keeps even timeout-prone subs fast.
    for sub in subreddits:
        if time.time() >= comment_deadline: break
        rows = fetch_one(sub, query, s_epoch, e_epoch, field="body",
                         max_pages=3, deadline=comment_deadline,
                         endpoint=ARCTIC_COMMENTS, page_sleep=1.0)
        if rows: print(f"    · r/{sub:24s} cmts={len(rows)}")
        all_rows.extend(rows)

    # Filter bots + short comments (+ optional word-boundary regex)
    filtered = apply_filters(all_rows, min_body_chars=30,
                             word_boundary_query=word_boundary_filter)
    dropped = len(all_rows) - len(filtered)
    if dropped > 0:
        print(f"    · filters dropped {dropped} bot/short/non-matching rows")
    return filtered


def run_category_pass() -> None:
    """Kefir-category enthusiasm tracker. 36mo, gut-health subs, all 3
    endpoints. Word-boundary regex requires \\bkefir\\b in body text when
    present. This is the demand-side signal — category momentum independent
    of the LWAY brand."""
    end = datetime.today()
    start = end - timedelta(days=365 * 3)
    s_epoch = iso_to_epoch(start.strftime("%Y-%m-%d"))
    e_epoch = iso_to_epoch(end.strftime("%Y-%m-%d"))

    now = time.time()
    rows = _fetch_three_endpoint(
        CATEGORY_SUBS, "kefir", s_epoch, e_epoch,
        title_deadline=now + 30, body_deadline=now + 55, comment_deadline=now + 90,
        word_boundary_filter="kefir",
    )
    if not rows:
        print("  · category pass: 0 hits after filters — seed CSV preserved")
        return

    df = pd.DataFrame(rows).drop_duplicates(subset=["subreddit", "item_id", "kind"])
    df["dt"] = pd.to_datetime(df["created_utc"], unit="s", utc=True)
    df["week"] = df["dt"].dt.to_period("W-SUN").dt.end_time.dt.strftime("%Y-%m-%d")
    weekly = df.groupby("week").size().reset_index(name="post_count")
    weekly["subreddits"] = ",".join("r/" + s for s in CATEGORY_SUBS) + " (title+body+comments)"

    # Don't clobber a richer seeded series with a tiny real-fetch result —
    # Arctic Shift's comments endpoint is heavily rate-limited.
    MIN_OVERWRITE_ROWS = 10
    if CATEGORY_OUT_CSV.exists():
        try:
            existing = pd.read_csv(CATEGORY_OUT_CSV)
            if len(existing) > len(weekly) and len(weekly) < MIN_OVERWRITE_ROWS:
                print(
                    f"  · category pass: real fetch returned {len(weekly)} rows < seed's "
                    f"{len(existing)} — preserving seed"
                )
                return
        except Exception:
            pass

    weekly.to_csv(CATEGORY_OUT_CSV, index=False)
    print(f"  · category pass: ✓ wrote {CATEGORY_OUT_CSV.name}  rows={len(weekly)}  total={weekly['post_count'].sum()}")


def main() -> int:
    if not CONFIG_CSV.exists():
        print(f"  [error] missing {CONFIG_CSV}", file=sys.stderr)
        return 1

    subs_df = pd.read_csv(CONFIG_CSV)
    subreddits = subs_df["subreddit"].dropna().astype(str).tolist()
    print(f"  fetching {len(subreddits)} subs × query={QUERY!r} (36mo, title+body+comments)")

    end = datetime.today()
    start = end - timedelta(days=365 * 3)
    s_epoch = iso_to_epoch(start.strftime("%Y-%m-%d"))
    e_epoch = iso_to_epoch(end.strftime("%Y-%m-%d"))

    now = time.time()
    rows = _fetch_three_endpoint(
        subreddits, QUERY, s_epoch, e_epoch,
        title_deadline=now + TITLE_DEADLINE_SEC,
        body_deadline=now + TITLE_DEADLINE_SEC + BODY_DEADLINE_SEC,
        comment_deadline=now + TITLE_DEADLINE_SEC + BODY_DEADLINE_SEC + COMMENT_DEADLINE_SEC,
        word_boundary_filter="lifeway",
    )

    weekly = weekly_counts(rows)
    OUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    weekly.to_csv(OUT_CSV, index=False)
    total = weekly["post_count"].sum() if not weekly.empty else 0
    print(
        f"\n  ✓ wrote {OUT_CSV.name}  rows={len(weekly)}  total={total}  "
        f"weeks={weekly['week'].nunique() if not weekly.empty else 0}"
    )

    # ── Recent posts feed — write actual titles + bodies + URLs ────────────
    if rows:
        posts_df = pd.DataFrame(rows).drop_duplicates(subset=["subreddit", "item_id", "kind"])
        posts_df["date"] = pd.to_datetime(posts_df["created_utc"], unit="s", utc=True).dt.strftime("%Y-%m-%d")
        if "sentiment" not in posts_df.columns:
            posts_df["sentiment"] = posts_df["body"].apply(classify_sentiment)
        def _excerpt(row):
            if row.get("kind") == "post" and row.get("title"):
                return str(row["title"])
            b = str(row.get("body") or "").strip().replace("\n", " ")
            return b[:200] + ("…" if len(b) > 200 else "")
        posts_df["excerpt"] = posts_df.apply(_excerpt, axis=1)
        out = posts_df[["date", "subreddit", "kind", "author", "excerpt", "url",
                        "score", "num_comments", "sentiment"]].sort_values("date", ascending=False)
        out.head(80).to_csv(POSTS_CSV, index=False)
        print(f"  ✓ wrote {POSTS_CSV.name}  rows={min(80, len(out))}  (most-recent Lifeway posts/comments)")

    # Kefir category enthusiasm (demand-side signal)
    print("\n  ── secondary pass: kefir-category enthusiasm ──")
    run_category_pass()
    return 0


if __name__ == "__main__":
    sys.exit(main())
