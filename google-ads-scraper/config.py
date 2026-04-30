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
TIMEOUT_PAGE_LOAD = 45000
TIMEOUT_SELECTOR = 8000
# Longer wait when resolving sponsored blocks after layout/CAPTCHA
EXTRACT_SPONSORED_TIMEOUT_MS = 15000
# Extra settle + scroll before reading the SERP (increase after manual CAPTCHA if ads load late).
EXTRACT_PAGE_SETTLE_SEC = 2.0
EXTRACT_INITIAL_SCROLL_PAUSE_SEC = 1.5
# Primary wait for grouped text-ad blocks (`[data-text-ad="1"]`).
DATA_TEXT_AD_WAIT_MS = 8000
# After CAPTCHA/consent, re-open SERP if URL is not a Google search page.
SERP_VERIFY_TIMEOUT_MS = 15000
SERP_RENAV_MAX_ATTEMPTS = 2

# Run each query in a fresh browser session (restart per CSV row).
PERSISTENT_MODE = False
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
# Default 5 iterations per CSV row; reduce if you hit CAPTCHA often, or tune delays for your IP/proxy.
ITERATIONS_PER_QUERY = 5
DELAY_BETWEEN_ITERATIONS_MIN = 8
DELAY_BETWEEN_ITERATIONS_MAX = 12
DELAY_BETWEEN_QUERIES = 5
DELAY_BETWEEN_QUERIES_JITTER_MIN = 0.5
DELAY_BETWEEN_QUERIES_JITTER_MAX = 2.5

# Retry settings
MAX_RETRIES_ON_FAILURE = 3
RETRY_BACKOFF_MULTIPLIER = 2
PERSISTENT_NAV_RETRIES = 3

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

# --- Email enrichment (post-process results.csv) ---
EMAIL_ENRICH_ENABLED = True
EMAIL_ENRICH_INPUT = "results.csv"
EMAIL_ENRICH_OUTPUT = "results_enriched.csv"
EMAIL_ENRICH_LOG = "email_enrich.log"
EMAIL_ENRICH_MAX_PAGES_PER_DOMAIN = 8
EMAIL_ENRICH_NAV_TIMEOUT_MS = 25000
EMAIL_ENRICH_CONCURRENCY = 2
EMAIL_ENRICH_DELAY_MIN = 1.0
EMAIL_ENRICH_DELAY_MAX = 2.5
EMAIL_ENRICH_USE_HEADLESS = True
EMAIL_ENRICH_CHROME_ARGS = [
    "--disable-blink-features=AutomationControlled",
]
EMAIL_ENRICH_USE_ROLE_GUESSES = True
# Role permutations when crawl finds no email (info + support first; deduped at runtime).
EMAIL_ENRICH_ROLE_LOCALPARTS = [
    "info",
    "support",
    "contact",
    "hello",
    "office",
]
EMAIL_ENRICH_USE_HEADLINE_GUESS = True
EMAIL_ENRICH_MAX_GUESS_CANDIDATES = 24
EMAIL_ENRICH_SMTP_ENABLED = False
# Truth Reactor runs per domain (one verify_safe per candidate, in order, up to this cap).
EMAIL_ENRICH_MAX_SMTP_PROBES_PER_DOMAIN = 32
# If True, stop after a strong SMTP deliverable (skips verifying remaining candidates).
EMAIL_ENRICH_VERIFY_EARLY_STOP = False
EMAIL_ENRICH_SMTP_TIMEOUT_SECONDS = 15.0
EMAIL_ENRICH_SMTP_CATCHALL_TIMEOUT_SECONDS = 12.0
EMAIL_ENRICH_REACTOR_LIST_UPDATE_INTERVAL_SECONDS = 604800.0
# Empty strings = default under ./data/
EMAIL_ENRICH_REACTOR_CACHE_DB = ""
EMAIL_ENRICH_REACTOR_LIST_DIR = ""
# Only used when EMAIL_ENRICH_VERIFY_EARLY_STOP and SMTP are enabled.
EMAIL_ENRICH_VERIFY_EARLY_STOP_CONFIDENCE = 88.0
