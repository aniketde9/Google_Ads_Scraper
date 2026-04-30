# Google Ads Scraper

Python scraper for collecting sponsored Google search results from CSV queries.

## Setup

1. Create and use a virtual environment (from this folder):
   - `python -m venv .venv`
   - PowerShell: `.\.venv\Scripts\Activate.ps1`
   - Command Prompt: `.venv\Scripts\activate.bat`
2. Install dependencies:
   - `pip install -r requirements.txt`
   - `playwright install chromium`
3. Put your input file at `input_queries.csv` with columns:
   - `profession,location,pincode`
4. Run:
   - `python main.py`

## Email enrichment (`results.csv` → `results_enriched.csv`)

After the main scraper writes `results.csv`, you can crawl advertiser sites, extract or guess contact emails, and optionally verify them (DNS / disposable lists / optional SMTP).

1. In `config.py`, set **`EMAIL_ENRICH_ENABLED = True`** (or pass **`--force`** on the CLI).
2. Defaults: **`EMAIL_ENRICH_INPUT`** = `results.csv`, **`EMAIL_ENRICH_OUTPUT`** = `results_enriched.csv`, log file **`email_enrich.log`**. **`enriched_email`** is only filled after Truth Reactor **`verify_safe`** (no unverified fallbacks). Role guesses include **`info@`** and **`support@`** first; tune **`EMAIL_ENRICH_MAX_SMTP_PROBES_PER_DOMAIN`** and **`EMAIL_ENRICH_VERIFY_EARLY_STOP`** (default off = verify every candidate up to the cap).
3. Run from this folder:
   - `python enrich_results_emails.py`
   - `python enrich_results_emails.py --limit 10` (first 10 unique domains)
   - `python enrich_results_emails.py --dry-run` (no Playwright or verification)
   - `python enrich_results_emails.py --input path/to/results.csv --output path/out.csv`

**SMTP verification** is **off by default** (`EMAIL_ENRICH_SMTP_ENABLED = False`). Many networks block outbound port 25; enabling SMTP sends RCPT probes that can upset mail admins or be treated as abuse. Only turn it on where you have permission and a suitable network. The Truth Reactor caches disposable/role lists under **`data/`** inside this project (created on first run).

**Ethics / compliance:** Use enrichment only for lawful, permitted purposes. Respect site terms, rate limits, and privacy. This does not bypass contact forms or logins.

## Browser session (`PERSISTENT_MODE`)

By default **`PERSISTENT_MODE = True`**: one Chromium context and tab stay open for all CSV rows and iterations; each run navigates with `?q=...` (same effect as using the search bar). Set **`PERSISTENT_MODE = False`** for a fresh incognito context every iteration (stricter isolation, more CAPTCHAs when solving manually).

Sponsored extraction prefers **`[data-text-ad="1"]`** (grouped text-ad cards), then the **“Sponsored results”** heading with an ancestor **`div`** scope, then **`uEierd`**, label walks, and a small **JS** fallback. After a **CAPTCHA** flow, **`ensure_on_google_serp`** checks that the URL is a Google **`/search`** page and re-navigates to the query if not (see **`SERP_RENAV_MAX_ATTEMPTS`** / **`SERP_VERIFY_TIMEOUT_MS`**). Tune **`EXTRACT_PAGE_SETTLE_SEC`** if ads load late.

## If you see `CAPTCHA` in `scraper.log`

Google often blocks **headless** automation. This project defaults to **headed** mode (`HEADLESS_MODE = False` in `config.py`) so a real browser window opens.

Searching **US results from a non-US IP** (e.g. India → Austin) triggers blocks quickly; **residential US proxies** are usually the difference between “always CAPTCHA” and “sometimes works.”

- Set `PROXY_ENABLED = True` and `PROXY_STRING` in `config.py` (residential / mobile US exit, not datacenter).
- Defaults are tuned for difficult IPs: `ITERATIONS_PER_QUERY = 2` and 8–12s between iterations. For the full 10 runs per query, set `ITERATIONS_PER_QUERY = 10` in `config.py` (and adjust delays if stable).
- If blocks continue locally, set `USE_SYSTEM_CHROME = True` (Google Chrome must be installed).
- **Stealth:** this repo uses `playwright-stealth` **v2** (`Stealth().apply_stealth_async(context)`). Older snippets that use `stealth_async` are for a different API.

- **Manual verification:** with headed mode (`HEADLESS_MODE = False`), set `PAUSE_FOR_MANUAL_CAPTCHA = True` in `config.py`. When Google shows a challenge, the scraper plays an **alert sound** (Windows beeps; otherwise terminal bell), waits **`CAPTCHA_GRACE_SECONDS`** (default 30) for you to verify in the browser, then continues automatically if the page cleared. If not, press **Enter** in the terminal after finishing. Toggle with `CAPTCHA_ALERT_SOUND` / adjust time with `CAPTCHA_GRACE_SECONDS`.

- Wait between sessions if you hit repeated challenges; the scraper backs off after several CAPTCHA events.

## Legal notice

This tool is intended only for internal market research. Automated scraping of Google Search may violate Google's Terms of Service and robots policy. Review legal/compliance implications before use.
