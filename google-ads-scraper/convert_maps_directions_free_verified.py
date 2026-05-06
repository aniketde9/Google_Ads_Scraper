"""Free-only conversion of Maps 'Directions' rows to probable official websites.

This script does not use paid APIs. It:
1) Finds candidate sites via DuckDuckGo HTML search.
2) Verifies candidates with multi-signal scoring from page/title content.
3) Updates only high-confidence rows; leaves uncertain rows untouched.

Usage:
  python convert_maps_directions_free_verified.py
  python convert_maps_directions_free_verified.py --input May5_results_ads.csv --output May5_results_ads_verified_free_v2.csv
  python convert_maps_directions_free_verified.py --min-verified-score 8 --delay-seconds 2.5
"""

from __future__ import annotations

import argparse
import base64
import atexit
import re
import time
from dataclasses import dataclass
from pathlib import Path
import tempfile
from typing import Iterable
from urllib.parse import parse_qs, unquote_plus, urlparse

import pandas as pd
import requests
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright


DDG_ENDPOINT = "https://html.duckduckgo.com/html/"
BING_ENDPOINT = "https://www.bing.com/search"
GOOGLE_ENDPOINT = "https://www.google.com/search"
HTTP_TIMEOUT_SECONDS = 12
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
# Search engines can serve challenge/locale pages for richer UAs.
# A simpler UA is more stable for lightweight HTML result scraping.
SEARCH_USER_AGENT = "Mozilla/5.0"
_GOOGLE_BROWSER_SEARCHER = None

# Directory/listing domains that are rarely official websites.
BLOCKED_DOMAINS = {
    "yelp.com",
    "medifind.com",
    "zocdoc.com",
    "healthgrades.com",
    "vitals.com",
    "sharecare.com",
    "carecredit.com",
    "yellowpages.com",
    "mapquest.com",
    "doximity.com",
    "findatopdoc.com",
    "caredash.com",
    "superpages.com",
    "wellness.com",
    "npino.com",
}

SUFFIX_STOPWORDS = {
    "llc",
    "inc",
    "co",
    "corp",
    "pllc",
    "ltd",
    "md",
    "m.d",
    "dr",
    "doctor",
    "clinic",
    "center",
    "group",
    "associates",
}

GENERIC_MATCH_TOKENS = {
    "dermatology",
    "dermatologist",
    "medical",
    "clinic",
    "center",
    "group",
    "institute",
    "skin",
    "care",
    "office",
}

LOCATION_NOISE_TOKENS = {
    "north",
    "south",
    "east",
    "west",
    "center",
    "loop",
    "office",
    "downtown",
    "uptown",
    "bucktown",
    "river",
    "heights",
    "park",
    "plaza",
}


@dataclass
class MatchResult:
    website_name: str | None
    url: str | None
    domain: str | None
    score: int
    reason: str


def normalize_text(value: str) -> str:
    value = (value or "").lower()
    value = re.sub(r"[^a-z0-9\s]", " ", value)
    value = re.sub(r"\s+", " ", value).strip()
    return value


def normalize_business_name(name: str) -> str:
    tokens = normalize_text(name).split()
    kept = [t for t in tokens if t not in SUFFIX_STOPWORDS]
    return " ".join(kept)


def extract_domain(url: str) -> str:
    try:
        host = (urlparse(url).hostname or "").lower()
    except ValueError:
        return ""
    if host.startswith("www."):
        host = host[4:]
    return host


def parse_business_name_from_maps_url(maps_url: str) -> str:
    """Extract business name from /maps/dir//... URL."""
    raw_url = str(maps_url or "")
    # Prefer /dir//name,address/data= format (as seen in your CSV).
    match = re.search(r"/dir//(.*?)(?:,|/data=)", raw_url)
    if not match:
        return ""
    name = unquote_plus(match.group(1)).strip()
    name = re.sub(r"\s+", " ", name)
    return name


def parse_maps_hint_from_url(maps_url: str) -> dict[str, str]:
    """Extract richer text hint from /maps/dir//.../data= URLs."""
    raw_url = str(maps_url or "")
    try:
        parsed = urlparse(raw_url)
    except ValueError:
        return {"name": "", "full_hint": "", "address_hint": ""}
    path = parsed.path or ""
    match = re.search(r"/dir//(.*?)/data=", path)
    if not match:
        name = parse_business_name_from_maps_url(raw_url)
        return {"name": name, "full_hint": name, "address_hint": ""}

    decoded = unquote_plus(match.group(1))
    decoded = re.sub(r"\s+", " ", decoded).strip()
    parts = [p.strip() for p in decoded.split(",") if p.strip()]
    name = parts[0] if parts else parse_business_name_from_maps_url(raw_url)
    address_hint = ", ".join(parts[1:]) if len(parts) > 1 else ""
    return {"name": name, "full_hint": decoded, "address_hint": address_hint}


def is_directions_maps_row(row: pd.Series) -> bool:
    website_name = str(row.get("website_name", "")).strip().lower()
    url = str(row.get("url", "")).strip().lower()
    return website_name == "directions" and "google.com/maps" in url


def domain_is_blocked(url: str) -> bool:
    domain = extract_domain(url)
    return any(domain == d or domain.endswith(f".{d}") for d in BLOCKED_DOMAINS)


def resolve_ddg_result_href(href: str) -> str | None:
    """DuckDuckGo html results can be direct links or /l/?uddg= wrapped links."""
    if not href:
        return None
    if href.startswith("http://") or href.startswith("https://"):
        parsed = urlparse(href)
        if "duckduckgo.com" in (parsed.netloc or "") and parsed.path.startswith("/l/"):
            qs = parse_qs(parsed.query)
            uddg = qs.get("uddg", [])
            if uddg and uddg[0]:
                return unquote_plus(uddg[0])
        return href
    if href.startswith("//duckduckgo.com/l/"):
        parsed = urlparse("https:" + href)
        qs = parse_qs(parsed.query)
        uddg = qs.get("uddg", [])
        if uddg and uddg[0]:
            return unquote_plus(uddg[0])
    if href.startswith("/l/"):
        parsed = urlparse(href)
        qs = parse_qs(parsed.query)
        uddg = qs.get("uddg", [])
        if uddg and uddg[0]:
            return unquote_plus(uddg[0])
    return None


def build_search_queries(
    business_name: str,
    city: str,
    profession: str,
    pincode: str,
    full_hint: str,
    address_hint: str,
) -> list[str]:
    queries: list[str] = []
    # Keep queries simple when using real browser Google search.
    if full_hint:
        queries.append(f"{full_hint}".strip())
    if business_name and address_hint:
        queries.append(f"{business_name} {address_hint}".strip())
    if business_name:
        queries.append(f"{business_name} {city} {pincode}".strip())
        queries.append(f"{business_name} {city}".strip())
        queries.append(f"{business_name}".strip())

    # Deduplicate while preserving order.
    out: list[str] = []
    seen: set[str] = set()
    for q in queries:
        qn = q.strip()
        if not qn or qn in seen:
            continue
        seen.add(qn)
        out.append(qn)
    return out


def business_match_tokens(name: str) -> list[str]:
    tokens = normalize_business_name(name).split()
    return [t for t in tokens if len(t) >= 4 and t not in GENERIC_MATCH_TOKENS]


def generate_domain_guesses(business_name: str) -> list[str]:
    tokens = [t for t in normalize_business_name(business_name).split() if len(t) >= 3]
    tokens = [t for t in tokens if t not in LOCATION_NOISE_TOKENS]
    if not tokens:
        return []
    guesses: list[str] = []
    # Common official-site patterns.
    joins = [
        "".join(tokens[:2]),
        "".join(tokens[:3]),
        "".join(tokens),
    ]
    for stem in joins:
        if len(stem) < 5:
            continue
        guesses.append(f"https://www.{stem}.com")
        guesses.append(f"https://{stem}.com")

    # Dermatology groups often brand as "...skin.com"
    if "dermatology" in tokens:
        alt_tokens = ["skin" if t == "dermatology" else t for t in tokens]
        alt_joins = [
            "".join(alt_tokens[:2]),
            "".join(alt_tokens[:3]),
            "".join(alt_tokens),
        ]
        for stem in alt_joins:
            if len(stem) < 5:
                continue
            guesses.append(f"https://www.{stem}.com")
            guesses.append(f"https://{stem}.com")
    # Deduplicate
    out: list[str] = []
    seen: set[str] = set()
    for g in guesses:
        if g in seen:
            continue
        seen.add(g)
        out.append(g)
    return out


def resolve_bing_result_href(href: str) -> str | None:
    """Resolve Bing result links, including /ck/a redirect wrappers."""
    if not href:
        return None
    if href.startswith("http://") or href.startswith("https://"):
        parsed = urlparse(href)
        host = (parsed.netloc or "").lower()
        if host.endswith("bing.com") and parsed.path.startswith("/ck/a"):
            qs = parse_qs(parsed.query)
            encoded = (qs.get("u", [""])[0] or "").strip()
            # Common pattern: u=a1<base64url_of_target_url>
            if encoded.startswith("a1") and len(encoded) > 2:
                payload = encoded[2:]
                padding = "=" * ((4 - len(payload) % 4) % 4)
                try:
                    decoded = base64.urlsafe_b64decode(payload + padding).decode(
                        "utf-8", errors="ignore"
                    )
                    if decoded.startswith("http://") or decoded.startswith("https://"):
                        return decoded
                except Exception:
                    return None
            return None
        return href
    return None


def resolve_google_result_href(href: str) -> str | None:
    """Resolve Google SERP anchors to outbound target URLs."""
    if not href:
        return None
    if href.startswith("/url?"):
        parsed = urlparse(href)
        qs = parse_qs(parsed.query)
        target = (qs.get("q", [""])[0] or "").strip()
        if target.startswith("http://") or target.startswith("https://"):
            return target
        return None
    if href.startswith("http://") or href.startswith("https://"):
        host = (urlparse(href).hostname or "").lower()
        if "google." in host:
            return None
        return href
    return None


def _dedupe_candidates(urls: list[str], max_candidates: int) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for url in urls:
        if not url:
            continue
        if domain_is_blocked(url):
            continue
        key = url.split("?")[0].rstrip("/")
        if key in seen:
            continue
        seen.add(key)
        out.append(url)
        if len(out) >= max_candidates:
            break
    return out


def fetch_search_candidates_ddg(query: str, max_candidates: int = 10) -> list[str]:
    response = requests.get(
        DDG_ENDPOINT,
        params={"q": query},
        headers={"User-Agent": SEARCH_USER_AGENT},
        timeout=HTTP_TIMEOUT_SECONDS,
    )
    # DDG can return 202 challenge pages without real result links.
    if response.status_code not in (200, 202):
        response.raise_for_status()
    if response.status_code == 202:
        return []
    soup = BeautifulSoup(response.text, "html.parser")

    raw_candidates: list[str] = []
    for anchor in soup.find_all("a", href=True):
        resolved = resolve_ddg_result_href(anchor["href"])
        if not resolved:
            continue
        raw_candidates.append(resolved)
    return _dedupe_candidates(raw_candidates, max_candidates=max_candidates)


def fetch_search_candidates_bing(query: str, max_candidates: int = 10) -> list[str]:
    response = requests.get(
        BING_ENDPOINT,
        params={
            "q": query,
            "setlang": "en-us",
            "cc": "us",
            "mkt": "en-US",
        },
        headers={"User-Agent": SEARCH_USER_AGENT},
        timeout=HTTP_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")

    raw_candidates: list[str] = []
    for anchor in soup.select("li.b_algo h2 a[href]"):
        resolved = resolve_bing_result_href(anchor.get("href", ""))
        if resolved:
            raw_candidates.append(resolved)
    # Broader fallback if selector misses.
    if not raw_candidates:
        for anchor in soup.find_all("a", href=True):
            resolved = resolve_bing_result_href(anchor["href"])
            if resolved:
                raw_candidates.append(resolved)
    return _dedupe_candidates(raw_candidates, max_candidates=max_candidates)


def fetch_search_candidates_google(query: str, max_candidates: int = 10) -> list[str]:
    response = requests.get(
        GOOGLE_ENDPOINT,
        params={
            "q": query,
            "hl": "en",
            "gl": "us",
            "num": "10",
            "pws": "0",
        },
        headers={"User-Agent": SEARCH_USER_AGENT},
        timeout=HTTP_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    if "/httpservice/retry/enablejs" in response.text:
        return []
    soup = BeautifulSoup(response.text, "html.parser")

    raw_candidates: list[str] = []
    for anchor in soup.find_all("a", href=True):
        resolved = resolve_google_result_href(anchor["href"])
        if resolved:
            raw_candidates.append(resolved)
    return _dedupe_candidates(raw_candidates, max_candidates=max_candidates)


class GoogleBrowserSearcher:
    """Persistent Chromium page for Google queries."""

    def __init__(self, headless: bool = False):
        self.headless = headless
        self.playwright = None
        self.context = None
        self.page = None
        self.user_data_dir = None

    def start(self) -> None:
        if self.page is not None:
            return
        self.playwright = sync_playwright().start()
        self.user_data_dir = tempfile.mkdtemp(prefix="google-search-profile-")
        self.context = self.playwright.chromium.launch_persistent_context(
            user_data_dir=self.user_data_dir,
            headless=self.headless,
            args=["--disable-blink-features=AutomationControlled"],
            viewport={"width": 1366, "height": 900},
            locale="en-US",
        )
        pages = self.context.pages
        self.page = pages[0] if pages else self.context.new_page()
        try:
            self.page.goto("https://www.google.com/?hl=en&gl=us", timeout=30000)
            self.page.wait_for_load_state("domcontentloaded", timeout=15000)
        except Exception:
            pass

    def stop(self) -> None:
        if self.context:
            try:
                self.context.close()
            except Exception:
                pass
        if self.playwright:
            try:
                self.playwright.stop()
            except Exception:
                pass
        self.playwright = None
        self.context = None
        self.page = None
        if self.user_data_dir:
            try:
                Path(self.user_data_dir).rmdir()
            except Exception:
                pass
            self.user_data_dir = None

    def search(self, query: str, max_candidates: int = 10) -> list[str]:
        self.start()
        assert self.page is not None
        try:
            self.page.goto(
                f"https://www.google.com/search?q={requests.utils.quote(query)}&hl=en&gl=us",
                timeout=45000,
            )
            self.page.wait_for_load_state("domcontentloaded", timeout=15000)
            self.page.wait_for_timeout(1200)
            hrefs = self.page.eval_on_selector_all(
                "a[href]",
                "els => els.map(e => e.getAttribute('href') || '').filter(Boolean)",
            )
        except Exception:
            return []
        raw_candidates: list[str] = []
        for href in hrefs or []:
            resolved = resolve_google_result_href(str(href))
            if resolved:
                raw_candidates.append(resolved)
        return _dedupe_candidates(raw_candidates, max_candidates=max_candidates)


def _get_google_browser_searcher(headless: bool = False) -> GoogleBrowserSearcher:
    global _GOOGLE_BROWSER_SEARCHER
    if _GOOGLE_BROWSER_SEARCHER is None:
        _GOOGLE_BROWSER_SEARCHER = GoogleBrowserSearcher(headless=headless)
        _GOOGLE_BROWSER_SEARCHER.start()
    return _GOOGLE_BROWSER_SEARCHER


def _shutdown_google_browser_searcher() -> None:
    global _GOOGLE_BROWSER_SEARCHER
    if _GOOGLE_BROWSER_SEARCHER is not None:
        _GOOGLE_BROWSER_SEARCHER.stop()
        _GOOGLE_BROWSER_SEARCHER = None


atexit.register(_shutdown_google_browser_searcher)


def fetch_search_candidates(
    query: str,
    max_candidates: int = 10,
    use_google_browser: bool = False,
    google_browser_headless: bool = False,
) -> tuple[list[str], str]:
    """Google-first candidate lookup with fallback engines."""
    if use_google_browser:
        browser_candidates = _get_google_browser_searcher(
            headless=google_browser_headless
        ).search(query, max_candidates=max_candidates)
        if browser_candidates:
            return browser_candidates, "google-browser"
    google_candidates = fetch_search_candidates_google(query, max_candidates=max_candidates)
    if google_candidates:
        return google_candidates, "google"
    bing_candidates = fetch_search_candidates_bing(query, max_candidates=max_candidates)
    if bing_candidates:
        return bing_candidates, "bing"
    ddg_candidates = fetch_search_candidates_ddg(query, max_candidates=max_candidates)
    if ddg_candidates:
        return ddg_candidates, "duckduckgo"
    return [], "none"


def _token_overlap_ratio(name_tokens: Iterable[str], page_tokens: set[str]) -> float:
    name_tokens_list = [t for t in name_tokens if t]
    if not name_tokens_list:
        return 0.0
    overlap = sum(1 for t in name_tokens_list if t in page_tokens)
    return overlap / max(1, len(name_tokens_list))


def score_candidate_page(
    url: str,
    business_name: str,
    city: str,
    profession: str,
) -> tuple[int, str]:
    """Score candidate with multi-signal checks (0-10)."""
    if domain_is_blocked(url):
        return 0, "blocked directory domain"

    try:
        response = requests.get(
            url,
            headers={"User-Agent": USER_AGENT},
            timeout=HTTP_TIMEOUT_SECONDS,
            allow_redirects=True,
        )
    except requests.RequestException as exc:
        return 0, f"fetch error: {str(exc)[:60]}"

    if response.status_code >= 400:
        return 0, f"http status {response.status_code}"

    soup = BeautifulSoup(response.text, "html.parser")
    title = normalize_text(soup.title.get_text(" ", strip=True) if soup.title else "")
    h1 = normalize_text(soup.h1.get_text(" ", strip=True) if soup.h1 else "")
    body = normalize_text(soup.get_text(" ", strip=True))
    url_l = url.lower()
    domain_l = extract_domain(url).lower()

    name_n = normalize_business_name(business_name)
    city_n = normalize_text(city)
    profession_n = normalize_text(profession)

    page_tokens = set(body.split())
    name_tokens = [t for t in name_n.split() if len(t) > 2]
    overlap_ratio = _token_overlap_ratio(name_tokens, page_tokens)

    score = 0
    reasons: list[str] = []

    # Name signals
    if name_n and name_n in title:
        score += 3
        reasons.append("name in title")
    if name_n and name_n in h1:
        score += 3
        reasons.append("name in h1")
    if overlap_ratio >= 0.75:
        score += 2
        reasons.append("strong token overlap")
    elif overlap_ratio >= 0.5:
        score += 1
        reasons.append("partial token overlap")

    # Geo + specialty signals
    if city_n and city_n in body:
        score += 1
        reasons.append("city match")
    if profession_n and profession_n in body:
        score += 1
        reasons.append("profession match")
    elif "dermatolog" in body:
        score += 1
        reasons.append("specialty stem match")

    # URL path quality signal
    if any(token in url_l for token in ("/contact", "/about", "/location", "/locations")):
        score += 1
        reasons.append("site path signal")

    # Domain token signal (helps official-site matches like kaminskadermatology.com)
    core_tokens = [
        t
        for t in normalize_business_name(business_name).split()
        if len(t) >= 4 and t not in LOCATION_NOISE_TOKENS
    ]
    token_hits = sum(1 for t in core_tokens[:3] if t in domain_l)
    if token_hits >= 2:
        score += 4
        reasons.append("domain token match")
    elif token_hits == 1:
        score += 2
        reasons.append("partial domain token match")

    # Penalties
    if "directory" in title or "find a doctor" in title:
        score -= 3
        reasons.append("directory-like title penalty")
    if "google.com/maps" in url_l:
        score -= 5
        reasons.append("maps url penalty")

    score = max(0, min(10, score))
    if not reasons:
        reasons.append("low signals")
    return score, ", ".join(reasons)


def find_best_verified_match(
    business_name: str,
    city: str,
    profession: str,
    pincode: str,
    full_hint: str,
    address_hint: str,
    min_verified_score: int,
    min_review_score: int,
    verbose: bool = False,
    use_google_browser: bool = False,
    google_browser_headless: bool = False,
) -> MatchResult:
    if not business_name or len(normalize_text(business_name)) < 4:
        return MatchResult(None, None, None, 0, "name too short")

    # Deterministic free fallback: guess likely official domains from business name.
    domain_guesses = generate_domain_guesses(business_name)
    for guessed_url in domain_guesses:
        score, reason = score_candidate_page(guessed_url, business_name, city, profession)
        if verbose:
            print(f"  Domain guess check: {guessed_url} -> score {score}")
        if score >= min_verified_score:
            return MatchResult(
                business_name,
                guessed_url,
                extract_domain(guessed_url),
                score,
                f"domain guess verified: {reason}",
            )

    queries = build_search_queries(
        business_name=business_name,
        city=city,
        profession=profession,
        pincode=pincode,
        full_hint=full_hint,
        address_hint=address_hint,
    )
    if not queries:
        return MatchResult(None, None, None, 0, "no usable queries")

    candidates: list[str] = []
    sources: list[str] = []
    for query in queries:
        try:
            found, source_engine = fetch_search_candidates(
                query,
                max_candidates=10,
                use_google_browser=use_google_browser,
                google_browser_headless=google_browser_headless,
            )
        except requests.RequestException:
            continue
        if found:
            sources.append(source_engine)
            candidates.extend(found)
        if len(candidates) >= 12:
            break
    candidates = _dedupe_candidates(candidates, max_candidates=12)
    # Prefer URLs containing distinctive business tokens to reduce noisy search pages.
    match_tokens = business_match_tokens(business_name)
    filtered = [
        u for u in candidates if any(t in (u or "").lower() for t in match_tokens)
    ] if match_tokens else []
    if filtered:
        candidates = filtered

    if verbose:
        print(
            f"  Search queries tried: {len(queries)}\n"
            f"  Primary query: {queries[0][:120]}{'...' if len(queries[0]) > 120 else ''}\n"
            f"  Sources with hits: {', '.join(sources) if sources else 'none'}\n"
            f"  Candidates found: {len(candidates)}"
        )

    if not candidates:
        return MatchResult(None, None, None, 0, "no search candidates")

    scored: list[tuple[int, str, str]] = []
    for candidate in candidates:
        score, reason = score_candidate_page(candidate, business_name, city, profession)
        scored.append((score, candidate, reason))

    scored.sort(key=lambda x: x[0], reverse=True)
    best_score, best_url, best_reason = scored[0]
    best_domain = extract_domain(best_url)

    if verbose:
        print(
            f"  Best candidate: {best_url}\n"
            f"  Best score: {best_score}/10 | Reason: {best_reason}"
        )

    if best_score >= min_verified_score:
        return MatchResult(business_name, best_url, best_domain, best_score, best_reason)

    if best_score >= min_review_score:
        return MatchResult(None, None, None, best_score, f"review: {best_reason}")

    return MatchResult(None, None, None, best_score, f"unresolved: {best_reason}")


def process_csv(
    input_file: str,
    output_file: str,
    min_verified_score: int,
    min_review_score: int,
    delay_seconds: float,
    verbose: bool,
    use_google_browser: bool,
    google_browser_headless: bool,
) -> None:
    df = pd.read_csv(input_file)

    # Backup columns for auditability.
    for col in ("original_website_name", "original_url", "original_domain"):
        if col not in df.columns:
            df[col] = ""

    if "business_name" not in df.columns:
        df["business_name"] = ""
    if "resolution_status" not in df.columns:
        df["resolution_status"] = "skipped"
    if "verification_score" not in df.columns:
        df["verification_score"] = 0
    if "match_reason" not in df.columns:
        df["match_reason"] = ""

    mask = df.apply(is_directions_maps_row, axis=1)
    total_targets = int(mask.sum())

    processed = 0
    verified = 0
    review = 0
    unresolved = 0

    print(
        "Starting conversion...\n"
        f"Input: {input_file}\n"
        f"Output: {output_file}\n"
        f"Directions rows to process: {total_targets}\n"
        f"Thresholds: verified>={min_verified_score}, review>={min_review_score}\n"
        f"Delay between rows: {delay_seconds:.2f}s\n"
        f"Google browser search: {'on' if use_google_browser else 'off'}"
    )

    if total_targets == 0:
        print("No Directions+Maps rows found. Nothing to convert.")

    for idx, row in df[mask].iterrows():
        processed += 1

        maps_hint = parse_maps_hint_from_url(str(row.get("url", "")))
        business_name = maps_hint["name"]
        df.at[idx, "business_name"] = business_name

        if verbose:
            print(
                "\n"
                + "-" * 72
                + f"\n[{processed}/{total_targets}] Row index: {idx} | "
                f"Business: {business_name or '(unparsed)'} | "
                f"Location: {row.get('location', '')} | Profession: {row.get('profession', '')}"
            )

        result = find_best_verified_match(
            business_name=business_name,
            city=str(row.get("location", "")),
            profession=str(row.get("profession", "")),
            pincode=str(row.get("pincode", "")),
            full_hint=maps_hint["full_hint"],
            address_hint=maps_hint["address_hint"],
            min_verified_score=min_verified_score,
            min_review_score=min_review_score,
            verbose=verbose,
            use_google_browser=use_google_browser,
            google_browser_headless=google_browser_headless,
        )

        df.at[idx, "verification_score"] = int(result.score)
        df.at[idx, "match_reason"] = result.reason

        if result.url and result.score >= min_verified_score:
            # Preserve originals before overwrite.
            df.at[idx, "original_website_name"] = str(row.get("website_name", ""))
            df.at[idx, "original_url"] = str(row.get("url", ""))
            df.at[idx, "original_domain"] = str(row.get("domain", ""))

            df.at[idx, "website_name"] = result.website_name or business_name or "Unknown"
            df.at[idx, "url"] = result.url
            df.at[idx, "domain"] = result.domain or ""
            df.at[idx, "resolution_status"] = "verified"
            verified += 1
            if verbose:
                print(
                    f"  Result: VERIFIED | score={result.score} | "
                    f"domain={result.domain or ''}"
                )
        elif result.score >= min_review_score:
            df.at[idx, "resolution_status"] = "review"
            review += 1
            if verbose:
                print(f"  Result: REVIEW | score={result.score} | {result.reason}")
        else:
            df.at[idx, "resolution_status"] = "unresolved"
            unresolved += 1
            if verbose:
                print(f"  Result: UNRESOLVED | score={result.score} | {result.reason}")

        if processed % 10 == 0 or processed == total_targets:
            print(
                f"Progress: {processed}/{total_targets} processed | "
                f"verified={verified}, review={review}, unresolved={unresolved}"
            )

        time.sleep(max(0.0, delay_seconds))

    # Keep non-target rows marked as skipped when not already set.
    df.loc[~mask & (df["resolution_status"] == ""), "resolution_status"] = "skipped"

    df.to_csv(output_file, index=False)
    print(f"Processed Directions rows: {processed}")
    print(f"Verified updates: {verified}")
    print(f"Needs review: {review}")
    print(f"Unresolved: {unresolved}")
    print(f"Saved: {output_file}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Free-only converter for Directions + Google Maps rows."
    )
    parser.add_argument(
        "--input",
        default="May5_results_ads.csv",
        help="Input CSV path (default: May5_results_ads.csv)",
    )
    parser.add_argument(
        "--output",
        default="May5_results_ads_verified_free_v2.csv",
        help="Output CSV path (default: May5_results_ads_verified_free_v2.csv)",
    )
    parser.add_argument(
        "--min-verified-score",
        type=int,
        default=8,
        help="Minimum score to overwrite row (default: 8)",
    )
    parser.add_argument(
        "--min-review-score",
        type=int,
        default=6,
        help="Minimum score to mark row as review (default: 6)",
    )
    parser.add_argument(
        "--delay-seconds",
        type=float,
        default=2.0,
        help="Delay between rows to reduce blocking (default: 2.0)",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Reduce row-level logs (prints only startup, periodic progress, and summary).",
    )
    parser.add_argument(
        "--google-browser",
        action="store_true",
        help="Use persistent Chromium to fetch Google search candidates.",
    )
    parser.add_argument(
        "--google-browser-headless",
        action="store_true",
        help="Run Google browser mode headless (default is headed).",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    process_csv(
        input_file=args.input,
        output_file=args.output,
        min_verified_score=max(0, min(10, args.min_verified_score)),
        min_review_score=max(0, min(10, args.min_review_score)),
        delay_seconds=args.delay_seconds,
        verbose=not args.quiet,
        use_google_browser=args.google_browser,
        google_browser_headless=args.google_browser_headless,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
