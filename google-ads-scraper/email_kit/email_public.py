"""Public email extraction helpers (vendored from Author_Finder)."""

from __future__ import annotations

import re
from urllib.parse import urlparse

import dns.exception
import dns.resolver

EMAIL_REGEX = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")

BRACKET_OBFUSCATE: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\[at\]", re.IGNORECASE), "@"),
    (re.compile(r"\(at\)", re.IGNORECASE), "@"),
    (re.compile(r"\[dot\]", re.IGNORECASE), "."),
    (re.compile(r"\(dot\)", re.IGNORECASE), "."),
]

EXTRA_OBFUSCATE_FOR_CANDIDATE: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\s+at\s+", re.IGNORECASE), "@"),
    (re.compile(r"\s+dot\s+", re.IGNORECASE), "."),
]


def _deobfuscate_text(text: str) -> str:
    s = text
    for pat, repl in BRACKET_OBFUSCATE:
        s = pat.sub(repl, s)
    s = re.sub(r"\s*@\s*", "@", s)
    s = re.sub(r"\s*\.\s*", ".", s)
    return s


BLOCKLIST_EMAIL_DOMAINS = frozenset(
    {
        "amazon.com",
        "amazon.co.uk",
        "amazonexteu.qualtrics.com",
        "qualtrics.com",
        "audible.com",
        "audible.co.uk",
        "example.com",
        "sentry.io",
        "wixpress.com",
        "schema.org",
    }
)

BLOCKLIST_EMAIL_LOCALPART_PREFIXES = ("noreply", "no-reply", "donotreply", "mailer-daemon")

GENERIC_LOCALPARTS = frozenset(
    {
        "info",
        "hello",
        "contact",
        "team",
        "support",
        "admin",
        "sales",
        "press",
        "mail",
        "office",
        "media",
        "help",
    }
)


def is_generic_localpart(email: str) -> bool:
    if not email or "@" not in email:
        return False
    local = email.split("@", 1)[0].strip().lower()
    if local in GENERIC_LOCALPARTS:
        return True
    return any(local.startswith(p + ".") or local.startswith(p + "+") for p in GENERIC_LOCALPARTS)


def sanitize_author_name_for_guessing(raw: str) -> str:
    s = (raw or "").strip()
    if not s:
        return ""
    s = re.sub(r"\s*\|.*$", "", s).strip()
    s = re.sub(r",?\s*et al\.?\s*$", "", s, flags=re.IGNORECASE).strip()
    parts = [p.strip() for p in s.split(",") if p.strip()]
    if len(parts) >= 2:
        a, b = parts[0], parts[1]
        a_toks = a.split()
        b_toks = b.split()
        if len(a_toks) == 1 and len(b_toks) == 1 and a.isalpha() and b.isalpha():
            s = f"{b} {a}"
        elif len(b_toks) >= 2 or re.search(r"[A-Z]\.", b):
            s = a
        elif len(parts) > 2:
            s = parts[0]
        else:
            s = f"{a} {b}"
    elif len(parts) == 1:
        s = parts[0]
    return re.sub(r"\s+", " ", s).strip()


def generate_personal_guesses(author_name: str, domain: str) -> list[str]:
    domain = (domain or "").strip().lower().removeprefix("www.")
    if not domain:
        return []
    clean_name = sanitize_author_name_for_guessing(author_name)
    if not clean_name:
        return []
    tokens = [t for t in clean_name.split() if t]
    if not tokens:
        return []
    first = re.sub(r"[^a-zA-Z\-]", "", tokens[0]).lower()
    last = re.sub(r"[^a-zA-Z\-]", "", tokens[-1]).lower() if len(tokens) > 1 else first
    middle = re.sub(r"[^a-zA-Z\-]", "", tokens[1]).lower() if len(tokens) > 2 else ""
    if not first:
        return []
    initial = first[0]
    li = last[0] if last else ""

    candidates: list[str | None] = [
        f"{first}@{domain}",
        f"{last}@{domain}",
        f"{first}.{last}@{domain}",
        f"{first}{last}@{domain}",
        f"{initial}{last}@{domain}",
        f"{initial}.{last}@{domain}",
        f"{first}{li}@{domain}" if last else None,
        f"{first}.{li}@{domain}" if last else None,
        f"{first}{middle}@{domain}" if middle else None,
        f"{first}.{middle}@{domain}" if middle else None,
        f"{initial}{middle}{last}@{domain}" if middle else None,
        f"{initial}.{middle}.{last}@{domain}" if middle else None,
        f"{first}_{last}@{domain}",
        f"{last}.{first}@{domain}",
        f"{first}-{last}@{domain}",
    ]
    out: list[str] = []
    seen: set[str] = set()
    for c in candidates:
        if not c:
            continue
        local = c.split("@", 1)[0]
        if not local or len(local) > 64:
            continue
        if c not in seen:
            seen.add(c)
            out.append(c)
    return out


def normalize_email_candidate(raw: str) -> str | None:
    s = raw.strip().lower()
    for pat, repl in BRACKET_OBFUSCATE:
        s = pat.sub(repl, s)
    for pat, repl in EXTRA_OBFUSCATE_FOR_CANDIDATE:
        s = pat.sub(repl, s)
    s = re.sub(r"\s*@\s*", "@", s)
    s = re.sub(r"\s*\.\s*", ".", s)
    s = s.strip().strip(".,;:\"'")
    if not EMAIL_REGEX.fullmatch(s):
        return None
    return s


def extract_emails_from_text(text: str) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    blob = _deobfuscate_text(text or "")
    for m in EMAIL_REGEX.finditer(blob):
        norm = normalize_email_candidate(m.group(0))
        if norm and norm not in seen and _email_is_plausible(norm):
            seen.add(norm)
            out.append(norm)
    return out


def _email_is_plausible(email: str) -> bool:
    local, _, domain = email.partition("@")
    if not local or not domain:
        return False
    d = domain.lower()
    if d in BLOCKLIST_EMAIL_DOMAINS:
        return False
    for p in BLOCKLIST_EMAIL_LOCALPART_PREFIXES:
        if local.lower().startswith(p):
            return False
    return True


def domain_has_mx(domain: str, *, lifetime: float = 5.0) -> bool:
    domain = domain.strip().lower().rstrip(".")
    if not domain:
        return False
    try:
        answers = dns.resolver.resolve(domain, "MX", lifetime=lifetime)
        return len(answers) > 0
    except (dns.exception.DNSException, OSError, ValueError):
        return False


def hostname_from_url(url: str) -> str | None:
    try:
        p = urlparse(url)
        host = (p.hostname or "").lower()
        return host or None
    except ValueError:
        return None


def email_domain_matches_site(email: str, site_url: str) -> bool:
    parts = email.lower().split("@", 1)
    if len(parts) != 2:
        return False
    eh = parts[1]
    sh = hostname_from_url(site_url)
    if not sh:
        return False
    if eh == sh:
        return True
    return sh.endswith("." + eh)
