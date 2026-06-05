#!/usr/bin/env python3
"""Lifeway Foods (NASDAQ: LWAY) demand + control-situation dashboard generator.

Reads config + seed + fetched CSVs from ../data and ../config, computes section
metrics, and emits a single static index.html (Chart.js via CDN). No server, no
build step — open the file or host it on GitHub Pages.

Architecture (mirrors the VITL/Warhammer pattern):
    CSVs ──► load_all() ──► compute_*() ──► render_*() ──► build_html()
                                   │
                                   └─► chart_blob ──► window.__lway ──► Chart.js

Run:  python3 scripts/generate_lway_dashboard.py
"""
from __future__ import annotations

import datetime as dt
import json
import re
import warnings
from pathlib import Path

import pandas as pd

# pandas 3.0 raises a cosmetic ChainedAssignment FutureWarning on copy-derived
# frames even when the assignment is safe; silence it so pipeline output stays clean.
warnings.filterwarnings("ignore", category=FutureWarning, message=".*ChainedAssignment.*")

# ── Paths ──────────────────────────────────────────────────────────────────
HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
DATA = ROOT / "data"
CONFIG = ROOT / "config"
READS = ROOT / "reads"
OUT_HTML = ROOT / "index.html"

GENERATED_AT = dt.datetime.now()

# ── Brand palette (Lifeway blue + warm gold) ───────────────────────────────
BRAND_ACCENT = "#1f4e79"   # deep Lifeway blue   rgb(31,78,121)
BRAND_ACCENT2 = "#e8b54d"  # warm gold
BRAND_ACCENT3 = "#7fa8c9"  # light blue
BRAND_NEG = "#c0504d"      # red / negative      rgb(192,80,77)
BRAND_PURPLE = "#8e6db4"
BRAND_BROWN = "#9c6b3f"
BRAND_BLUE = "#6b94b1"

# ── Situation facts (baked anchors; verify against filings each refresh) ────
TICKER = "LWAY"
BRAND_NAME = "Lifeway Foods"
DANONE_BID_1 = 25.00
DANONE_BID_2 = 27.00
DANONE_BID2_DATE = "2024-11-15"
DANONE_WITHDRAW_DATE = "2025-09-18"
PILL_EXPIRY_DATE = "2026-10-29"
ANNUAL_MEETING_DATE = "2026-06-17"
FAMILY_PCT = 26.17
DANONE_PCT = 23.0
CONSEC_QUARTERS = 26
Q1_2026_SALES = 63.0
Q1_2026_SALES_YOY = 37
Q1_2026_GM = 27.5
CURRENT_PRICE_FALLBACK = 23.83

# ── Topic / brand / reaction color maps ────────────────────────────────────
TOPIC_ORDER = ["takeover", "governance", "financial", "launch", "category", "health", "other"]
TOPIC_COLORS = {
    "takeover": "#c0504d", "governance": "#7d3c4a", "financial": "#e0892e",
    "launch": "#1f4e79", "category": "#5b8c5a", "health": "#7fa8c9", "other": "#8b8b78",
}
TOPIC_LABELS = {
    "takeover": "Takeover / Danone", "governance": "Governance / Proxy",
    "financial": "Financial", "launch": "Launch / Distribution",
    "category": "Kefir Category", "health": "Gut Health", "other": "Other",
}
BRAND_SOV_ORDER = ["Lifeway", "Chobani", "Fage", "Siggi's", "Maple Hill", "Wallaby"]
BRAND_SOV_COLORS = {
    "Lifeway": "#1f4e79", "Chobani": "#e0892e", "Fage": "#5b8c5a",
    "Siggi's": "#8e6db4", "Maple Hill": "#9c6b3f", "Wallaby": "#6b94b1",
}
REACTION_COLORS = {"negative": "#c0504d", "flat": "#b8a04c", "positive": "#1f4e79"}

# ── Generic helpers ────────────────────────────────────────────────────────
def safe_read(path: Path, **kw) -> pd.DataFrame:
    """Read a CSV → DataFrame; empty DataFrame if missing/blank/broken.

    A parse failure is logged loudly (not silently swallowed) so a malformed
    analyst-maintained seed surfaces immediately instead of emptying a section.
    """
    import sys
    p = Path(path)
    if not p.exists() or p.stat().st_size == 0:
        return pd.DataFrame()
    try:
        return pd.read_csv(p, **kw)
    except Exception as e:
        print(f"  ! WARNING: failed to parse {p.name}: {type(e).__name__}: {e}", file=sys.stderr)
        return pd.DataFrame()


def file_mtime(path: Path) -> str | None:
    """File modification date as YYYY-MM-DD, or None if missing."""
    try:
        ts = Path(path).stat().st_mtime
        return dt.datetime.fromtimestamp(ts).strftime("%Y-%m-%d")
    except Exception:
        return None


_BOLD = re.compile(r"\*\*([^*]+)\*\*")
_ITAL = re.compile(r"(?<!\*)\*([^*]+)\*(?!\*)")
_H1 = re.compile(r"^#\s+(.*)$")


def _esc(t: str) -> str:
    return t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _inline(t: str) -> str:
    t = _esc(t)
    t = _BOLD.sub(r"<strong>\1</strong>", t)
    t = _ITAL.sub(r"<em>\1</em>", t)
    return t


def load_markdown(path: Path) -> dict:
    """Parse a light-markdown read file → {title, datestamp, html}. {} if absent."""
    p = Path(path)
    if not p.exists():
        return {}
    raw = p.read_text(encoding="utf-8").strip()
    if not raw:
        return {}
    title, datestamp = "", ""
    html: list[str] = []
    in_list = False
    for ln in raw.splitlines():
        m = _H1.match(ln)
        if m and not title:
            head = m.group(1)
            if "·" in head:
                title, datestamp = [s.strip() for s in head.split("·", 1)]
            else:
                title = head.strip()
            continue
        if ln.strip().startswith("- "):
            if not in_list:
                html.append("<ul>")
                in_list = True
            html.append(f"<li>{_inline(ln.strip()[2:])}</li>")
            continue
        if in_list:
            html.append("</ul>")
            in_list = False
        if ln.strip():
            html.append(f"<p>{_inline(ln.strip())}</p>")
    if in_list:
        html.append("</ul>")
    return {"title": title, "datestamp": datestamp, "html": "\n".join(html)}


def fmt_num(v, dollars=False, dp=0):
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return "—"
    try:
        s = f"{float(v):,.{dp}f}"
    except Exception:
        return "—"
    return f"${s}" if dollars else s


def fmt_pct(v, dp=1, plus=False):
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return "—"
    try:
        f = float(v)
    except Exception:
        return "—"
    sign = "+" if (plus and f > 0) else ""
    return f"{sign}{f:.{dp}f}%"


def refresh_footer() -> str:
    return (f"Generated {GENERATED_AT:%Y-%m-%d %H:%M} · "
            f"{BRAND_NAME} ({TICKER}) demand &amp; control dashboard · "
            f"static build, no live calls at view time")


def datestamp_chip(path: Path, label: str = "data") -> str:
    d = file_mtime(path)
    if not d:
        return '<span class="datestamp muted">seed</span>'
    return f'<span class="datestamp">{label} · {d}</span>'


def chart_card(chart_id, title, subtitle="", source="", y_axis_label="",
               height_class="big", take_md=None) -> str:
    take_html = ""
    if take_md:
        md = load_markdown(READS / take_md)
        if md and md.get("html"):
            eyebrow = md.get("title") or "Take"
            take_html = (f'<div class="chart-take"><div class="take-eyebrow">{eyebrow}</div>'
                         f'{md["html"]}</div>')
    axis = f'<div class="axis-label">{y_axis_label}</div>' if y_axis_label else ""
    sub = f'<div class="chart-subtitle">{subtitle}</div>' if subtitle else ""
    src = f'<div class="source-caption">{source}</div>' if source else ""
    return (f'<div class="chart-card">\n'
            f'  <div class="chart-title-row"><div class="chart-title">{title}</div></div>\n'
            f'  {sub}\n  {axis}\n'
            f'  <div class="chart-wrap {height_class}"><canvas id="{chart_id}"></canvas></div>\n'
            f'  {src}\n  {take_html}\n</div>')


def placeholder(msg: str) -> str:
    return f'<div class="placeholder">{msg}</div>'


# ── Load every data source into one dict ───────────────────────────────────
def load_all() -> dict:
    return {
        "products": safe_read(CONFIG / "products.csv"),
        "retailers": safe_read(CONFIG / "retailers.csv"),
        "subs": safe_read(CONFIG / "reddit_subreddits.csv"),
        "stock": safe_read(DATA / "lway_stock.csv"),
        "reddit_weekly": safe_read(DATA / "reddit_mentions_weekly.csv"),
        "reddit_posts": safe_read(DATA / "reddit_posts_recent.csv"),
        "kefir_category": safe_read(DATA / "kefir_category_weekly.csv"),
        "competitor_weekly": safe_read(DATA / "competitor_mentions_weekly.csv"),
        "youtube_monthly": safe_read(DATA / "youtube_monthly.csv"),
        "youtube_category": safe_read(DATA / "youtube_category_monthly.csv"),
        "youtube_videos": safe_read(DATA / "youtube_recent_videos.csv"),
        "news": safe_read(DATA / "news_articles.csv"),
        "events": safe_read(DATA / "event_reactions.csv"),
        "cpi_milk": safe_read(DATA / "bls_cpi_milk_monthly.csv"),
        "classiii": safe_read(DATA / "usda_milk_monthly.csv"),
        "qrev": safe_read(DATA / "quarterly_revenue_growth.csv"),
        "gm": safe_read(DATA / "gross_margin_trajectory.csv"),
        "valuation": safe_read(DATA / "valuation_snapshot.csv"),
        "catalysts": safe_read(DATA / "forward_catalysts.csv"),
        "category_growth": safe_read(DATA / "category_growth.csv"),
        "ownership": safe_read(DATA / "ownership.csv"),
        "callouts": safe_read(DATA / "callouts.csv"),
        "regime_cluster": safe_read(DATA / "regime_cluster.csv"),
    }


# ── Compute helpers ────────────────────────────────────────────────────────
def _numlist(series) -> list:
    out = []
    for x in series:
        try:
            out.append(None if pd.isna(x) else round(float(x), 4))
        except Exception:
            out.append(None)
    return out


def _zscore(vals: list) -> list:
    s = pd.Series([v for v in vals if v is not None], dtype="float64")
    if len(s) < 2 or s.std(ddof=0) == 0:
        return [0.0 if v is not None else None for v in vals]
    mean, sd = s.mean(), s.std(ddof=0)
    return [None if v is None else round((v - mean) / sd, 4) for v in vals]


def _rolling_mean(vals: list, window: int = 4) -> list:
    return pd.Series(vals, dtype="float64").rolling(window, min_periods=1).mean().tolist()


# ── Section compute functions ──────────────────────────────────────────────
def compute_quick_read(D: dict) -> dict:
    stock = D["stock"]
    last_close, last_date = CURRENT_PRICE_FALLBACK, None
    if not stock.empty and "close" in stock.columns:
        s = stock.dropna(subset=["close"]).sort_values("date")
        if not s.empty:
            last_close = float(s.iloc[-1]["close"])
            last_date = str(s.iloc[-1]["date"])
    discount = (DANONE_BID_2 - last_close) / DANONE_BID_2 * 100.0
    timeline = []
    cat = D["catalysts"]
    if not cat.empty:
        for _, r in cat.iterrows():
            if str(r.get("date_kind", "")) == "ongoing":
                continue
            timeline.append({
                "date": str(r.get("date", "")),
                "event": str(r.get("event", "")),
                "tier": str(r.get("impact_tier", "")),
                "direction": str(r.get("direction", "")),
            })
    return {
        "last_close": round(last_close, 2),
        "last_date": last_date,
        "discount": round(discount, 1),
        "control": round(FAMILY_PCT + DANONE_PCT, 2),
        "family": FAMILY_PCT, "danone": DANONE_PCT,
        "streak": CONSEC_QUARTERS,
        "timeline": timeline,
    }


def compute_setup(D: dict) -> dict:
    stock = D["stock"]
    dates, close = [], []
    if not stock.empty and {"date", "close"}.issubset(stock.columns):
        s = stock.dropna(subset=["close"]).sort_values("date")
        dates = [str(x) for x in s["date"]]
        close = _numlist(s["close"])
    refs = [
        {"date": DANONE_BID2_DATE, "label": "Danone $27 bid", "color": BRAND_ACCENT2},
        {"date": DANONE_WITHDRAW_DATE, "label": "Danone withdraws", "color": BRAND_NEG},
        {"date": ANNUAL_MEETING_DATE, "label": "Annual meeting", "color": BRAND_ACCENT},
        {"date": PILL_EXPIRY_DATE, "label": "Pill expiry", "color": BRAND_PURPLE},
    ]
    return {"dates": dates, "close": close,
            "bid1": DANONE_BID_1, "bid2": DANONE_BID_2, "refs": refs}


def compute_stock_news(D: dict) -> dict:
    stock = D["stock"]
    s = pd.DataFrame()
    if not stock.empty and {"date", "close"}.issubset(stock.columns):
        s = (stock.dropna(subset=["close"])
             .assign(date=lambda x: pd.to_datetime(x["date"], errors="coerce"))
             .dropna(subset=["date"]).sort_values("date"))
    ev_dates = [d.strftime("%Y-%m-%d") for d in s["date"]] if not s.empty else []
    ev_close = _numlist(s["close"]) if not s.empty else []

    events = []
    edf = D["events"]
    if not edf.empty:
        for _, r in edf.iterrows():
            d = str(r.get("date", ""))
            y = None
            if not s.empty:
                dd = pd.to_datetime(d, errors="coerce")
                if pd.notna(dd):
                    asof = s[s["date"] <= dd]
                    if not asof.empty:
                        y = round(float(asof.iloc[-1]["close"]), 4)
            kind = str(r.get("reaction_kind", "flat")).lower()
            if kind not in REACTION_COLORS:
                kind = "flat"
            rp = r.get("reaction_pct")
            events.append({
                "date": d, "x": d, "y": y,
                "headline": str(r.get("headline", "")),
                "summary": str(r.get("summary", "")),
                "reaction_pct": (None if pd.isna(rp) else float(rp)),
                "reaction_kind": kind,
            })

    weeks, series = [], {}
    news = D["news"]
    if not news.empty and {"date", "topic"}.issubset(news.columns):
        n = (news.assign(date=lambda x: pd.to_datetime(x["date"], errors="coerce"))
             .dropna(subset=["date"]))
        n = n.assign(week=n["date"].dt.to_period("W-SUN").dt.end_time.dt.strftime("%Y-%m-%d"))
        weeks = sorted(n["week"].unique().tolist())
        for t in TOPIC_ORDER:
            by = n[n["topic"] == t].groupby("week").size()
            counts = [int(by.get(w, 0)) for w in weeks]
            if sum(counts) > 0:
                series[t] = counts
    return {"events": {"dates": ev_dates, "close": ev_close, "events": events},
            "topics": {"weeks": weeks, "series": series}}


def compute_milk(D: dict) -> dict:
    classiii = {"months": [], "vals": []}
    c3 = D["classiii"]
    if not c3.empty and {"month", "class_iii_price"}.issubset(c3.columns):
        c = c3.dropna(subset=["class_iii_price"]).sort_values("month")
        classiii = {"months": [str(x) for x in c["month"]],
                    "vals": _numlist(c["class_iii_price"])}
    cpi = {"months": [], "vals": []}
    cp = D["cpi_milk"]
    if not cp.empty and {"month", "price"}.issubset(cp.columns):
        c = cp.dropna(subset=["price"]).sort_values("month")
        cpi = {"months": [str(x) for x in c["month"]], "vals": _numlist(c["price"])}
    return {"classiii": classiii, "cpi": cpi}


def compute_brand(D: dict) -> dict:
    sov = {"weeks": [], "series": {}, "order": BRAND_SOV_ORDER}
    cw = D["competitor_weekly"]
    if not cw.empty and {"week", "brand", "post_count"}.issubset(cw.columns):
        weeks = sorted(cw["week"].unique().tolist())
        sov["weeks"] = weeks
        for b in BRAND_SOV_ORDER:
            sub = cw[cw["brand"].str.lower() == b.lower()]
            by = sub.groupby("week")["post_count"].sum()
            vals = [int(by.get(w, 0)) for w in weeks]
            if sum(vals) > 0:
                sov["series"][b] = vals
    reddit = {"weeks": [], "lway": [], "category": []}
    rw, kc = D["reddit_weekly"], D["kefir_category"]
    lway_by = rw.groupby("week")["post_count"].sum().to_dict() \
        if (not rw.empty and {"week", "post_count"}.issubset(rw.columns)) else {}
    cat_by = kc.groupby("week")["post_count"].sum().to_dict() \
        if (not kc.empty and {"week", "post_count"}.issubset(kc.columns)) else {}
    all_weeks = sorted(set(lway_by) | set(cat_by))
    if all_weeks:
        reddit["weeks"] = all_weeks
        reddit["lway"] = [int(lway_by.get(w, 0)) for w in all_weeks]
        reddit["category"] = [int(cat_by.get(w, 0)) for w in all_weeks]
    return {"sov": sov, "reddit": reddit}


def compute_financial(D: dict) -> dict:
    diff_color = {"easy": "#5b8c5a", "medium": "#e0892e", "hard": "#c0504d"}
    qrev = {"labels": [], "actual": [], "est": [], "colors": [], "notes": []}
    q = D["qrev"]
    if not q.empty:
        for _, r in q.iterrows():
            qrev["labels"].append(str(r.get("quarter", "")))
            val = r.get("revenue_yoy_pct")
            val = None if pd.isna(val) else float(val)
            kind = str(r.get("kind", "actual"))
            qrev["actual"].append(val if kind == "actual" else None)
            qrev["est"].append(val if kind == "estimate" else None)
            qrev["colors"].append(diff_color.get(str(r.get("comp_difficulty", "")).lower(), "#8b8b78"))
            qrev["notes"].append(str(r.get("note", "") or ""))

    gm = {"labels": [], "actual": [], "est": []}
    band = None
    g = D["gm"]
    if not g.empty:
        qrows = g[~g["quarter"].astype(str).str.startswith("FY")]
        for _, r in qrows.iterrows():
            gm["labels"].append(str(r.get("quarter", "")))
            val = r.get("gross_margin_pct")
            val = None if pd.isna(val) else float(val)
            kind = str(r.get("kind", "actual"))
            gm["actual"].append(val if kind == "actual" else None)
            gm["est"].append(val if kind == "estimate" else None)
        lo = g[g["quarter"].astype(str).str.contains("FY 2027E low")]
        hi = g[g["quarter"].astype(str).str.contains("FY 2027E high")]
        if not lo.empty and not hi.empty:
            band = {"low": float(lo.iloc[0]["gross_margin_pct"]),
                    "high": float(hi.iloc[0]["gross_margin_pct"])}
        if gm["actual"]:
            last_actual = max((i for i, v in enumerate(gm["actual"]) if v is not None), default=None)
            if last_actual is not None:
                gm["est"][last_actual] = gm["actual"][last_actual]

    cat = {"labels": [], "lway": [], "category": []}
    cg = D["category_growth"]
    if not cg.empty:
        for _, r in cg.iterrows():
            cat["labels"].append(str(r.get("quarter", "")))
            cat["lway"].append(None if pd.isna(r.get("lway_yoy_pct")) else float(r.get("lway_yoy_pct")))
            cat["category"].append(None if pd.isna(r.get("category_yoy_pct")) else float(r.get("category_yoy_pct")))
    return {"qrev": qrev, "gm": gm, "gm_band": band, "cat": cat}


def compute_demand_vs_stock(D: dict) -> dict:
    rw = D["reddit_weekly"]
    if rw.empty or "post_count" not in rw.columns or "week" not in rw.columns:
        return {"weeks": [], "demand_z": [], "stock_idx": []}
    by = rw.groupby("week")["post_count"].sum().sort_index()
    weeks = [str(w) for w in by.index]
    # Smooth the sparse weekly mention counts with a 4-week trailing mean before
    # z-scoring; raw counts are mostly zero and z-score into an unreadable barcode.
    demand_z = _zscore(_rolling_mean([float(x) for x in by.values], 4))

    ym = D["youtube_monthly"]
    if not ym.empty and {"month", "video_count"}.issubset(ym.columns):
        ymv = ym.groupby("month")["video_count"].sum().sort_index()
        if len(ymv) >= 2:
            mz = _zscore([float(x) for x in ymv.values])
            mmap = {m: z for m, z in zip(ymv.index, mz)}
            blended = []
            for w, rz in zip(weeks, demand_z):
                yz = mmap.get(w[:7])
                blended.append(round((rz + yz) / 2, 4) if (rz is not None and yz is not None) else rz)
            demand_z = blended

    stock = D["stock"]
    stock_idx = [None] * len(weeks)
    if not stock.empty and {"date", "close"}.issubset(stock.columns):
        s = (stock.dropna(subset=["close"])
             .assign(date=lambda x: pd.to_datetime(x["date"], errors="coerce"))
             .dropna(subset=["date"]).sort_values("date"))
        vals = []
        for w in weeks:
            wd = pd.to_datetime(w, errors="coerce")
            asof = s[s["date"] <= wd]
            vals.append(float(asof.iloc[-1]["close"]) if not asof.empty else None)
        base = next((v for v in vals if v is not None), None)
        if base:
            stock_idx = [None if v is None else round(v / base * 100, 2) for v in vals]
    return {"weeks": weeks, "demand_z": demand_z, "stock_idx": stock_idx}


# ── Anchored-callout engine ─────────────────────────────────────────────────
# Every interpretive line (callouts, the three-forces status metrics, the
# framer answers) is a template whose {placeholders} are filled from computed
# or dated-seed metrics. fill_template records which metrics each line used —
# and any it could NOT anchor — into CALLOUT_LEDGER, which main() writes out as
# an audit so opinion-only statements can't slip in unnoticed.
CALLOUT_LEDGER: list[dict] = []
_PLACEHOLDER = re.compile(r"\{([a-zA-Z0-9_]+)\}")


def fill_template(tmpl: str, M: dict, *, where: str = "", kind: str = "") -> str:
    used, missing = [], []

    def repl(m):
        k = m.group(1)
        v = M.get(k)
        if v is None or v == "":
            missing.append(k)
            return "{" + k + "}"
        used.append(k)
        return str(v)

    out = _PLACEHOLDER.sub(repl, tmpl)
    CALLOUT_LEDGER.append({
        "where": where, "kind": kind, "text": tmpl,
        "used": used, "missing": missing,
        "anchored": bool(used) and not missing,
    })
    return out


def _last_non_null(vals: list):
    for v in reversed(vals or []):
        if v is not None:
            return v
    return None


def compute_metrics(D, qr, fin, brand, milk, demand) -> dict:
    """Flatten every anchorable number into one {key: display_string} dict.

    Values are pre-formatted strings (or None when the underlying series is
    missing, which fill_template then flags). Thresholds tagged 'seed' are
    analyst-chosen watch levels, not computed — called out in the ledger."""
    today = GENERATED_AT.date()
    M: dict = {}

    def dd(date_str):
        try:
            return (pd.to_datetime(date_str).date() - today).days
        except Exception:
            return None

    # — price / control —
    M["last_close"] = fmt_num(qr["last_close"], dollars=True, dp=2)
    M["last_date"] = qr["last_date"] or None
    M["discount_bid2"] = f"{qr['discount']:.0f}"
    M["bid1"] = f"{DANONE_BID_1:.0f}"
    M["bid2"] = f"{DANONE_BID_2:.0f}"
    M["two_bloc"] = f"{qr['control']:.1f}"
    M["family_pct"] = f"{FAMILY_PCT:.1f}"
    M["danone_pct"] = f"{DANONE_PCT:.0f}"
    M["meeting_date"] = ANNUAL_MEETING_DATE
    M["pill_date"] = PILL_EXPIRY_DATE
    M["withdraw_date"] = DANONE_WITHDRAW_DATE
    M["streak"] = str(CONSEC_QUARTERS)
    dm = dd(ANNUAL_MEETING_DATE); M["days_to_meeting"] = str(dm) if dm is not None else None
    dp = dd(PILL_EXPIRY_DATE); M["days_to_pill"] = str(dp) if dp is not None else None

    # — financial —
    qrev_latest = _last_non_null(fin["qrev"]["actual"])
    M["qrev_latest"] = f"{qrev_latest:.0f}" if qrev_latest is not None else None
    gm_latest = _last_non_null(fin["gm"]["actual"])
    M["gm_latest"] = f"{gm_latest:.1f}" if gm_latest is not None else None
    band = fin.get("gm_band")
    M["gm_band_low"] = f"{band['low']:.1f}" if band else None
    M["gm_band_high"] = f"{band['high']:.1f}" if band else None
    M["gm_flag"] = "26"  # analyst margin floor (seed threshold)
    lway_c = _last_non_null(fin["cat"]["lway"]); cat_c = _last_non_null(fin["cat"]["category"])
    M["cat_latest"] = f"{cat_c:.0f}" if cat_c is not None else None
    M["cat_gap"] = f"{lway_c - cat_c:.0f}" if (lway_c is not None and cat_c is not None) else None

    # — milk (Class III) —
    mlatest = mmonth = myoy = None
    c3 = D["classiii"]
    if not c3.empty and {"month", "class_iii_price"}.issubset(c3.columns):
        c = c3.dropna(subset=["class_iii_price"]).sort_values("month")
        if not c.empty:
            mlatest = float(c.iloc[-1]["class_iii_price"]); mmonth = str(c.iloc[-1]["month"])
            if len(c) >= 13:
                prev = float(c.iloc[-13]["class_iii_price"])
                if prev:
                    myoy = (mlatest - prev) / prev * 100
    M["milk_latest"] = f"{mlatest:.2f}" if mlatest is not None else None
    M["milk_month"] = mmonth
    M["milk_yoy"] = f"{myoy:+.0f}" if myoy is not None else None
    M["milk_flag"] = f"{mlatest + 2:.0f}" if mlatest is not None else None  # +$2/cwt headwind (seed)

    # — brand share of voice —
    sov = brand["sov"]["series"]
    if sov:
        totals = {b: sum(v) for b, v in sov.items()}
        grand = sum(totals.values()) or 1
        lway_pct = totals.get("Lifeway", 0) / grand * 100
        M["lway_sov_pct"] = f"{lway_pct:.0f}"
        M["sov_flag"] = f"{max(0.0, lway_pct - 8):.0f}"  # 8-pt buffer below current (seed)
    else:
        M["lway_sov_pct"] = None; M["sov_flag"] = None

    # — demand ↔ price correlation —
    z = demand.get("demand_z") or []; idx = demand.get("stock_idx") or []
    pairs = [(a, b) for a, b in zip(z, idx) if a is not None and b is not None]
    if len(pairs) >= 3:
        r = pd.Series([p[0] for p in pairs]).corr(pd.Series([p[1] for p in pairs]))
        M["demand_corr"] = f"{r:+.2f}" if pd.notna(r) else None
    else:
        M["demand_corr"] = None

    # — news / events —
    ev = D["events"]
    M["events_total"] = str(len(ev)) if not ev.empty else None
    M["news_total"] = str(len(D["news"])) if not D["news"].empty else "0"
    if not ev.empty:
        e = ev.assign(_d=pd.to_datetime(ev["date"], errors="coerce")).sort_values("_d")
        last = e.iloc[-1]
        M["last_event_date"] = str(last.get("date", "")) or None
        rp = last.get("reaction_pct")
        M["last_event_pct"] = f"{float(rp):+.0f}" if pd.notna(rp) else None
    else:
        M["last_event_date"] = None; M["last_event_pct"] = None

    # — valuation scenarios —
    val = D["valuation"]
    def _scn(needle):
        if val.empty or "label" not in val.columns:
            return None
        hit = val[val["label"].astype(str).str.contains(needle, case=False, na=False)]
        return float(hit.iloc[0]["value"]) if not hit.empty else None
    vb = _scn("base"); vr = _scn("re-bid")
    M["val_base"] = f"{vb:.0f}" if vb is not None else None
    M["val_rebid"] = f"{vr:.0f}" if vr is not None else None

    # — regime cluster (seed) —
    rc = D["regime_cluster"]
    if not rc.empty and "kind" in rc.columns:
        def _row(kind):
            hit = rc[rc["kind"] == kind]
            return hit.iloc[0] if not hit.empty else None
        dn = _row("distribution"); zo = _row("insider_buy"); ci = _row("institution")
        if dn is not None and pd.notna(dn.get("stated_price")):
            M["danone_sec_price"] = f"{float(dn['stated_price']):.2f}"
        if zo is not None and pd.notna(zo.get("shares")):
            M["zolezzi_shares"] = f"{int(zo['shares']):,}"
        if ci is not None and pd.notna(ci.get("stated_price")):
            M["citadel_pct"] = f"{float(ci['stated_price']):.1f}"

    return M


# ── Render helpers ─────────────────────────────────────────────────────────
def section_header(num, title, subtitle, anchor) -> str:
    return (f'<div class="section-header" id="{anchor}">'
            f'<div class="section-num">{num}</div>'
            f'<div><div class="section-title">{title}</div>'
            f'<div class="section-subtitle">{subtitle}</div></div></div>')


def render_top_callout(D: dict) -> str:
    md = load_markdown(READS / "whats_new.md")
    if not md:
        return ""
    chip = f'<span class="datestamp">{md["datestamp"]}</span>' if md.get("datestamp") else ""
    return (f'<div class="whats-new-card"><div class="whats-new-eyebrow">What\'s new {chip}</div>'
            f'<div class="whats-new-title">{md.get("title", "")}</div>'
            f'<div class="whats-new-body">{md.get("html", "")}</div></div>')


def render_the_situation(D: dict, M: dict) -> str:
    # Each force shows a live status line (value + direction), not a paragraph.
    cols = [
        ("Danone overhang", "OVERHANG", "neg", "down",
         f"Danone bid ${DANONE_BID_1:.0f} → ${DANONE_BID_2:.0f}, then <strong>withdrew {DANONE_WITHDRAW_DATE}</strong>, "
         f"keeping a ~{DANONE_PCT:.0f}% stake plus a cooperation agreement. The poison pill lapses {PILL_EXPIRY_DATE}.",
         "pill lapses in {days_to_pill}d · {discount_bid2}% below ${bid2}"),
        ("Family control fight", "CONTESTED", "mid", "flat",
         f"Edward &amp; Ludmila Smolyansky ({FAMILY_PCT:.1f}%) are running a dissident slate against CEO Julie Smolyansky "
         f"and the incumbent board. The <strong>{ANNUAL_MEETING_DATE} annual meeting</strong> is the decisive vote.",
         "{days_to_meeting}d to the {family_pct}% vote"),
        ("Category momentum", "ACCELERATING", "pos", "up",
         f"Q1 2026 net sales <strong>${Q1_2026_SALES:.0f}M</strong>, gross margin {Q1_2026_GM:.1f}% — the {CONSEC_QUARTERS}th "
         "straight YoY-growth quarter as kefir rides the gut-health wave.",
         "+{qrev_latest}% Q1 sales · {streak}-qtr streak"),
    ]
    arrows = {"up": "▲", "down": "▼", "flat": "■"}
    items = ""
    for title, pill, cls, dirn, desc, metric_tmpl in cols:
        metric = fill_template(metric_tmpl, M, where=f"forces/{title}", kind="force")
        items += (f'<div class="damage-col"><div class="damage-pill {cls}">{pill}</div>'
                  f'<div class="damage-title">{title}</div>'
                  f'<div class="damage-desc">{desc}</div>'
                  f'<div class="damage-metric"><span class="dir {dirn}">{arrows[dirn]}</span>{metric}</div></div>')
    return ('<div class="three-damages">'
            f'<div class="three-damages-eyebrow">The setup · three forces colliding into the {ANNUAL_MEETING_DATE} vote</div>'
            f'<div class="damages-grid">{items}</div></div>')


def render_dashboard_questions(M: dict) -> str:
    """Top-of-page framer — the three questions the dashboard exists to answer.
    Each answer carries a live/dated number so the framer is anchored, not opinion."""
    qs = [
        ("1", "Is the kefir / cultured-dairy category real &amp; durable?",
         "Kefir is compounding — Q1 sales <strong>+{qrev_latest}%</strong> on a {cat_gap}-pt gap over the ~{cat_latest}% category."),
        ("2", "Who controls the company once the cooperation-agreement constraints lift?",
         "<strong>{two_bloc}%</strong> sits in two strategic blocs; the pill lapses {pill_date} ({days_to_pill}d), reopening the control question."),
        ("3", "What is it worth — standalone vs. in a deal?",
         "<strong>{last_close}</strong> today against a ${val_base} stand-alone / ${val_rebid} re-bid frame."),
    ]
    rows = ""
    for n, q, ans in qs:
        filled = fill_template(ans, M, where=f"framer/q{n}", kind="framer")
        rows += f'<div class="qre-q"><b>{n}</b><div><strong>{q}</strong> {filled}</div></div>'
    return ('<div class="quick-read-explainer">'
            '<div class="qre-eyebrow">What this dashboard answers</div>'
            f'{rows}</div>')


def render_callouts(section: str, M: dict, D: dict) -> str:
    """Section-level callout pair (WHAT THIS MEANS FOR LWAY + WHAT TO WATCH),
    driven entirely by data/callouts.csv with live metric substitution."""
    cdf = D["callouts"]
    if cdf.empty or "section" not in cdf.columns:
        return ""
    rows = cdf[cdf["section"] == section]
    if rows.empty:
        return ""
    means = rows[rows["kind"] == "means"].sort_values("order")
    watch = rows[rows["kind"] == "watch"].sort_values("order")
    means_html = "".join(
        f"<p>{fill_template(str(r['text']), M, where=f'{section}/means', kind='means')}</p>"
        for _, r in means.iterrows())
    watch_html = "".join(
        f"<li>{fill_template(str(r['text']), M, where=f'{section}/watch', kind='watch')}</li>"
        for _, r in watch.iterrows())
    means_box = (f'<div class="callout callout-means"><div class="callout-eyebrow">'
                 f'What this means for {TICKER}</div>{means_html}</div>') if means_html else ""
    watch_box = (f'<div class="callout callout-watch"><div class="callout-eyebrow">'
                 f'What to watch</div><ul>{watch_html}</ul></div>') if watch_html else ""
    if not means_box and not watch_box:
        return ""
    return f'<div class="section-callouts">{means_box}{watch_box}</div>'


def render_quick_read(D: dict, qr: dict) -> str:
    close_sub = f"as of {qr['last_date']}" if qr["last_date"] else "fallback — run the stock fetcher"
    disc_cls = "neg" if qr["discount"] > 0 else "pos"
    tiles = [
        ("Last close", fmt_num(qr["last_close"], dollars=True, dp=2), close_sub, ""),
        (f"Discount to ${DANONE_BID_2:.0f} bid", fmt_pct(qr["discount"]), "vs withdrawn Danone offer", disc_cls),
        ("Two-bloc control", f"{qr['control']:.1f}%", f"{qr['family']:.1f}% family + {qr['danone']:.0f}% Danone", ""),
        ("YoY-growth streak", f"{qr['streak']} qtrs", "consecutive quarters", "pos"),
    ]
    htiles = ""
    for label, val, sub, cls in tiles:
        htiles += (f'<div class="hero-tile"><div class="hero-label">{label}</div>'
                   f'<div class="hero-val {cls}">{val}</div>'
                   f'<div class="hero-sub">{sub}</div></div>')
    strip = ""
    for t in qr["timeline"][:5]:
        strip += (f'<div class="timeline-item"><div class="timeline-date">{t["date"]}</div>'
                  f'<div class="timeline-text">{_esc(t["event"])}</div>'
                  f'<div class="timeline-tier">{t["tier"]}</div></div>')
    timeline = (f'<div class="timeline-strip"><div class="timeline-eyebrow">Forward catalysts</div>'
                f'<div class="timeline-rows">{strip}</div></div>') if strip else ""
    return (section_header("00", "Quick read",
                           "Where the stock sits vs the takeover anchor — and what's next", "quick-read")
            + f'<div class="hero-row">{htiles}</div>' + timeline)


def render_setup(D: dict) -> str:
    body = (
        '<div class="setup-grid">'
        '<div class="setup-card"><div class="setup-eyebrow">The control trade</div>'
        f'<p>Danone twice tried to buy Lifeway — ${DANONE_BID_1:.0f} then ${DANONE_BID_2:.0f} — before walking away on '
        f'{DANONE_WITHDRAW_DATE}, keeping ~{DANONE_PCT:.0f}% and a cooperation agreement. The poison pill lapses '
        f'{PILL_EXPIRY_DATE}. Any re-bid resets the floor back toward the old premium.</p></div>'
        '<div class="setup-card"><div class="setup-eyebrow">The value trade</div>'
        f'<p>Under the governance noise the business compounds: {CONSEC_QUARTERS} straight YoY-growth quarters, gross margin '
        'pushing toward 28–30%, and kefir taking share above the category. Stand-alone fair value sits in the mid-$20s, with '
        f'a strategic re-bid scenario at/above ${DANONE_BID_2:.0f}.</p></div></div>'
    )
    if not D["stock"].empty:
        chart = chart_card("setupAnchorChart", "Share price vs takeover anchors",
                           f"Daily close with the ${DANONE_BID_1:.0f}/${DANONE_BID_2:.0f} Danone bids and key control dates",
                           "Source: Yahoo Finance daily close · seed control dates",
                           "Price ($)", "big", take_md="setup_take.md")
    else:
        chart = placeholder("Share-price chart populates after the first <code>fetch_stock_price.py</code> run.")
    return (section_header("01", "The setup",
                           "Control optionality stacked on a compounding category story", "setup") + body + chart)


def render_news(D: dict) -> str:
    head = section_header("02", "News flow &amp; market reaction",
                          "The Danone saga in prices, plus what the press is covering now", "news")
    if not D["stock"].empty:
        ev_chart = chart_card("eventsChart", "Price &amp; event reactions",
                              "Daily close with corporate-event markers; dot color = 1-day stock reaction",
                              "Source: Yahoo Finance · seed event reactions", "Price ($)", "big")
    else:
        ev_chart = placeholder("Event-reaction price chart populates after the first stock pull.")
    rows = ""
    edf = D["events"]
    if not edf.empty:
        for _, r in edf.sort_values("date", ascending=False).iterrows():
            kind = str(r.get("reaction_kind", "flat")).lower()
            badge = "badge-pos" if kind == "positive" else ("badge-neg" if kind == "negative" else "badge-mid")
            rp = r.get("reaction_pct")
            rp_s = fmt_pct(rp, plus=True) if pd.notna(rp) else "—"
            rows += (f'<tr><td class="nowrap">{r.get("date", "")}</td>'
                     f'<td><strong>{_esc(str(r.get("headline", "")))}</strong>'
                     f'<div class="cell-sub">{_esc(str(r.get("summary", "")))}</div></td>'
                     f'<td class="num"><span class="badge {badge}">{rp_s}</span></td></tr>')
    ev_table = (f'<div class="table-card"><table><thead><tr><th>Date</th><th>Event</th>'
                f'<th class="num">1-day reaction</th></tr></thead><tbody>{rows}</tbody></table></div>') if rows else ""
    if not D["news"].empty:
        topic_chart = chart_card("topicMixChart", "What the press is writing about",
                                 "Weekly article volume by topic", "Source: GDELT / Google News RSS",
                                 "Articles / week", "big")
        n = D["news"].copy().sort_values("date", ascending=False)
        log = ""
        for _, r in n.head(40).iterrows():
            t = str(r.get("topic", "other"))
            chip = TOPIC_LABELS.get(t, t.title())
            color = TOPIC_COLORS.get(t, "#8b8b78")
            url = str(r.get("url", "") or "")
            head_txt = _esc(str(r.get("headline", "")))
            link = f'<a href="{url}" target="_blank" rel="noopener">{head_txt}</a>' if url.startswith("http") else head_txt
            log += (f'<div class="feed-item"><div class="feed-row1">'
                    f'<span class="feed-date">{r.get("date", "")}</span>'
                    f'<span class="topic-chip" style="background:{color}1a;color:{color};border-color:{color}55">{chip}</span>'
                    f'</div><div class="feed-excerpt">{link}</div>'
                    f'<div class="feed-meta">{_esc(str(r.get("source", "")))}</div></div>')
        article_log = (f'<details class="article-log" open><summary>Recent coverage · '
                       f'{min(40, len(n))} of {len(n)} articles</summary>'
                       f'<div class="feed-list">{log}</div></details>')
    else:
        topic_chart = placeholder("Topic-mix chart and article log populate after the first "
                                  "<code>fetch_google_news.py</code> run.")
        article_log = ""
    return head + ev_chart + ev_table + topic_chart + article_log


def render_milk(D: dict) -> str:
    head = section_header("03", "Milk &amp; input costs",
                          "Class III milk is the gross-margin swing factor for a dairy processor", "milk")
    if D["classiii"].empty:
        return head + placeholder("Milk price series unavailable.")
    c = D["classiii"].dropna(subset=["class_iii_price"]).sort_values("month")
    latest = float(c.iloc[-1]["class_iii_price"])
    latest_m = str(c.iloc[-1]["month"])
    chg = None
    if len(c) >= 13:
        prev = float(c.iloc[-13]["class_iii_price"])
        if prev:
            chg = (latest - prev) / prev * 100
    chg_cls = "neg" if (chg or 0) > 0 else "pos"
    cards = ('<div class="stat-row">'
             f'<div class="stat-card"><div class="hero-label">Latest Class III</div>'
             f'<div class="hero-val">${latest:.2f}</div><div class="hero-sub">{latest_m} · per cwt</div></div>'
             f'<div class="stat-card"><div class="hero-label">12-mo change</div>'
             f'<div class="hero-val {chg_cls}">{fmt_pct(chg, plus=True)}</div>'
             f'<div class="hero-sub">lower milk = margin tailwind</div></div></div>')
    chart = chart_card("milkChart", "Milk price backdrop",
                       "USDA Class III ($/cwt, left axis) vs CPI retail whole milk ($/gal, right axis)",
                       "Source: USDA AMS Class III (seed) · BLS CPI APU0000709112", "$ / cwt", "big",
                       take_md="milk_take.md")
    return head + cards + chart


def render_brand(D: dict) -> str:
    head = section_header("04", "Brand &amp; category demand",
                          "Share of voice vs cultured-dairy peers, and kefir-category enthusiasm", "brand")
    blocks = []
    if not D["competitor_weekly"].empty:
        blocks.append(chart_card("brandSovChart", "Share of voice · cultured-dairy brands",
                                 "Weekly Reddit mentions by brand (stacked)", "Source: Reddit via Arctic Shift",
                                 "Mentions / week", "big", take_md="sov_take.md"))
    else:
        blocks.append(placeholder("Brand share-of-voice chart populates after "
                                  "<code>fetch_competitor_mentions.py</code> runs."))
    if not D["reddit_weekly"].empty or not D["kefir_category"].empty:
        blocks.append(chart_card("redditChart", "Lifeway vs kefir-category buzz",
                                 "Weekly Reddit mentions: Lifeway brand vs the broad kefir category",
                                 "Source: Reddit via Arctic Shift", "Mentions / week", "big"))
    else:
        blocks.append(placeholder("Reddit demand chart populates after <code>fetch_reddit_arctic.py</code> runs."))
    feed = ""
    rp = D["reddit_posts"]
    if not rp.empty:
        items = ""
        for _, r in rp.head(20).iterrows():
            sent = str(r.get("sentiment", "neutral")).lower()
            dot = "pos" if sent == "positive" else ("neg" if sent == "negative" else "mid")
            url = str(r.get("url", "") or "")
            ex = _esc(str(r.get("excerpt", "")))
            link = f'<a href="{url}" target="_blank" rel="noopener">{ex}</a>' if url.startswith("http") else ex
            items += (f'<div class="feed-item"><div class="feed-row1">'
                      f'<span class="feed-date">{r.get("date", "")}</span>'
                      f'<span class="legend-dot {dot}"></span>'
                      f'<span class="feed-meta">r/{_esc(str(r.get("subreddit", "")))} · {r.get("kind", "")}</span></div>'
                      f'<div class="feed-excerpt">{link}</div></div>')
        feed = (f'<details class="article-log"><summary>Recent Lifeway posts &amp; comments · '
                f'{min(20, len(rp))}</summary><div class="feed-list">{items}</div></details>')
    vfeed = ""
    vv = D["youtube_videos"]
    if not vv.empty:
        items = ""
        for _, r in vv.head(12).iterrows():
            url = str(r.get("url", "") or "")
            ti = _esc(str(r.get("title", "")))
            link = f'<a href="{url}" target="_blank" rel="noopener">{ti}</a>' if url.startswith("http") else ti
            items += (f'<div class="feed-item"><div class="feed-row1">'
                      f'<span class="feed-date">{str(r.get("published", ""))[:10]}</span>'
                      f'<span class="feed-meta">{_esc(str(r.get("channel", "")))}</span></div>'
                      f'<div class="feed-excerpt">{link}</div></div>')
        vfeed = (f'<details class="article-log"><summary>Recent kefir videos · {min(12, len(vv))}</summary>'
                 f'<div class="feed-list">{items}</div></details>')
    return head + "".join(blocks) + feed + vfeed


def render_financial(D: dict, fin: dict) -> str:
    head = section_header("05", "Financial trajectory &amp; valuation",
                          "Revenue growth, margin expansion, share-gain vs category, and the value bridge", "financial")
    qrev = chart_card("qrevChart", "Quarterly net-sales growth (YoY)",
                      "Bars colored by comp difficulty; faded bars = estimates",
                      "Source: company prints (seed) · forward estimates", "YoY %", "big")
    gm = chart_card("gmChart", "Gross-margin trajectory",
                    "Solid = actual, dashed = estimate; shaded band = FY27E target range",
                    "Source: company prints (seed)", "Gross margin %", "big", take_md="financial_take.md")
    cat = chart_card("catGrowthChart", "Lifeway vs kefir category (YoY)",
                     "LWAY revenue growth vs estimated category growth — the share-gain gap",
                     "Source: LWAY prints + category scanner commentary (seed)", "YoY %", "big")
    val = D["valuation"]
    cards = ""
    if not val.empty:
        for _, r in val.iterrows():
            kind = str(r.get("kind", ""))
            label = _esc(str(r.get("label", "")))
            v = r.get("value")
            note = _esc(str(r.get("note", "") or ""))
            if pd.isna(v):
                disp = "—"
            elif kind == "multiple":
                disp = f"{float(v):.1f}x"
            else:
                disp = fmt_num(v, dollars=True, dp=2)
            cls = {"current": "", "anchor": "mid", "scenario": "pos", "multiple": ""}.get(kind, "")
            cards += (f'<div class="val-card"><div class="val-kind">{kind}</div>'
                      f'<div class="hero-label">{label}</div>'
                      f'<div class="val-num {cls}">{disp}</div>'
                      f'<div class="hero-sub">{note}</div></div>')
    val_block = f'<div class="val-cards-row">{cards}</div>' if cards else ""
    return head + qrev + gm + val_block + cat


def render_demand(D: dict) -> str:
    head = section_header("06", "Demand vs the tape",
                          "Composite social demand (z-scored) against the share price, indexed", "demand")
    if D["reddit_weekly"].empty or D["stock"].empty:
        return head + placeholder("Demand-vs-tape overlay populates once both Reddit and stock data are present.")
    chart = chart_card("demandChart", "Social demand vs share price",
                       "Z-scored Reddit + YouTube demand, 4-wk trailing avg (left) vs LWAY indexed to 100 (right)",
                       "Source: Reddit / YouTube + Yahoo Finance", "Demand (z-score)", "big",
                       take_md="demand_take.md")
    return head + chart


def render_summary_modal(D: dict, qr: dict, fin: dict) -> str:
    paras = []
    paras.append(
        f"<p><strong>Lifeway Foods ({TICKER})</strong> is a control situation wrapped around a compounding category story. "
        f"Danone bid ${DANONE_BID_1:.0f} then ${DANONE_BID_2:.0f} a share before withdrawing on {DANONE_WITHDRAW_DATE}, "
        f"leaving a ~{DANONE_PCT:.0f}% strategic stake and a cooperation agreement in place. The stock last printed "
        f"{fmt_num(qr['last_close'], dollars=True, dp=2)}, a {fmt_pct(qr['discount'])} discount to the withdrawn ${DANONE_BID_2:.0f} bid.</p>")
    paras.append(
        f"<p>Control is genuinely contested. The founding Smolyansky family's dissident bloc holds {qr['family']:.1f}% and is "
        f"running a slate against CEO Julie Smolyansky and the board; combined with Danone's stake, {qr['control']:.1f}% of the "
        f"register sits in two strategic hands ahead of the <strong>{ANNUAL_MEETING_DATE} annual meeting</strong>. The poison "
        f"pill lapses {PILL_EXPIRY_DATE}, reopening a re-bid window.</p>")
    paras.append(
        f"<p>Operationally the franchise is accelerating: Q1 2026 net sales of ${Q1_2026_SALES:.0f}M (+{Q1_2026_SALES_YOY}%) "
        f"marked the {CONSEC_QUARTERS}th consecutive YoY-growth quarter, gross margin reached {Q1_2026_GM:.1f}%, and Lifeway is "
        "taking share well above the high-single-digit kefir category as gut-health demand broadens. Lower Class III milk is a "
        "margin tailwind into the back half.</p>")
    paras.append(
        f"<p><strong>Bottom line:</strong> two strategic blocs, an accelerating category, and a pill expiry stack optionality on "
        f"top of a business that already clears a mid-$20s stand-alone value — with a strategic re-bid scenario toward "
        f"${DANONE_BID_2:.0f}+. The {ANNUAL_MEETING_DATE} vote is the swing factor.</p>")
    body = "".join(paras)
    return (f'<div class="modal-backdrop" id="summaryModal" onclick="if(event.target===this)this.style.display=\'none\'">'
            f'<div class="modal"><div class="modal-head"><div class="modal-title">Executive summary · {BRAND_NAME} ({TICKER})</div>'
            f'<button class="modal-close" onclick="document.getElementById(\'summaryModal\').style.display=\'none\'">×</button></div>'
            f'<div class="modal-body">{body}</div></div></div>')


# ── Static CSS (Lifeway blue on cream) ─────────────────────────────────────
CSS = """
:root{
  --accent:#1f4e79; --accent2:#e8b54d; --accent3:#7fa8c9; --neg:#c0504d;
  --bg:#fbf8ef; --surface:#ffffff; --surface2:#f7f3e6; --border:#e4dccd;
  --text:#2b2f25; --muted:#7a7c70; --pos:#1f4e79;
  --mono:'SF Mono',ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;
}
*{box-sizing:border-box}
html{scroll-behavior:smooth}
body{margin:0;background:var(--bg);color:var(--text);
  font-family:'Inter',-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
  font-size:15px;line-height:1.55;-webkit-font-smoothing:antialiased;text-rendering:optimizeLegibility}
/* numerics: tabular figures everywhere, SF Mono for tables + inline metric code */
.hero-val,.val-num,.damage-metric,.stat-card .hero-val,td.num,th.num,
.timeline-date,.feed-date{font-variant-numeric:tabular-nums}
code,.source-caption code,.callout-watch li b,td.num,.qre-q b{font-family:var(--mono)}
.source-caption code{color:var(--accent);background:rgba(31,78,121,0.08);
  padding:1px 5px;border-radius:3px;font-size:11px}
a{color:var(--accent);text-decoration:none}
a:hover{text-decoration:underline}
.topbar{position:sticky;top:0;z-index:50;background:var(--accent);color:#fff;
  box-shadow:0 1px 8px rgba(31,78,121,0.25)}
.topbar-inner{max-width:1280px;margin:0 auto;padding:12px 20px;display:flex;
  align-items:center;gap:16px;flex-wrap:wrap}
.topbar h1{font-size:18px;margin:0;font-weight:700;letter-spacing:.2px;white-space:nowrap}
.topbar h1 span{opacity:.7;font-weight:500}
.topbar-nav{display:flex;gap:6px;flex-wrap:wrap;margin-left:auto}
.nav-btn{color:#fff;background:rgba(255,255,255,0.12);border:1px solid rgba(255,255,255,0.18);
  padding:5px 10px;border-radius:7px;font-size:12.5px;cursor:pointer;transition:background .15s}
.nav-btn:hover{background:rgba(255,255,255,0.26);text-decoration:none}
.summary-btn{color:var(--accent);background:var(--accent2);border:none;padding:6px 13px;
  border-radius:7px;font-size:12.5px;font-weight:700;cursor:pointer}
.summary-btn:hover{filter:brightness(1.05)}
.container{max-width:1280px;margin:0 auto;padding:22px 20px 60px}
.datestamp{display:inline-block;font-size:11px;color:var(--muted);background:var(--surface2);
  border:1px solid var(--border);border-radius:20px;padding:2px 9px;vertical-align:middle}
.datestamp.muted{opacity:.7}
.whats-new-card{background:linear-gradient(100deg,#fff,#fbf3df);border:1px solid var(--border);
  border-left:5px solid var(--accent2);border-radius:12px;padding:16px 20px;margin:18px 0 6px}
.whats-new-eyebrow{font-size:11px;letter-spacing:1.4px;text-transform:uppercase;color:var(--accent);
  font-weight:700;margin-bottom:5px;display:flex;align-items:center;gap:8px}
.whats-new-title{font-size:18px;font-weight:700;margin-bottom:6px}
.whats-new-body p{margin:5px 0}
.three-damages{background:var(--surface);border:1px solid var(--border);border-radius:12px;
  padding:18px 20px;margin:20px 0 12px;box-shadow:0 1px 3px rgba(31,78,121,0.05)}
.three-damages-eyebrow{font-size:11px;letter-spacing:1.4px;text-transform:uppercase;
  color:var(--accent);font-weight:700;margin-bottom:13px}
.damages-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:14px}
.damage-col{background:#fdfbf2;border:1px solid var(--border);border-radius:10px;padding:14px 16px}
.damage-pill{display:inline-block;font-size:10.5px;letter-spacing:.8px;font-weight:800;
  padding:3px 9px;border-radius:20px;margin-bottom:8px;text-transform:uppercase}
.damage-pill.neg{background:#fbe6e3;color:var(--neg)}
.damage-pill.mid{background:#f6efd6;color:#9a7b1f}
.damage-pill.pos{background:#e4eef7;color:var(--accent)}
.damage-title{font-size:15.5px;font-weight:700;margin-bottom:6px}
.damage-desc{font-size:13px;color:#43463c;line-height:1.5}
.damage-metric{margin-top:10px;padding-top:8px;border-top:1px dashed var(--border);
  font-size:12.5px;font-weight:700;color:var(--accent);display:flex;align-items:center;gap:5px}
.damage-metric .dir{font-size:12px;font-weight:800}
.damage-metric .dir.up{color:#2a7d3a} .damage-metric .dir.down{color:var(--neg)} .damage-metric .dir.flat{color:var(--muted)}

/* Section-level callout pair — WHAT THIS MEANS FOR LWAY + WHAT TO WATCH */
.section-callouts{display:grid;grid-template-columns:1fr 1fr;gap:14px;margin:16px 0 6px}
.callout{border-radius:10px;padding:13px 16px}
.callout-means{background:linear-gradient(180deg,#fdf9ec,#fcefd0);border:1px solid #f0d987;
  border-left:4px solid var(--accent2)}
.callout-watch{background:linear-gradient(180deg,#f4f8fb,#eaf1f7);border:1px solid #cdddea;
  border-left:4px solid var(--accent)}
.callout-eyebrow{font-size:10.5px;letter-spacing:1.3px;text-transform:uppercase;font-weight:800;margin-bottom:8px}
.callout-means .callout-eyebrow{color:#8a6b10}
.callout-watch .callout-eyebrow{color:var(--accent)}
.callout p{margin:0;font-size:13px;line-height:1.62;color:#43463c}
.callout strong{color:var(--text);font-weight:700}
.callout-watch ul{margin:0;padding:0;list-style:none}
.callout-watch li{font-size:12.5px;line-height:1.5;color:#43463c;padding:6px 0;border-bottom:1px dashed var(--border)}
.callout-watch li:last-child{border-bottom:none}
.callout-watch li b{color:var(--text);font-variant-numeric:tabular-nums;font-weight:700}
.callout-flag{display:inline-block;font-size:9px;font-weight:800;letter-spacing:.4px;color:#b34738;
  background:#f8e2dc;border-radius:4px;padding:1px 5px;margin-left:6px;text-transform:uppercase}

/* "What this dashboard answers" framer */
.quick-read-explainer{background:linear-gradient(180deg,#fffdf2,#fcefd0);border:1px solid #ecd47c;
  border-radius:12px;padding:16px 22px;margin:16px 0 8px}
.qre-eyebrow{font-size:10.5px;font-weight:800;color:#8a6b10;letter-spacing:1.5px;
  margin-bottom:10px;text-transform:uppercase}
.qre-q{display:flex;gap:11px;padding:8px 0;border-bottom:1px dashed var(--border);
  font-size:13.5px;line-height:1.6;color:#43463c}
.qre-q:last-child{border-bottom:none}
.qre-q b{color:var(--accent);font-weight:800;white-space:nowrap}
.qre-q strong{color:var(--text);font-weight:700}
.section-header{display:flex;align-items:flex-start;gap:14px;margin:38px 0 14px;
  padding-top:14px;border-top:2px solid var(--border);scroll-margin-top:72px}
.section-num{font-size:13px;font-weight:800;color:#fff;background:var(--accent);
  border-radius:8px;padding:4px 9px;margin-top:2px}
.section-title{font-size:21px;font-weight:700}
.section-subtitle{font-size:13.5px;color:var(--muted)}
.hero-row{display:grid;grid-template-columns:repeat(4,1fr);gap:14px;margin:6px 0 4px}
.hero-tile,.stat-card{background:var(--surface);border:1px solid var(--border);border-radius:12px;padding:16px}
.hero-label{font-size:12px;color:var(--muted);font-weight:600;text-transform:uppercase;letter-spacing:.5px}
.hero-val{font-size:30px;font-weight:800;margin:4px 0 2px;color:var(--text)}
.hero-val.pos{color:var(--pos)} .hero-val.neg{color:var(--neg)}
.hero-sub{font-size:12.5px;color:var(--muted)}
.stat-row{display:grid;grid-template-columns:repeat(2,1fr);gap:14px;margin:4px 0 12px;max-width:640px}
.timeline-strip{background:var(--surface);border:1px solid var(--border);border-radius:12px;
  padding:14px 18px;margin:14px 0}
.timeline-eyebrow{font-size:11px;letter-spacing:1.2px;text-transform:uppercase;color:var(--muted);
  font-weight:700;margin-bottom:8px}
.timeline-rows{display:flex;flex-direction:column;gap:8px}
.timeline-item{display:grid;grid-template-columns:120px 1fr auto;gap:12px;align-items:center;
  font-size:13.5px;padding-bottom:7px;border-bottom:1px dashed var(--border)}
.timeline-item:last-child{border-bottom:none;padding-bottom:0}
.timeline-date{font-weight:700;color:var(--accent)}
.timeline-tier{font-size:10.5px;font-weight:800;color:var(--muted);letter-spacing:.6px}
.setup-grid{display:grid;grid-template-columns:1fr 1fr;gap:14px;margin:6px 0 14px}
.setup-card{background:var(--surface);border:1px solid var(--border);border-radius:12px;padding:16px 18px}
.setup-card p{margin:6px 0 0;font-size:13.5px;color:#43463c}
.setup-eyebrow{font-size:11px;letter-spacing:1.2px;text-transform:uppercase;color:var(--accent);font-weight:700}
.chart-card{background:var(--surface);border:1px solid var(--border);border-radius:12px;
  padding:16px 18px;margin:14px 0}
.chart-title-row{display:flex;justify-content:space-between;align-items:baseline}
.chart-title{font-size:16.5px;font-weight:700}
.chart-subtitle{font-size:12.5px;color:var(--muted);margin-top:2px}
.axis-label{font-size:11px;color:var(--muted);margin-top:6px;font-style:italic}
.chart-wrap{position:relative;width:100%;height:300px;margin-top:8px}
.chart-wrap.big{height:380px}
.chart-wrap.tall{height:460px}
.source-caption{font-size:11px;color:var(--muted);margin-top:8px}
.chart-take{background:var(--surface2);border-radius:9px;padding:11px 14px;margin-top:12px}
.chart-take p{margin:4px 0;font-size:13px}
.take-eyebrow{font-size:10.5px;letter-spacing:1.2px;text-transform:uppercase;color:var(--accent);font-weight:800}
.placeholder{background:var(--surface2);border:1px dashed var(--border);border-radius:12px;
  padding:22px;margin:14px 0;color:var(--muted);font-size:13.5px;text-align:center}
.placeholder code{background:#fff;border:1px solid var(--border);border-radius:5px;padding:1px 5px;font-size:12px}
.table-card{background:var(--surface);border:1px solid var(--border);border-radius:12px;
  padding:6px 6px;margin:14px 0;overflow-x:auto}
table{width:100%;border-collapse:collapse;font-size:13.5px}
th{text-align:left;padding:9px 12px;border-bottom:2px solid var(--border);font-size:11.5px;
  text-transform:uppercase;letter-spacing:.5px;color:var(--muted)}
td{padding:9px 12px;border-bottom:1px solid var(--border);vertical-align:top}
td.num,th.num{text-align:right}
.nowrap{white-space:nowrap}
.cell-sub{font-size:12px;color:var(--muted);margin-top:2px}
.badge{display:inline-block;font-size:12px;font-weight:700;padding:2px 8px;border-radius:6px}
.badge-pos{background:#e4eef7;color:var(--accent)}
.badge-neg{background:#fbe6e3;color:var(--neg)}
.badge-mid{background:#f6efd6;color:#9a7b1f}
.badge-na{background:var(--surface2);color:var(--muted)}
.val-cards-row{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin:14px 0}
.val-card{background:var(--surface);border:1px solid var(--border);border-radius:12px;padding:14px}
.val-kind{font-size:10px;letter-spacing:1px;text-transform:uppercase;color:var(--muted);font-weight:800}
.val-num{font-size:24px;font-weight:800;margin:3px 0}
.val-num.pos{color:var(--pos)} .val-num.mid{color:#9a7b1f}
.article-log{margin:14px 0;background:var(--surface);border:1px solid var(--border);border-radius:12px;padding:6px 14px}
.article-log summary{cursor:pointer;font-weight:700;font-size:13.5px;padding:8px 0;color:var(--accent)}
.feed-list{display:flex;flex-direction:column;gap:10px;padding:8px 0 12px}
.feed-item{border-bottom:1px dashed var(--border);padding-bottom:9px}
.feed-item:last-child{border-bottom:none}
.feed-row1{display:flex;align-items:center;gap:9px;flex-wrap:wrap}
.feed-date{font-size:12px;color:var(--muted);font-weight:600}
.feed-excerpt{font-size:13.5px;margin-top:3px}
.feed-meta{font-size:11.5px;color:var(--muted)}
.topic-chip{font-size:11px;font-weight:700;padding:1px 8px;border-radius:20px;border:1px solid}
.legend-dot{width:9px;height:9px;border-radius:50%;display:inline-block;background:var(--muted)}
.legend-dot.pos{background:var(--accent)} .legend-dot.neg{background:var(--neg)} .legend-dot.mid{background:var(--accent2)}
.modal-backdrop{display:none;position:fixed;inset:0;background:rgba(20,24,18,0.55);z-index:100;
  align-items:flex-start;justify-content:center;padding:40px 16px;overflow-y:auto}
.modal{background:var(--surface);border-radius:14px;max-width:760px;width:100%;
  box-shadow:0 12px 48px rgba(0,0,0,0.3)}
.modal-head{display:flex;justify-content:space-between;align-items:center;padding:18px 22px;
  border-bottom:1px solid var(--border)}
.modal-title{font-size:17px;font-weight:700}
.modal-close{background:none;border:none;font-size:26px;line-height:1;cursor:pointer;color:var(--muted)}
.modal-body{padding:18px 22px 26px}
.modal-body p{font-size:14px;line-height:1.6;margin:9px 0}
footer{max-width:1280px;margin:0 auto;padding:24px 20px 50px;color:var(--muted);font-size:12px;
  border-top:1px solid var(--border)}
@media(max-width:880px){
  .damages-grid,.hero-row,.val-cards-row{grid-template-columns:1fr 1fr}
  .setup-grid,.section-callouts{grid-template-columns:1fr}
  .timeline-item{grid-template-columns:90px 1fr}
  .timeline-tier{display:none}
}
@media(max-width:560px){
  .damages-grid,.hero-row,.val-cards-row,.stat-row{grid-template-columns:1fr}
}
"""


# ── Static client JS (reads window.__lway, builds 10 charts) ───────────────
JS_STATIC = """
document.addEventListener('DOMContentLoaded', function(){
  const d = window.__lway; if(!d) return;
  const el = id => document.getElementById(id);
  const A=d.accent, A2=d.accent2, A3=d.accent3, NEG=d.accent_neg,
        PURPLE=d.purple, BROWN=d.brown, BLUE=d.blue;
  const grid = () => ({color:'rgba(0,0,0,0.05)'});

  const refLinePlugin = { id:'refLine', beforeDraw(chart,args,opts){
    const {ctx,chartArea,scales}=chart; if(!chartArea) return; const x=scales.x;
    (opts.bands||[]).forEach(b=>{ const x1=x.getPixelForValue(b.start), x2=x.getPixelForValue(b.end);
      if(x1==null||x2==null) return; ctx.save(); ctx.fillStyle=b.color||'rgba(0,0,0,0.04)';
      ctx.fillRect(Math.min(x1,x2),chartArea.top,Math.abs(x2-x1),chartArea.bottom-chartArea.top); ctx.restore(); });
    (opts.refs||[]).forEach(r=>{ const px=x.getPixelForValue(r.date);
      if(px==null||isNaN(px)) return; ctx.save();
      ctx.strokeStyle=r.color||'#888'; ctx.lineWidth=r.width||1.3; ctx.setLineDash(r.dash||[5,4]);
      ctx.beginPath(); ctx.moveTo(px,chartArea.top); ctx.lineTo(px,chartArea.bottom); ctx.stroke();
      if(r.label){ ctx.setLineDash([]); ctx.fillStyle=r.color||'#888';
        ctx.font='600 10px -apple-system,sans-serif'; ctx.translate(px+3,chartArea.top+5);
        ctx.rotate(Math.PI/2); ctx.textAlign='left'; ctx.fillText(r.label,0,0); }
      ctx.restore(); });
  }};
  const yBandPlugin = { id:'yBand', beforeDraw(chart,args,opts){
    const {ctx,chartArea,scales}=chart; if(!chartArea) return; const y=scales.y;
    (opts.bands||[]).forEach(b=>{ const y1=y.getPixelForValue(b.from), y2=y.getPixelForValue(b.to);
      if(y1==null||y2==null) return; ctx.save(); ctx.fillStyle=b.color||'rgba(0,0,0,0.05)';
      ctx.fillRect(chartArea.left,Math.min(y1,y2),chartArea.right-chartArea.left,Math.abs(y2-y1)); ctx.restore(); });
  }};
  Chart.register(refLinePlugin, yBandPlugin);
  Chart.defaults.font.family="'Inter',-apple-system,BlinkMacSystemFont,Segoe UI,Roboto,sans-serif";
  Chart.defaults.font.size=11; Chart.defaults.color='#5a5c50';
  Chart.defaults.plugins.legend.labels.usePointStyle=true;
  Chart.defaults.plugins.legend.labels.boxWidth=8;
  Chart.defaults.plugins.legend.labels.padding=14;

  // 01 — setup anchor
  (function(){ const s=d.setup; if(!el('setupAnchorChart')||!s.dates.length) return;
    const bid1=s.dates.map(()=>s.bid1), bid2=s.dates.map(()=>s.bid2);
    new Chart(el('setupAnchorChart'),{type:'line',data:{labels:s.dates,datasets:[
      {label:'LWAY close',data:s.close,borderColor:A,backgroundColor:'rgba(31,78,121,0.08)',
        borderWidth:2,fill:true,tension:.15,pointRadius:0},
      {label:'Danone $27 bid',data:bid2,borderColor:NEG,borderWidth:1.5,borderDash:[6,4],pointRadius:0,fill:false},
      {label:'Danone $25 bid',data:bid1,borderColor:'#9a7b1f',borderWidth:1.2,borderDash:[3,4],pointRadius:0,fill:false}
    ]},options:{responsive:true,maintainAspectRatio:false,interaction:{mode:'index',intersect:false},
      plugins:{refLine:{refs:s.refs,bands:[]}},
      scales:{x:{type:'time',time:{unit:'month'},grid:{display:false},
        ticks:{maxRotation:0,autoSkip:true,maxTicksLimit:10}},
        y:{title:{display:true,text:'Price ($)'},grid:grid()}}}});
  })();

  // 02 — events (price + reaction dots)
  (function(){ const ne=d.news.events; if(!el('eventsChart')||!ne.dates.length) return;
    const dots=ne.events.filter(e=>e.y!=null).map(e=>({x:e.date,y:e.y,_e:e}));
    new Chart(el('eventsChart'),{type:'line',data:{datasets:[
      {label:'LWAY close',data:ne.dates.map((dt,i)=>({x:dt,y:ne.close[i]})),borderColor:A,
        backgroundColor:'rgba(31,78,121,0.08)',borderWidth:2,fill:true,tension:.15,pointRadius:0,order:2},
      {label:'Corporate events',type:'scatter',data:dots,pointRadius:7,pointHoverRadius:9,
        pointBackgroundColor:dots.map(p=>d.reaction_colors[p._e.reaction_kind]||'#888'),
        pointBorderColor:'#fff',pointBorderWidth:1.5,order:1}
    ]},options:{responsive:true,maintainAspectRatio:false,
      plugins:{legend:{position:'bottom'},tooltip:{callbacks:{label:function(ctx){
        const e=ctx.raw&&ctx.raw._e;
        if(e){ const r=(e.reaction_pct!=null)?('1-day: '+(e.reaction_pct>0?'+':'')+e.reaction_pct+'%'):'';
          return [e.headline, r]; }
        return 'LWAY $'+(ctx.parsed.y!=null?ctx.parsed.y.toFixed(2):''); }}}},
      scales:{x:{type:'time',time:{unit:'month'},grid:{display:false},
        ticks:{maxRotation:0,autoSkip:true,maxTicksLimit:10}},
        y:{title:{display:true,text:'Price ($)'},grid:grid()}}}});
  })();

  // 02 — topic mix (stacked area)
  (function(){ const tm=d.news.topics; if(!el('topicMixChart')||!tm.weeks.length) return;
    const ds=Object.keys(tm.series).map(t=>({label:d.topic_labels[t]||t,
      data:tm.weeks.map((w,i)=>({x:w,y:tm.series[t][i]})),
      borderColor:d.topic_colors[t]||'#888',backgroundColor:(d.topic_colors[t]||'#888')+'88',
      fill:true,tension:.2,pointRadius:0,borderWidth:1}));
    new Chart(el('topicMixChart'),{type:'line',data:{datasets:ds},
      options:{responsive:true,maintainAspectRatio:false,interaction:{mode:'index',intersect:false},
        plugins:{legend:{position:'bottom'}},
        scales:{x:{type:'time',time:{unit:'week'},grid:{display:false}},
          y:{stacked:true,title:{display:true,text:'Articles / week'},grid:grid()}}}});
  })();

  // 03 — milk (dual axis)
  (function(){ const mk=d.milk; if(!el('milkChart')||!mk.classiii.months.length) return;
    const ds=[{label:'Class III ($/cwt)',data:mk.classiii.months.map((m,i)=>({x:m,y:mk.classiii.vals[i]})),
      borderColor:A,backgroundColor:'rgba(31,78,121,0.07)',borderWidth:2,fill:true,tension:.2,pointRadius:0,yAxisID:'y'}];
    if(mk.cpi.months.length){ ds.push({label:'CPI retail milk ($/gal)',
      data:mk.cpi.months.map((m,i)=>({x:m,y:mk.cpi.vals[i]})),borderColor:A2,borderWidth:2,
      borderDash:[5,3],fill:false,tension:.2,pointRadius:0,yAxisID:'y1'}); }
    new Chart(el('milkChart'),{type:'line',data:{datasets:ds},
      options:{responsive:true,maintainAspectRatio:false,interaction:{mode:'index',intersect:false},
        plugins:{legend:{position:'bottom'}},
        scales:{x:{type:'time',time:{unit:'month'},grid:{display:false}},
          y:{position:'left',title:{display:true,text:'$ / cwt'},grid:grid()},
          y1:{position:'right',title:{display:true,text:'$ / gal'},grid:{display:false}}}}});
  })();

  // 04 — brand share of voice (stacked bars)
  (function(){ const sv=d.brand.sov; if(!el('brandSovChart')||!sv.weeks.length) return;
    const ds=sv.order.filter(b=>sv.series[b]).map(b=>({label:b,
      data:sv.weeks.map((w,i)=>({x:w,y:sv.series[b][i]})),
      backgroundColor:d.sov_colors[b]||'#888',borderWidth:0}));
    new Chart(el('brandSovChart'),{type:'bar',data:{datasets:ds},
      options:{responsive:true,maintainAspectRatio:false,
        plugins:{legend:{position:'bottom'}},
        scales:{x:{type:'time',time:{unit:'week'},stacked:true,grid:{display:false}},
          y:{stacked:true,title:{display:true,text:'Mentions / week'},grid:grid()}}}});
  })();

  // 04 — reddit lway vs category (dual axis)
  (function(){ const rd=d.brand.reddit; if(!el('redditChart')||!rd.weeks.length) return;
    new Chart(el('redditChart'),{type:'line',data:{datasets:[
      {label:'Lifeway brand',data:rd.weeks.map((w,i)=>({x:w,y:rd.lway[i]})),borderColor:A,
        backgroundColor:'rgba(31,78,121,0.08)',borderWidth:2,fill:true,tension:.2,pointRadius:0,yAxisID:'y'},
      {label:'Kefir category',data:rd.weeks.map((w,i)=>({x:w,y:rd.category[i]})),borderColor:A2,
        borderWidth:2,fill:false,tension:.2,pointRadius:0,yAxisID:'y1'}
    ]},options:{responsive:true,maintainAspectRatio:false,interaction:{mode:'index',intersect:false},
      plugins:{legend:{position:'bottom'}},
      scales:{x:{type:'time',time:{unit:'week'},grid:{display:false}},
        y:{position:'left',title:{display:true,text:'Lifeway mentions'},grid:grid()},
        y1:{position:'right',title:{display:true,text:'Category mentions'},grid:{display:false}}}}});
  })();

  // 05 — quarterly revenue YoY (bars)
  (function(){ const q=d.fin.qrev; if(!el('qrevChart')||!q.labels.length) return;
    const estColors=q.colors.map(c=>c+'55');
    new Chart(el('qrevChart'),{type:'bar',data:{labels:q.labels,datasets:[
      {label:'Actual',data:q.actual,backgroundColor:q.colors,borderWidth:0},
      {label:'Estimate',data:q.est,backgroundColor:estColors,borderColor:q.colors,borderWidth:1.5}
    ]},options:{responsive:true,maintainAspectRatio:false,
      plugins:{legend:{position:'bottom'},tooltip:{callbacks:{afterBody:function(items){
        const i=items[0].dataIndex; return q.notes[i]||''; }}}},
      scales:{x:{grid:{display:false}},y:{title:{display:true,text:'YoY %'},grid:grid()}}}});
  })();

  // 05 — gross margin trajectory (line + target band)
  (function(){ const g=d.fin.gm; if(!el('gmChart')||!g.labels.length) return;
    const bands=d.fin.gm_band?[{from:d.fin.gm_band.low,to:d.fin.gm_band.high,color:'rgba(31,78,121,0.10)'}]:[];
    new Chart(el('gmChart'),{type:'line',data:{labels:g.labels,datasets:[
      {label:'Actual',data:g.actual,borderColor:A,backgroundColor:'rgba(31,78,121,0.08)',
        borderWidth:2.5,fill:true,tension:.2,pointRadius:3},
      {label:'Estimate',data:g.est,borderColor:A,borderWidth:2,borderDash:[6,4],
        fill:false,tension:.2,pointRadius:3,spanGaps:true}
    ]},options:{responsive:true,maintainAspectRatio:false,
      plugins:{legend:{position:'bottom'},yBand:{bands:bands}},
      scales:{x:{grid:{display:false}},
        y:{title:{display:true,text:'Gross margin %'},grid:grid(),suggestedMin:22,suggestedMax:31}}}});
  })();

  // 05 — lifeway vs category (line)
  (function(){ const ct=d.fin.cat; if(!el('catGrowthChart')||!ct.labels.length) return;
    new Chart(el('catGrowthChart'),{type:'line',data:{labels:ct.labels,datasets:[
      {label:'Lifeway YoY',data:ct.lway,borderColor:A,backgroundColor:'rgba(31,78,121,0.08)',
        borderWidth:2.5,fill:true,tension:.2,pointRadius:3},
      {label:'Kefir category YoY',data:ct.category,borderColor:A2,borderWidth:2,borderDash:[5,3],
        fill:false,tension:.2,pointRadius:3}
    ]},options:{responsive:true,maintainAspectRatio:false,
      plugins:{legend:{position:'bottom'}},
      scales:{x:{grid:{display:false}},y:{title:{display:true,text:'YoY %'},grid:grid()}}}});
  })();

  // 06 — demand vs tape (dual axis)
  (function(){ const dm=d.demand; if(!el('demandChart')||!dm.weeks.length) return;
    new Chart(el('demandChart'),{type:'line',data:{datasets:[
      {label:'Social demand (z, 4-wk avg)',data:dm.weeks.map((w,i)=>({x:w,y:dm.demand_z[i]})),borderColor:A2,
        backgroundColor:'rgba(232,181,77,0.15)',borderWidth:2,fill:true,tension:.25,pointRadius:0,yAxisID:'y'},
      {label:'LWAY (indexed=100)',data:dm.weeks.map((w,i)=>({x:w,y:dm.stock_idx[i]})),borderColor:A,
        borderWidth:2,fill:false,tension:.15,pointRadius:0,yAxisID:'y1',spanGaps:true}
    ]},options:{responsive:true,maintainAspectRatio:false,interaction:{mode:'index',intersect:false},
      plugins:{legend:{position:'bottom'}},
      scales:{x:{type:'time',time:{unit:'week'},grid:{display:false}},
        y:{position:'left',title:{display:true,text:'Demand (z)'},grid:grid()},
        y1:{position:'right',title:{display:true,text:'Price (indexed)'},grid:{display:false}}}}});
  })();
});
"""


def _json_default(o):
    try:
        import numpy as np
        if isinstance(o, np.integer):
            return int(o)
        if isinstance(o, np.floating):
            return float(o)
    except Exception:
        pass
    if hasattr(o, "item"):
        try:
            return o.item()
        except Exception:
            pass
    return str(o)


def build_html(body: str, blob: dict) -> str:
    nav = ('<a class="nav-btn" href="#quick-read">Quick read</a>'
           '<a class="nav-btn" href="#setup">Setup</a>'
           '<a class="nav-btn" href="#news">News</a>'
           '<a class="nav-btn" href="#milk">Milk</a>'
           '<a class="nav-btn" href="#brand">Brand</a>'
           '<a class="nav-btn" href="#financial">Financials</a>'
           '<a class="nav-btn" href="#demand">Demand</a>')
    topbar = ('<div class="topbar"><div class="topbar-inner">'
              f'<h1>{BRAND_NAME} <span>· {TICKER} demand &amp; control</span></h1>'
              f'<div class="topbar-nav">{nav}'
              '<button class="summary-btn" onclick="document.getElementById(\'summaryModal\')'
              '.style.display=\'flex\'">Executive summary</button></div></div></div>')
    blob_json = json.dumps(blob, default=_json_default, ensure_ascii=False, allow_nan=True)
    script = "<script>\nwindow.__lway = " + blob_json + ";\n" + JS_STATIC + "\n</script>"
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f'<title>{BRAND_NAME} ({TICKER}) — demand &amp; control dashboard</title>'
        '<link rel="preconnect" href="https://fonts.googleapis.com">'
        '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>'
        '<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap" rel="stylesheet">'
        '<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>'
        '<script src="https://cdn.jsdelivr.net/npm/chartjs-adapter-date-fns@3.0.0/'
        'dist/chartjs-adapter-date-fns.bundle.min.js"></script>'
        f'<style>{CSS}</style></head><body>'
        f'{topbar}{body}'
        f'{script}</body></html>'
    )


def _dump_ledger() -> None:
    """Write the callout anchor audit (which metric each line is wired to, and
    any line that could not be anchored) to reads/anchor_ledger.md + stderr."""
    import sys
    total = len(CALLOUT_LEDGER)
    flagged = [c for c in CALLOUT_LEDGER if not c["anchored"]]
    lines = ["# Callout anchor ledger",
             "",
             f"_Generated {GENERATED_AT:%Y-%m-%d %H:%M} · {total} interpretive lines · "
             f"{total - len(flagged)} anchored · {len(flagged)} flagged_",
             ""]
    for c in CALLOUT_LEDGER:
        status = "OK  " if c["anchored"] else "FLAG"
        metrics = ", ".join(c["used"]) or "(no placeholders)"
        miss = f"  ·  MISSING: {', '.join(c['missing'])}" if c["missing"] else ""
        lines.append(f"- `{status}` **{c['where']}** ({c['kind']}) → {metrics}{miss}")
    (READS / "anchor_ledger.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"  · callouts: {total} interpretive lines, {len(flagged)} flagged "
          f"(audit → reads/anchor_ledger.md)", file=sys.stderr)
    for c in flagged:
        why = ", ".join(c["missing"]) if c["missing"] else "no placeholders (opinion-only)"
        print(f"    ! UNANCHORED {c['where']} ({c['kind']}): {why}", file=sys.stderr)


def main():
    D = load_all()
    qr = compute_quick_read(D)
    fin = compute_financial(D)
    setup = compute_setup(D)
    news = compute_stock_news(D)
    milk = compute_milk(D)
    brand = compute_brand(D)
    demand = compute_demand_vs_stock(D)
    M = compute_metrics(D, qr, fin, brand, milk, demand)
    blob = {
        "accent": BRAND_ACCENT, "accent2": BRAND_ACCENT2, "accent3": BRAND_ACCENT3,
        "accent_neg": BRAND_NEG, "purple": BRAND_PURPLE, "brown": BRAND_BROWN, "blue": BRAND_BLUE,
        "topic_colors": TOPIC_COLORS, "topic_labels": TOPIC_LABELS,
        "reaction_colors": REACTION_COLORS, "sov_colors": BRAND_SOV_COLORS,
        "setup": setup, "news": news, "milk": milk, "brand": brand,
        "fin": fin, "demand": demand,
    }

    def sect(html: str, key: str) -> str:
        return html + render_callouts(key, M, D)

    body = (
        '<div class="container">'
        + render_top_callout(D)
        + render_the_situation(D, M)
        + render_dashboard_questions(M)
        + sect(render_quick_read(D, qr), "quick-read")
        + sect(render_setup(D), "setup")
        + sect(render_news(D), "news")
        + sect(render_milk(D), "milk")
        + sect(render_brand(D), "brand")
        + sect(render_financial(D, fin), "financial")
        + sect(render_demand(D), "demand")
        + '</div>'
        + f'<footer>{refresh_footer()}</footer>'
        + render_summary_modal(D, qr, fin)
    )
    html = build_html(body, blob)
    OUT_HTML.write_text(html, encoding="utf-8")
    print(f"✓ wrote {OUT_HTML}  ({len(html):,} bytes)")
    _dump_ledger()


if __name__ == "__main__":
    main()




