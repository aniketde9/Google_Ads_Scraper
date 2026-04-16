"""Extraction and normalization helpers."""

from __future__ import annotations

import datetime
import urllib.parse

import tldextract


def _is_google_ad_redirect(href_lower: str) -> bool:
    """True for outbound click wrappers used on ads (not generic Google properties)."""
    return "google.com/url" in href_lower or "/url?" in href_lower


def is_valid_sponsored_link(href: str | None, display_text: str) -> bool:
    """Validate sponsored link candidate.

    Most Google ads use https://www.google.com/url?q=... — those must not be rejected.
    """
    if not href:
        return False
    h = href.strip()
    if h.startswith("//"):
        h = "https:" + h
    lower = h.lower()
    if "youtube.com" in lower:
        return False
    if h.startswith("/url?"):
        if len(h) < 10 or len(display_text.strip()) < 2:
            return False
        return True
    if _is_google_ad_redirect(lower):
        if len(h) < 10 or len(display_text.strip()) < 2:
            return False
        return True
    if "google.com" in lower:
        return False
    if not (lower.startswith("http://") or lower.startswith("https://")):
        return False
    if len(h) < 10 or len(display_text.strip()) < 2:
        return False
    return True


def unpack_google_redirect_url(url: str) -> str:
    """Unpack Google redirect URL if present."""
    if url.startswith("/url?"):
        url = "https://www.google.com" + url
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
