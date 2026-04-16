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

## If you see `CAPTCHA` in `scraper.log`

Google often blocks **headless** automation. This project defaults to **headed** mode (`HEADLESS_MODE = False` in `config.py`) so a real browser window opens.

Searching **US results from a non-US IP** (e.g. India → Austin) triggers blocks quickly; **residential US proxies** are usually the difference between “always CAPTCHA” and “sometimes works.”

- Set `PROXY_ENABLED = True` and `PROXY_STRING` in `config.py` (residential / mobile US exit, not datacenter).
- Defaults are tuned for difficult IPs: `ITERATIONS_PER_QUERY = 2` and 8–12s between iterations. For the full 10 runs per query, set `ITERATIONS_PER_QUERY = 10` in `config.py` (and adjust delays if stable).
- If blocks continue locally, set `USE_SYSTEM_CHROME = True` (Google Chrome must be installed).
- **Stealth:** this repo uses `playwright-stealth` **v2** (`Stealth().apply_stealth_async(context)`). Older snippets that use `stealth_async` are for a different API.

- Wait between sessions if you hit repeated challenges; the scraper backs off after several CAPTCHA events.

## Legal notice

This tool is intended only for internal market research. Automated scraping of Google Search may violate Google's Terms of Service and robots policy. Review legal/compliance implications before use.
