"""Post-process results.csv: crawl advertiser sites, extract/guess emails, verify (Truth Reactor).

Run from google-ads-scraper directory:
  python enrich_results_emails.py
  python enrich_results_emails.py --limit 5 --dry-run
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import logging
import random
import re
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup
from playwright.async_api import async_playwright

import config as app_config
from email_kit.email_public import (
    email_domain_matches_site,
    extract_emails_from_text,
    generate_personal_guesses,
    is_generic_localpart,
    sanitize_author_name_for_guessing,
)
from email_kit.map_status import map_verification_to_notes
from email_kit.truth_reactor import EmailTruthReactor
from email_kit.types import VerificationResult
from extractors import extract_domain, normalize_domain

log = logging.getLogger("email_enrich")

RESULT_COLUMNS = [
    "profession",
    "location",
    "pincode",
    "website_name",
    "url",
    "domain",
    "appearance_count",
    "run_number",
    "timestamp",
]

ENRICH_COLUMNS = [
    "enriched_email",
    "enriched_email_source",
    "enriched_email_found_on_url",
    "email_category",
    "email_confidence",
    "email_smtp_ok",
    "email_verification_notes",
]


@dataclass
class DomainJob:
    norm_domain: str
    seed_urls: list[str] = field(default_factory=list)
    sample_website_name: str = ""
    sample_landing_url: str = ""


def _setup_file_logger() -> None:
    path = getattr(app_config, "EMAIL_ENRICH_LOG", "email_enrich.log")
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    fh = logging.FileHandler(path, encoding="utf-8")
    fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    root.addHandler(fh)
    sh = logging.StreamHandler()
    sh.setFormatter(logging.Formatter("%(message)s"))
    root.addHandler(sh)


def _registrable_domain_from_url(url: str) -> str:
    return extract_domain(url) or ""


def _same_registrable_domain(url: str, target_norm: str) -> bool:
    d = _registrable_domain_from_url(url)
    return normalize_domain(d) == normalize_domain(target_norm)


def _path_priority(path: str) -> int:
    p = path.lower()
    score = 0
    for kw, w in (
        ("contact", 5),
        ("about", 4),
        ("team", 4),
        ("attorney", 3),
        ("lawyer", 3),
        ("people", 3),
    ):
        if kw in p:
            score += w
    return score


def _gather_links(html: str, base_url: str, max_links: int) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    urls: list[str] = []
    seen: set[str] = set()
    for a in soup.find_all("a", href=True):
        href = (a.get("href") or "").strip()
        if not href or href.startswith(("#", "javascript:", "data:")):
            continue
        if href.lower().startswith("mailto:"):
            continue
        try:
            full = urljoin(base_url, href)
        except ValueError:
            # Malformed href can trigger "Invalid IPv6 URL" in urllib.parse
            continue
        if not full.strip():
            continue
        if full not in seen:
            seen.add(full)
            urls.append(full)
        if len(urls) >= max_links * 3:
            break
    return urls


def _emails_from_html(html: str, page_url: str) -> tuple[dict[str, list[str]], list[str]]:
    """Map email -> list of source hints; also return mailto emails."""
    by_email: dict[str, list[str]] = {}
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text(" ", strip=False)
    for e in extract_emails_from_text(text):
        by_email.setdefault(e, []).append(page_url)
    for a in soup.find_all("a", href=True):
        h = (a.get("href") or "").strip()
        if h.lower().startswith("mailto:"):
            addr = h.split(":", 1)[1].split("?", 1)[0].strip()
            if addr:
                for e in extract_emails_from_text(addr + " "):
                    by_email.setdefault(e, []).append(f"mailto:{page_url}")
    return by_email, list(by_email.keys())


def _pick_best_email(
    by_email: dict[str, list[str]],
    official_site_hint: str | None,
) -> tuple[str | None, str | None]:
    if not by_email:
        return None, None
    scored: list[tuple[tuple[int, int, int, str], str, list[str]]] = []
    for e, srcs in by_email.items():
        uniq = list(dict.fromkeys(srcs))
        site_bonus = 0
        if official_site_hint:
            for s in uniq:
                if email_domain_matches_site(e, s) or (
                    s.startswith("mailto:") and email_domain_matches_site(e, official_site_hint)
                ):
                    site_bonus = 100
                    break
            if site_bonus == 0 and email_domain_matches_site(e, official_site_hint):
                site_bonus = 100
        non_generic = 1 if not is_generic_localpart(e) else 0
        scored.append(((site_bonus, non_generic, len(uniq), e), e, uniq))
    scored.sort(key=lambda x: x[0], reverse=True)
    _, best, uniq = scored[0]
    found_url = uniq[0] if uniq else None
    if found_url and found_url.startswith("mailto:"):
        found_url = found_url.split(":", 2)[-1] if ":" in found_url else found_url
    return best, found_url


def _pick_best_non_generic(
    by_email: dict[str, list[str]],
    official_hint: str | None,
) -> tuple[str | None, str | None]:
    filtered = {e: v for e, v in by_email.items() if not is_generic_localpart(e)}
    if filtered:
        return _pick_best_email(filtered, official_hint)
    return _pick_best_email(by_email, official_hint)


def _headline_looks_name_like(headline: str) -> bool:
    s = (headline or "").strip()
    if len(s) < 3:
        return False
    low = s.lower()
    cta = (
        "call ",
        "call now",
        "schedule",
        "free consult",
        "click",
        "same day",
        "trusted by",
        "must have",
        "|",  # often separates promo tail
    )
    if any(x in low for x in cta):
        return False
    alnum = sum(1 for c in s if c.isalpha())
    if alnum < 4:
        return False
    digits = sum(1 for c in s if c.isdigit())
    if digits > len(s) * 0.25:
        return False
    tokens = [t for t in re.split(r"\s+", s) if t]
    alpha_tokens = [t for t in tokens if re.search(r"[a-zA-Z]", t)]
    if len(alpha_tokens) >= 2:
        return True
    if len(alpha_tokens) == 1 and len(alpha_tokens[0]) >= 5:
        return True
    return False


def _build_seed_urls(landing_url: str, norm_domain: str) -> list[str]:
    seeds: list[str] = []
    if landing_url:
        seeds.append(landing_url.strip())
    base_dom = norm_domain.strip().lower().removeprefix("www.")
    if base_dom:
        seeds.append(f"https://{base_dom}/")
        seeds.append(f"https://www.{base_dom}/")
    seen: set[str] = set()
    out: list[str] = []
    for u in seeds:
        if u and u not in seen:
            seen.add(u)
            out.append(u)
    return out


async def _fetch_page(page, url: str, timeout_ms: int) -> str:
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
        await asyncio.sleep(random.uniform(0.15, 0.4))
        return await page.content()
    except Exception as e:
        log.warning("fetch_failed url=%s err=%s", url, e)
        return ""


async def crawl_domain(
    browser,
    job: DomainJob,
    *,
    max_pages: int,
    nav_timeout_ms: int,
    delay_min: float,
    delay_max: float,
) -> tuple[dict[str, list[str]], list[tuple[str, str]]]:
    """Return (by_email sources, list of (url, html) for debugging)."""
    by_all: dict[str, list[str]] = {}
    pages_fetched: list[tuple[str, str]] = []
    ctx = await browser.new_context()
    page = await ctx.new_page()
    try:
        to_visit: list[str] = []
        seen_pages: set[str] = set()
        for s in job.seed_urls:
            if s and _same_registrable_domain(s, job.norm_domain):
                to_visit.append(s)

        while to_visit and len(pages_fetched) < max_pages:
            url = to_visit.pop(0)
            if url in seen_pages:
                continue
            seen_pages.add(url)
            await asyncio.sleep(random.uniform(delay_min, delay_max))
            html = await _fetch_page(page, url, nav_timeout_ms)
            if not html:
                continue
            pages_fetched.append((url, html))
            chunk, _ = _emails_from_html(html, url)
            for em, srcs in chunk.items():
                by_all.setdefault(em, []).extend(srcs)

            if len(pages_fetched) >= max_pages:
                break
            links = _gather_links(html, url, max_pages * 4)
            ranked = [( _path_priority(urlparse(u).path or ""), u) for u in links if _same_registrable_domain(u, job.norm_domain)]
            ranked.sort(key=lambda x: -x[0])
            for _, u in ranked:
                if u not in seen_pages and u not in to_visit:
                    to_visit.append(u)
    finally:
        await ctx.close()
    return by_all, pages_fetched


def _role_candidates(norm_domain: str, parts: list[str]) -> list[str]:
    dom = norm_domain.strip().lower().removeprefix("www.")
    if not dom:
        return []
    seen: set[str] = set()
    out: list[str] = []
    for p in parts:
        p = (p or "").strip().lower()
        if not p:
            continue
        addr = f"{p}@{dom}"
        if addr not in seen:
            seen.add(addr)
            out.append(addr)
    return out


def _headline_candidates(headline: str, norm_domain: str, cap: int) -> list[str]:
    if not _headline_looks_name_like(headline):
        return []
    cleaned = sanitize_author_name_for_guessing(headline)
    dom = norm_domain.strip().lower().removeprefix("www.")
    if not cleaned or not dom:
        return []
    guesses = generate_personal_guesses(cleaned, dom)
    return guesses[:cap]


def _category_rank(cat: str | None) -> int:
    return {"deliverable": 4, "risky": 3, "unknown": 2, "invalid": 1}.get(str(cat or ""), 0)


def _better(a: VerificationResult, b: VerificationResult) -> bool:
    if not isinstance(a, VerificationResult) or not isinstance(b, VerificationResult):
        return False
    ca, cb = _category_rank(a.category), _category_rank(b.category)
    if ca != cb:
        return ca > cb
    return float(a.confidence or 0) > float(b.confidence or 0)


async def _verify_best(
    reactor: EmailTruthReactor | None,
    candidates: list[str],
    do_smtp: bool,
    max_probes: int,
    early_stop: bool,
    early_conf: float,
) -> tuple[object | None, str | None, int, str]:
    """Verify each candidate in order (up to max_probes); return best VerificationResult."""
    if reactor is None:
        return None, None, 0, "reactor_unavailable"
    if not candidates:
        return None, None, 0, "no_candidates"
    best_r = None
    best_email: str | None = None
    used = 0
    for c in candidates:
        if used >= max_probes:
            break
        r = await asyncio.to_thread(lambda em=c: reactor.verify_safe(em, do_smtp=do_smtp))
        used += 1
        if best_r is None or _better(r, best_r):
            best_r, best_email = r, c
        if (
            early_stop
            and do_smtp
            and best_r
            and str(best_r.category) == "deliverable"
            and float(best_r.confidence or 0) >= early_conf
        ):
            break
    return best_r, best_email, used, ""


def load_results_csv(path: Path) -> tuple[list[dict[str, str]], list[str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        fieldnames = list(reader.fieldnames or [])
        if not fieldnames:
            return [], []
        rows = []
        for row in reader:
            rows.append({k: (row.get(k) or "").strip() for k in fieldnames})
        return rows, fieldnames


def group_jobs(rows: list[dict[str, str]]) -> dict[str, DomainJob]:
    groups: dict[str, DomainJob] = {}
    for row in rows:
        dom = (row.get("domain") or "").strip()
        if not dom:
            continue
        nd = normalize_domain(dom)
        if nd not in groups:
            groups[nd] = DomainJob(norm_domain=nd, sample_website_name="", sample_landing_url="")
        job = groups[nd]
        url = (row.get("url") or "").strip()
        if url:
            for s in _build_seed_urls(url, nd):
                if s not in job.seed_urls:
                    job.seed_urls.append(s)
        wn = (row.get("website_name") or "").strip()
        if wn and len(wn) > len(job.sample_website_name):
            job.sample_website_name = wn
        if url and not job.sample_landing_url:
            job.sample_landing_url = url
    return groups


async def run_enrichment(
    *,
    input_path: Path,
    output_path: Path,
    limit_domains: int | None,
    dry_run: bool,
) -> int:
    rows, input_fieldnames = load_results_csv(input_path)
    if not rows:
        log.error("No rows in %s", input_path)
        return 1

    jobs_map = group_jobs(rows)
    job_list = list(jobs_map.values())
    if limit_domains is not None:
        job_list = job_list[:limit_domains]

    reactor: EmailTruthReactor | None = None
    if not dry_run:
        try:
            reactor = EmailTruthReactor.for_ads_scraper(
                reactor_cache_db=app_config.EMAIL_ENRICH_REACTOR_CACHE_DB,
                reactor_list_dir=app_config.EMAIL_ENRICH_REACTOR_LIST_DIR,
                list_update_interval_seconds=app_config.EMAIL_ENRICH_REACTOR_LIST_UPDATE_INTERVAL_SECONDS,
                smtp_timeout=app_config.EMAIL_ENRICH_SMTP_TIMEOUT_SECONDS,
                smtp_catchall_timeout=app_config.EMAIL_ENRICH_SMTP_CATCHALL_TIMEOUT_SECONDS,
            )
        except Exception as e:
            log.warning("reactor_init_failed: %s", e)

    do_smtp = bool(app_config.EMAIL_ENRICH_SMTP_ENABLED)
    max_probes = max(1, int(app_config.EMAIL_ENRICH_MAX_SMTP_PROBES_PER_DOMAIN))
    early_conf = float(app_config.EMAIL_ENRICH_VERIFY_EARLY_STOP_CONFIDENCE)
    early_stop = bool(getattr(app_config, "EMAIL_ENRICH_VERIFY_EARLY_STOP", False))

    domain_result: dict[str, dict[str, str]] = {}

    launch_kwargs = {
        "headless": app_config.EMAIL_ENRICH_USE_HEADLESS,
        "args": list(app_config.EMAIL_ENRICH_CHROME_ARGS),
    }
    if getattr(app_config, "USE_SYSTEM_CHROME", False):
        launch_kwargs["channel"] = "chrome"

    async with async_playwright() as p:
        browser = await p.chromium.launch(**launch_kwargs)
        try:
            for job in job_list:
                log.info("domain=%s seeds=%s", job.norm_domain, len(job.seed_urls))
                by_email: dict[str, list[str]] = {}
                source = ""
                found_on = ""

                if dry_run:
                    domain_result[job.norm_domain] = {
                        "enriched_email": "",
                        "enriched_email_source": "dry_run",
                        "enriched_email_found_on_url": "",
                        "email_category": "",
                        "email_confidence": "",
                        "email_smtp_ok": "",
                        "email_verification_notes": "dry_run",
                    }
                    continue

                by_email, _pages = await crawl_domain(
                    browser,
                    job,
                    max_pages=app_config.EMAIL_ENRICH_MAX_PAGES_PER_DOMAIN,
                    nav_timeout_ms=app_config.EMAIL_ENRICH_NAV_TIMEOUT_MS,
                    delay_min=app_config.EMAIL_ENRICH_DELAY_MIN,
                    delay_max=app_config.EMAIL_ENRICH_DELAY_MAX,
                )
                hint = job.sample_landing_url or (f"https://{job.norm_domain}/")
                best, found_on = _pick_best_non_generic(by_email, hint)
                candidates: list[str] = []
                if best:
                    candidates.append(best)
                    source = "crawl"
                else:
                    if app_config.EMAIL_ENRICH_USE_ROLE_GUESSES:
                        for c in _role_candidates(
                            job.norm_domain, list(app_config.EMAIL_ENRICH_ROLE_LOCALPARTS)
                        ):
                            if c not in candidates:
                                candidates.append(c)
                        if candidates:
                            source = "guess_role"
                    if app_config.EMAIL_ENRICH_USE_HEADLINE_GUESS:
                        headline_added = False
                        for c in _headline_candidates(
                            job.sample_website_name,
                            job.norm_domain,
                            app_config.EMAIL_ENRICH_MAX_GUESS_CANDIDATES,
                        ):
                            if c not in candidates:
                                candidates.append(c)
                                headline_added = True
                        if headline_added:
                            source = (
                                "guess_role+headline"
                                if source == "guess_role"
                                else ("guess_headline" if not source else source)
                            )
                    if not candidates and app_config.EMAIL_ENRICH_USE_ROLE_GUESSES:
                        candidates = _role_candidates(
                            job.norm_domain, list(app_config.EMAIL_ENRICH_ROLE_LOCALPARTS)
                        )
                        source = "guess_role"

                candidates = candidates[: max(40, max_probes * 4)]

                best_r, verified_email, smtp_n, verify_skip = await _verify_best(
                    reactor,
                    candidates,
                    do_smtp,
                    max_probes,
                    early_stop,
                    early_conf,
                )
                # Only emit an address that went through the reactor; no unverified fallbacks.
                display_email = (verified_email or "").strip() if best_r is not None else ""

                if best_r is not None:
                    st, reason = map_verification_to_notes(best_r)
                    cat = str(best_r.category or "")
                    conf = str(best_r.confidence or "")
                    smtp_ok = "" if best_r.smtp_accepts is None else str(bool(best_r.smtp_accepts))
                elif reactor is None:
                    st, reason = "unverified", verify_skip or "reactor_unavailable"
                    cat, conf, smtp_ok = "", "", ""
                elif not candidates:
                    st, reason = "unverified", "no_email_candidate"
                    cat, conf, smtp_ok = "", "", ""
                else:
                    st, reason = "unverified", verify_skip or "reactor_failed_all_candidates"
                    cat, conf, smtp_ok = "", "", ""

                br = best_r.breakdown if best_r else []
                notes = f"{reason}; probes={smtp_n}; breakdown={' | '.join(br[:6])}"

                domain_result[job.norm_domain] = {
                    "enriched_email": display_email,
                    "enriched_email_source": source or ("crawl" if best else ""),
                    "enriched_email_found_on_url": found_on or "",
                    "email_category": cat,
                    "email_confidence": conf,
                    "email_smtp_ok": smtp_ok,
                    "email_verification_notes": notes,
                }
        finally:
            await browser.close()

    base_cols = [c for c in input_fieldnames if c not in ENRICH_COLUMNS]
    out_fieldnames = base_cols + [c for c in ENRICH_COLUMNS if c not in base_cols]
    with output_path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=out_fieldnames, extrasaction="ignore")
        w.writeheader()
        for row in rows:
            dom = normalize_domain((row.get("domain") or "").strip())
            extra = domain_result.get(dom, {
                "enriched_email": "",
                "enriched_email_source": "",
                "enriched_email_found_on_url": "",
                "email_category": "",
                "email_confidence": "",
                "email_smtp_ok": "",
                "email_verification_notes": "no_domain_or_not_processed",
            })
            w.writerow({**row, **extra})

    log.info("Wrote %s rows to %s", len(rows), output_path)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Enrich results.csv with emails")
    parser.add_argument("--input", type=Path, default=None, help="Input CSV (default: config)")
    parser.add_argument("--output", type=Path, default=None, help="Output CSV (default: config)")
    parser.add_argument("--limit", type=int, default=None, help="Max distinct domains to process")
    parser.add_argument("--dry-run", action="store_true", help="Skip crawl and verification")
    parser.add_argument(
        "--force",
        action="store_true",
        help="Run even if EMAIL_ENRICH_ENABLED is false in config",
    )
    args = parser.parse_args()

    _setup_file_logger()

    if not app_config.EMAIL_ENRICH_ENABLED and not args.dry_run and not args.force:
        log.error("EMAIL_ENRICH_ENABLED is false in config.py (use --force to run anyway)")
        return 1

    inp = args.input or Path(app_config.EMAIL_ENRICH_INPUT)
    out = args.output or Path(app_config.EMAIL_ENRICH_OUTPUT)
    if not inp.exists():
        log.error("Input not found: %s", inp)
        return 1

    return asyncio.run(
        run_enrichment(
            input_path=inp,
            output_path=out,
            limit_domains=args.limit,
            dry_run=args.dry_run,
        )
    )


if __name__ == "__main__":
    raise SystemExit(main())
