"""Extraction and normalization helpers."""

from __future__ import annotations

import datetime
import hashlib
import re
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


def _url_is_maps_or_place(url: str) -> bool:
    """True for Google Maps / local URLs (after optional unpack)."""
    if not url:
        return False
    try:
        parsed = urllib.parse.urlparse(url.strip())
    except ValueError:
        return False
    host = (parsed.hostname or "").lower()
    path = (parsed.path or "").lower()
    if host.startswith("maps.google.") or host == "maps.google.com":
        return True
    if "goo.gl" in host or "g.page" in host:
        return True
    if "google." in host and ("/maps" in path or path.startswith("/maps")):
        return True
    return False


def is_valid_places_sponsored_link(href: str | None, display_text: str) -> bool:
    """Like classic validation but allows Google Maps / place URLs (not generic google search)."""
    if not href:
        return False
    h = href.strip()
    if h.startswith("//"):
        h = "https:" + h
    lower = h.lower()
    if "youtube.com" in lower:
        return False
    dt = (display_text or "").strip()
    if len(dt) < 2:
        return False
    if h.startswith("/url?"):
        return len(h) >= 10
    if _is_google_ad_redirect(lower):
        return len(h) >= 10
    if not (lower.startswith("http://") or lower.startswith("https://")):
        return False
    if len(h) < 10:
        return False
    if "google." in lower and "/search" in lower:
        return False
    if _url_is_maps_or_place(h):
        return True
    if "google.com" in lower or "google." in (urllib.parse.urlparse(h).hostname or "").lower():
        return False
    return True


def _slug_from_maps_path(url: str) -> str:
    parsed = urllib.parse.urlparse(url)
    path = parsed.path or ""
    m = re.search(r"/place/([^/]+)", path, re.I)
    if m:
        raw = urllib.parse.unquote_plus(m.group(1))
        slug = re.sub(r"[^a-z0-9]+", "-", raw.lower()).strip("-")[:72]
        if slug:
            return slug
    qs = urllib.parse.parse_qs(parsed.query)
    for key in ("cid", "ftid", "place_id"):
        vals = qs.get(key)
        if vals and vals[0]:
            s = re.sub(r"[^a-z0-9]+", "-", vals[0].lower()).strip("-")[:40]
            if s:
                return f"id-{s}"
    return ""


def domain_for_ad_row(url: str) -> str:
    """Registrable domain for external URLs; stable synthetic ``place-…`` slug for Maps-only rows."""
    u = unpack_google_redirect_url((url or "").strip())
    if not u:
        return ""
    if _url_is_maps_or_place(u):
        slug = _slug_from_maps_path(u)
        if slug:
            return f"place-{slug}"
        h = hashlib.sha256(u.encode("utf-8", errors="ignore")).hexdigest()[:12]
        return f"place-{h}"
    return extract_domain(u)


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
