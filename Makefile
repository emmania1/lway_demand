# Prefer the project venv (created by `make install`); otherwise fall back to a
# system python3 that already has the deps, so `make generate` runs from a clone.
PY := $(shell [ -x venv/bin/python ] && echo venv/bin/python || echo python3)

.PHONY: help refresh-data refresh-fast generate venv install clean-data

help:
	@echo "LWAY (Lifeway Foods) Demand Dashboard"
	@echo ""
	@echo "  make venv          create local virtualenv at ./venv"
	@echo "  make install       install requirements into ./venv"
	@echo "  make refresh-data  run all fetchers in order (stock + reddit + competitors + news + milk + youtube)"
	@echo "  make refresh-fast  skip youtube (no API key needed)"
	@echo "  make generate      regenerate index.html from existing /data CSVs"
	@echo "  make clean-data    remove all CSVs in /data (configs and fetchers untouched)"

venv:
	python3 -m venv venv

install: venv
	./venv/bin/pip install -U pip
	./venv/bin/pip install -r requirements.txt

refresh-data:
	@echo "── stock ────────────────────────────────────────────"
	$(PY) scripts/fetch_stock_price.py
	@echo "── reddit (lifeway + kefir category pass) ───────────"
	$(PY) scripts/fetch_reddit_arctic.py
	@echo "── competitor mentions (kefir/cultured-dairy set) ───"
	$(PY) scripts/fetch_competitor_mentions.py
	@echo "── news (gdelt + google news rss) ───────────────────"
	$(PY) scripts/fetch_google_news.py
	@echo "── milk prices (BLS CPI + seed for USDA Class III) ──"
	$(PY) scripts/fetch_milk_prices.py
	@echo "── youtube (skipped if YOUTUBE_API_KEY missing) ─────"
	$(PY) scripts/fetch_youtube.py || echo "  [warn] youtube fetcher failed — continuing"
	@echo "── regenerate dashboard ─────────────────────────────"
	$(PY) scripts/generate_lway_dashboard.py

refresh-fast:
	@echo "── stock ────────────────────────────────────────────"
	$(PY) scripts/fetch_stock_price.py
	@echo "── reddit ───────────────────────────────────────────"
	$(PY) scripts/fetch_reddit_arctic.py
	@echo "── competitor mentions ──────────────────────────────"
	$(PY) scripts/fetch_competitor_mentions.py
	@echo "── news ─────────────────────────────────────────────"
	$(PY) scripts/fetch_google_news.py
	@echo "── milk prices ──────────────────────────────────────"
	$(PY) scripts/fetch_milk_prices.py
	@echo "── regenerate dashboard ─────────────────────────────"
	$(PY) scripts/generate_lway_dashboard.py

generate:
	$(PY) scripts/generate_lway_dashboard.py

clean-data:
	rm -f data/*.csv data/*.json
