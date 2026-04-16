"""Extraction and normalization helpers."""

from __future__ import annotations

import datetime
import urllib.parse

import tldextract


def is_valid_sponsored_link(href: str | None, display_text: str) -> bool:
    """Validate sponsored link candidate."""
    if not href:
        return False
    lower_href = href.lower()
    if "google.com" in lower_href or "youtube.com" in lower_href:
        return False
    if not (lower_href.startswith("http://") or lower_href.startswith("https://")):
        return False
    if len(href) < 10 or len(display_text.strip()) < 2:
        return False
    return True


def unpack_google_redirect_url(url: str) -> str:
    """Unpack Google redirect URL if present."""
    if "google.com/url" in url:
        parsed = urllib.parse.urlparse(url)
        params = urllib.parse.parse_qs(parsed.query)
        return params.get("q", [url])[0]
    return url


def extract_domain(url: str) -> str:
    """Extract registrable domain from URL."""
    parsed = urllib.parse.urlparse(url)
    hostname = parsed.hostname or ""
    extracted = tldextract.extract(hostname)
    if not extracted.domain:
        return ""
    return f"{extracted.domain}.{extracted.suffix}" if extracted.suffix else extracted.domain


def normalize_domain(domain: str) -> str:
    """Normalize domain for dedupe checks."""
    normalized = domain.lower().strip()
    normalized = normalized.replace("www.", "")
    normalized = normalized.replace("https://", "").replace("http://", "")
    return normalized.rstrip("/")


def extract_website_name(display_text: str) -> str:
    """Use first line of visible ad text as website name."""
    if not display_text:
        return "Unknown"
    return display_text.strip().split("\n")[0] or "Unknown"


def current_timestamp() -> str:
    """UTC timestamp in ISO format."""
    return datetime.datetime.utcnow().isoformat()
