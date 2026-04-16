"""Configuration for the Google Ads scraper."""

# Browser settings
# Headless Chromium is flagged heavily by Google Search; use headed (False) when possible.
HEADLESS_MODE = False
# If True, launches your installed Google Chrome (Playwright channel). Requires Chrome installed.
USE_SYSTEM_CHROME = False
# Visit google.com briefly before the search URL (more human-like navigation).
WARMUP_GOOGLE_HOME = True
WARMUP_DELAY_MIN = 1.0
WARMUP_DELAY_MAX = 2.5
TIMEOUT_PAGE_LOAD = 30000
TIMEOUT_SELECTOR = 8000
# Longer wait when resolving sponsored blocks after layout/CAPTCHA
EXTRACT_SPONSORED_TIMEOUT_MS = 12000

# One browser context + tab for all searches (easier manual CAPTCHA; less isolation).
PERSISTENT_MODE = True
VIEWPORT_WIDTH = 1920
VIEWPORT_HEIGHT = 1080

# Extra Chromium flags (Linux/Docker sometimes needs "--no-sandbox" — add there if needed)
CHROMIUM_EXTRA_ARGS = [
    "--disable-blink-features=AutomationControlled",
    "--disable-features=IsolateOrigins,site-per-process",
]

# After search results load: brief pause + optional mouse/scroll (behavioral signal smoothing)
POST_SEARCH_SETTLE_MIN = 0.5
POST_SEARCH_SETTLE_MAX = 1.5
HUMAN_MOUSE_AND_SCROLL = True

# Scraping settings
# Conservative test profile (harder IPs / CAPTCHA):2 iterations, slower pacing.
# For full spec runs, set ITERATIONS_PER_QUERY = 10 and tighten delays if your IP/proxy is stable.
ITERATIONS_PER_QUERY = 2
DELAY_BETWEEN_ITERATIONS_MIN = 8
DELAY_BETWEEN_ITERATIONS_MAX = 12
DELAY_BETWEEN_QUERIES = 5
DELAY_BETWEEN_QUERIES_JITTER_MIN = 0.5
DELAY_BETWEEN_QUERIES_JITTER_MAX = 2.5

# Retry settings
MAX_RETRIES_ON_FAILURE = 3
RETRY_BACKOFF_MULTIPLIER = 2

# CAPTCHA handling
MAX_CAPTCHA_ENCOUNTERS = 3
CAPTCHA_COOLDOWN = 60
# If Google shows a verification page in headed mode, wait for you to solve it in the
# browser, then press Enter in the terminal to continue (no effect when HEADLESS_MODE True).
PAUSE_FOR_MANUAL_CAPTCHA = True
# Beep / system alert when a verification page is detected (Windows: winsound; else terminal bell).
CAPTCHA_ALERT_SOUND = True
# Seconds to solve in the browser before we re-check the page (then Enter if still blocked).
CAPTCHA_GRACE_SECONDS = 30

# Compliance
COMPLIANCE_MODE = True

# Proxy settings — residential US exit is the most reliable fix for non-US IP → US SERP.
PROXY_ENABLED = False
PROXY_STRING = "http://user:pass@residential-proxy:port"

# Output and logging
INPUT_FILE = "input_queries.csv"
OUTPUT_FILE = "results.csv"
LOG_FILE = "scraper.log"

# Deduplication
NORMALIZE_URLS = True
