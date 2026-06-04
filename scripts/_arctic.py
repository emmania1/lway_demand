"""Shared Arctic Shift helper used by fetch_reddit_arctic.py and
fetch_competitor_mentions.py. Mirrors the pager / wall-clock-deadline
pattern from the sibling vitl_demand dashboard exactly — title-only
matching, paginate by `after`, cap at 100 rows per page, sleep
between pages, hard wall-clock deadline.

This file is private to scripts/ (underscore prefix). Don't import it
from the generator.
"""
from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from typing import Iterable

import pandas as pd
import requests

ARCTIC_BASE     = "https://arctic-shift.photon-reddit.com/api/posts/search"
ARCTIC_COMMENTS = "https://arctic-shift.photon-reddit.com/api/comments/search"
USER_AGENT = os.environ.get(
    "REDDIT_USER_AGENT", "lway-demand-dashboard/1.0 (+research)"
)

# Author allowlist filter — Arctic Shift returns `author` so we drop the
# obvious bot accounts. Lowercased comparison.
BOT_AUTHORS = {
    "automoderator", "remindmebot", "reminddebot", "savevideo",
    "videoinurl", "transcriberbot", "wikitextbot", "imagesofnetwork",
    "good_bot_bot", "haikubotinator", "removalbot", "of_patrol_bot",
    "stabbot", "user_simulator", "redditcomplaintsbot", "[deleted]",
}


def iso_to_epoch(d: str) -> int:
    return int(
        datetime.strptime(d, "%Y-%m-%d")
        .replace(tzinfo=timezone.utc)
        .timestamp()
    )


def fetch_one(
    sub: str,
    query: str,
    start_epoch: int,
    end_epoch: int,
    field: str = "title",
    max_pages: int = 40,
    deadline: float | None = None,
    page_sleep: float = 0.6,
    endpoint: str = ARCTIC_BASE,
) -> list[dict]:
    """Page through Arctic Shift /posts/search OR /comments/search.

    `endpoint` switches between posts (ARCTIC_BASE) and comments (ARCTIC_COMMENTS).
    For comments, pass field="body" instead of field="title".

    Returns rows with as much metadata as Arctic Shift returns: item_id,
    created_utc, subreddit, query, kind ("post"|"comment"), author, body.
    (body and author may be None when Arctic Shift omits them.)

    Stops paging when:
      - exhausted (data < 100 returned or after >= end_epoch)
      - max_pages reached
      - wall-clock deadline exceeded

    On HTTP 429/422: sleep 5s, retry once; on second failure bail for this
    (sub, query, endpoint) so the wider fetch doesn't grind to a halt.
    """
    kind = "comment" if endpoint == ARCTIC_COMMENTS else "post"
    rows: list[dict] = []
    after = start_epoch
    page = 0
    while after < end_epoch and page < max_pages:
        if deadline is not None and time.time() >= deadline:
            break
        params = {
            "subreddit": sub,
            field: query,
            "limit": 100,
            "after": after,
            "sort": "asc",
        }
        data = None
        for attempt in (1, 2):
            try:
                r = requests.get(
                    endpoint,
                    params=params,
                    headers={"User-Agent": USER_AGENT},
                    timeout=30,
                )
                # Arctic Shift uses 422 for "server-side timeout, slow down".
                # Treat 422 + 429 identically: one retry with backoff, then skip.
                if r.status_code in (422, 429):
                    if attempt == 1:
                        time.sleep(5.0)
                        continue
                    print(f"  [warn] r/{sub} q={query!r} {kind} p{page}: {r.status_code} after retry — skipping")
                    break
                r.raise_for_status()
                data = r.json().get("data", [])
                break
            except requests.RequestException as exc:
                print(f"  [warn] r/{sub} q={query!r} {kind} p{page}: {exc}")
                data = None
                break
        if data is None:
            break
        if not data:
            break
        newest_ts = after
        for row in data:
            ts = int(row.get("created_utc") or row.get("created", 0))
            if ts >= end_epoch:
                continue
            perm = row.get("permalink")
            if perm and not perm.startswith("http"):
                url = "https://reddit.com" + perm
            elif kind == "comment":
                link_id = (row.get("link_id") or "").replace("t3_", "")
                url = f"https://reddit.com/r/{row.get('subreddit', sub)}/comments/{link_id}/_/{row.get('id', '')}"
            else:
                url = f"https://reddit.com/r/{row.get('subreddit', sub)}/comments/{row.get('id', '')}"
            rows.append({
                "item_id": row.get("id") or f"{sub}_{ts}_{kind}",
                "created_utc": ts,
                "subreddit": row.get("subreddit", sub),
                "query": query,
                "kind": kind,
                "author": (row.get("author") or "").lower(),
                "title": (row.get("title") or "").strip() if kind == "post" else "",
                "body": row.get("body") if kind == "comment" else row.get("selftext"),
                "url": url,
                "score": int(row.get("score") or row.get("ups") or 0),
                "num_comments": int(row.get("num_comments") or 0) if kind == "post" else 0,
            })
            newest_ts = max(newest_ts, ts)
        if len(data) < 100 or newest_ts <= after:
            break
        after = newest_ts + 1
        page += 1
        time.sleep(page_sleep)
    return rows


# Sentiment dictionaries tuned for cultured-dairy / kefir discussion.
POSITIVE_KEYWORDS = [
    "love", "best", "worth", "trust", "quality", "favorite", "recommend",
    "switched to", "won't go back", "taste better", "healthier", "amazing",
    "great", "fresh", "creamy", "tangy", "gut health", "delicious",
]
NEGATIVE_KEYWORDS = [
    "expensive", "overpriced", "scam", "ripoff", "not worth", "fake",
    "misleading", "gross", "awful", "sketchy", "sour", "watery",
    "recall", "spoiled", "chalky", "too sweet", "added sugar",
]
_POS_RES = None
_NEG_RES = None


def _compile_keyword_regexes():
    """Lazy-compile word-boundary regexes for sentiment keywords."""
    global _POS_RES, _NEG_RES
    import re as _re
    if _POS_RES is None:
        _POS_RES = [_re.compile(rf"\b{_re.escape(k)}\b", _re.IGNORECASE) for k in POSITIVE_KEYWORDS]
        _NEG_RES = [_re.compile(rf"\b{_re.escape(k)}\b", _re.IGNORECASE) for k in NEGATIVE_KEYWORDS]


def classify_sentiment(text: str | None) -> str:
    """Dictionary-based sentiment. pos AND no neg → positive · neg AND no pos →
    negative · both or neither → neutral. Body=None → neutral (title-only rows
    don't have enough signal)."""
    if not text: return "neutral"
    _compile_keyword_regexes()
    s = str(text)
    pos_hit = any(r.search(s) for r in _POS_RES)
    neg_hit = any(r.search(s) for r in _NEG_RES)
    if pos_hit and not neg_hit: return "positive"
    if neg_hit and not pos_hit: return "negative"
    return "neutral"


def apply_filters(rows: list[dict], min_body_chars: int = 30,
                  word_boundary_query: str | None = None) -> list[dict]:
    """Apply the standard noise filters to a row list:
      - Drop authors in BOT_AUTHORS
      - Drop comments whose body is present AND shorter than `min_body_chars`
      - If `word_boundary_query` is given AND body is present, require a
        \\b{query}\\b match (case-insensitive).
    Rows where body is None (Arctic Shift didn't return text) pass through —
    server-side substring on the original `field` already enforced relevance.
    """
    import re as _re
    out = []
    wb_re = None
    if word_boundary_query:
        wb_re = _re.compile(rf"\b{_re.escape(word_boundary_query)}\b", _re.IGNORECASE)
    for r in rows:
        if (r.get("author") or "").lower() in BOT_AUTHORS:
            continue
        body = r.get("body")
        if body is not None:
            if len(str(body).strip()) < min_body_chars:
                continue
            if wb_re and not wb_re.search(str(body)):
                continue
        out.append(r)
    return out


def weekly_counts(rows: list[dict]) -> pd.DataFrame:
    """Roll row-level list → (week, subreddit, query, post_count + sentiment split)."""
    if not rows:
        return pd.DataFrame(columns=["week", "subreddit", "query", "post_count",
                                     "pos_count", "neg_count", "neu_count"])
    df = pd.DataFrame(rows)
    if "kind" not in df.columns:
        df["kind"] = "post"
    df = df.drop_duplicates(subset=["subreddit", "item_id", "kind"])
    df["dt"] = pd.to_datetime(df["created_utc"], unit="s", utc=True)
    df["week"] = df["dt"].dt.to_period("W-SUN").dt.end_time.dt.strftime("%Y-%m-%d")
    if "sentiment" not in df.columns:
        df["sentiment"] = df["body"].apply(classify_sentiment) if "body" in df.columns else "neutral"

    total = df.groupby(["week", "subreddit", "query"]).size().reset_index(name="post_count")
    pos = df[df["sentiment"] == "positive"].groupby(["week", "subreddit", "query"]).size().reset_index(name="pos_count")
    neg = df[df["sentiment"] == "negative"].groupby(["week", "subreddit", "query"]).size().reset_index(name="neg_count")
    neu = df[df["sentiment"] == "neutral"].groupby(["week", "subreddit", "query"]).size().reset_index(name="neu_count")

    out = total.merge(pos, on=["week", "subreddit", "query"], how="left") \
               .merge(neg, on=["week", "subreddit", "query"], how="left") \
               .merge(neu, on=["week", "subreddit", "query"], how="left")
    for c in ("pos_count", "neg_count", "neu_count"):
        out[c] = out[c].fillna(0).astype(int)
    return out.sort_values(["week", "subreddit", "query"]).reset_index(drop=True)
