"""Core Playwright scraping logic."""

from __future__ import annotations

import asyncio
import random
from typing import Dict, List

from playwright.async_api import Browser, BrowserContext, Page
from playwright_stealth import Stealth

from config import (
    CAPTCHA_ALERT_SOUND,
    CAPTCHA_GRACE_SECONDS,
    DATA_TEXT_AD_WAIT_MS,
    EXTRACT_INITIAL_SCROLL_PAUSE_SEC,
    EXTRACT_PAGE_SETTLE_SEC,
    EXTRACT_SPONSORED_TIMEOUT_MS,
    HEADLESS_MODE,
    HUMAN_MOUSE_AND_SCROLL,
    MAX_RETRIES_ON_FAILURE,
    PAUSE_FOR_MANUAL_CAPTCHA,
    POST_SEARCH_SETTLE_MAX,
    POST_SEARCH_SETTLE_MIN,
    PROXY_ENABLED,
    PROXY_STRING,
    RETRY_BACKOFF_MULTIPLIER,
    SERP_RENAV_MAX_ATTEMPTS,
    SERP_VERIFY_TIMEOUT_MS,
    TIMEOUT_PAGE_LOAD,
    VIEWPORT_HEIGHT,
    VIEWPORT_WIDTH,
    WARMUP_DELAY_MAX,
    WARMUP_DELAY_MIN,
    WARMUP_GOOGLE_HOME,
)
from extractors import is_valid_sponsored_link, unpack_google_redirect_url
from utils import get_random_user_agent, play_captcha_alert

STEALTH = Stealth()


def _dedupe_url_key(url: str) -> str:
    u = (url or "").lower().strip()
    if not u:
        return ""
    return u.split("?")[0].rstrip("/")


def is_blocked_or_captcha_page(html_text: str, page_url: str, page_title: str = "") -> bool:
    """Detect Google sorry / unusual-traffic pages.

    Avoid matching generic 'recaptcha' in normal SERP scripts (false positives).
    """
    url_lower = (page_url or "").lower()
    if "/sorry/" in url_lower or "google.com/sorry" in url_lower:
        return True

    title_lower = (page_title or "").lower()
    if "unusual traffic" in title_lower:
        return True

    lowered = html_text.lower()
    if "unusual traffic" in lowered:
        return True
    if "automated queries" in lowered:
        return True
    if "sorry, but your computer or network may be sending automated queries" in lowered:
        return True
    # Interstitial wording sometimes appears without "unusual traffic" in title yet
    if "our systems have detected unusual traffic" in lowered:
        return True
    return False


async def _offer_manual_captcha_solve(page: Page, logger) -> bool:
    """Alert user, allow a grace period to verify in-browser, then re-check (Enter if still blocked)."""
    logger.info(
        "Waiting for manual CAPTCHA / verification in browser",
        extra={
            "event_type": "manual_captcha_wait",
            "payload": {
                "message": "Solve challenge in browser",
                "grace_seconds": CAPTCHA_GRACE_SECONDS,
                "sound": CAPTCHA_ALERT_SOUND,
            },
        },
    )
    print(
        "\n"
        + "=" * 62
        + "\n"
        "  Google showed a verification page — complete it in the Chromium window.\n"
        f"  You have {CAPTCHA_GRACE_SECONDS} seconds; listen for the alert sound.\n"
        + "=" * 62
        + "\n",
        flush=True,
    )
    if CAPTCHA_ALERT_SOUND:
        await asyncio.to_thread(play_captcha_alert)

    await asyncio.sleep(CAPTCHA_GRACE_SECONDS)

    try:
        await page.wait_for_load_state("domcontentloaded", timeout=10000)
    except Exception:
        pass
    await asyncio.sleep(0.5)
    page_text = await page.content()
    page_title = await page.title()
    if not is_blocked_or_captcha_page(page_text, page.url, page_title):
        logger.info(
            "Verification page cleared after grace period",
            extra={"event_type": "manual_captcha_cleared", "payload": {}},
        )
        print("  Page looks clear — continuing scrape.\n", flush=True)
        return True

    print(
        "\n  Still on a block/verification page after the grace period.\n"
        "  Finish any remaining steps in the browser, then press ENTER here.\n",
        flush=True,
    )
    if CAPTCHA_ALERT_SOUND:
        await asyncio.to_thread(play_captcha_alert)

    await asyncio.to_thread(input, "Press ENTER when search results are visible... ")
    try:
        await page.wait_for_load_state("domcontentloaded", timeout=15000)
    except Exception:
        pass
    await asyncio.sleep(0.75)
    page_text = await page.content()
    page_title = await page.title()
    return not is_blocked_or_captcha_page(page_text, page.url, page_title)


async def _humanize_after_navigation(page: Page) -> None:
    """Short settle + light mouse/scroll to mimic human viewing (best-effort)."""
    await asyncio.sleep(random.uniform(POST_SEARCH_SETTLE_MIN, POST_SEARCH_SETTLE_MAX))
    if not HUMAN_MOUSE_AND_SCROLL:
        return
    try:
        vp = page.viewport_size
        vw = int((vp or {}).get("width") or VIEWPORT_WIDTH)
        vh = int((vp or {}).get("height") or VIEWPORT_HEIGHT)
        await page.mouse.move(
            random.randint(40, max(41, vw // 2)),
            random.randint(40, max(41, vh // 2)),
        )
        await page.evaluate(
            "window.scrollBy(0, arguments[0])",
            random.randint(120, 420),
        )
        await asyncio.sleep(random.uniform(0.15, 0.55))
    except Exception:
        pass

async def _scroll_results_for_lazy_ads(page: Page) -> None:
    """Reveal ads that load after scroll."""
    try:
        await page.evaluate("window.scrollTo(0, 0)")
        await asyncio.sleep(0.2)
        for delta in (380, 520, 700, 400):
            await page.evaluate("window.scrollBy(0, arguments[0])", delta)
            await asyncio.sleep(0.35)
    except Exception:
        pass


def _append_link_if_sponsored(
    href: str | None,
    display_text: str,
    search_query: str,
    seen_urls: set[str],
    results: List[Dict[str, str]],
) -> None:
    if not href:
        return
    href_norm = href
    if href_norm.startswith("//"):
        href_norm = "https:" + href_norm
    if not is_valid_sponsored_link(href_norm, display_text):
        return
    actual_url = unpack_google_redirect_url(href_norm)
    key = _dedupe_url_key(actual_url)
    if not key or key in seen_urls:
        return
    seen_urls.add(key)
    results.append(
        {
            "url": actual_url,
            "display_text": display_text,
            "source_query": search_query,
        }
    )


async def _extract_one_link_from_label(
    label,
    search_query: str,
    seen_urls: set[str],
    results: List[Dict[str, str]],
    *,
    min_depth: int,
    max_depth: int,
    min_headline_len: int,
) -> None:
    """From a visible Sponsored/Ad badge, walk up; first valid ad link per label wins."""
    try:
        if not await label.is_visible():
            return
    except Exception:
        return

    for depth in range(min_depth, max_depth + 1):
        try:
            container = label
            for _ in range(depth):
                container = container.locator("..").first
            links = await container.locator("a[href]").all()
            for link in links:
                try:
                    if not await link.is_visible():
                        continue
                    href = await link.get_attribute("href")
                    display_text = await link.inner_text()
                    if len((display_text or "").strip()) < min_headline_len:
                        continue
                    before = len(results)
                    _append_link_if_sponsored(href, display_text, search_query, seen_urls, results)
                    if len(results) > before:
                        return
                except Exception:
                    continue
        except Exception:
            continue


def _is_google_search_url(url: str) -> bool:
    u = (url or "").lower()
    return "google." in u and "/search" in u


async def ensure_on_google_serp(
    page: Page, encoded_search_query: str, logger, *, after_captcha: bool = False
) -> bool:
    """Ensure we are on a real Google SERP (not consent interstitial only). Re-navigate if needed."""
    payload_extra = {"after_captcha": after_captcha}
    if _is_google_search_url(page.url):
        try:
            await page.wait_for_load_state("domcontentloaded", timeout=min(8000, SERP_VERIFY_TIMEOUT_MS))
        except Exception:
            pass
        return True

    logger.info(
        "Not on Google SERP; attempting re-navigation",
        extra={
            "event_type": "serp_renavigate",
            "payload": {**payload_extra, "current_url": page.url or ""},
        },
    )
    target = f"https://www.google.com/search?q={encoded_search_query}&hl=en&gl=us"
    for attempt in range(1, SERP_RENAV_MAX_ATTEMPTS + 1):
        try:
            await page.goto(target, timeout=TIMEOUT_PAGE_LOAD)
            await page.wait_for_load_state("domcontentloaded")
            try:
                await page.wait_for_url("**/search**", timeout=SERP_VERIFY_TIMEOUT_MS)
            except Exception:
                pass
            await asyncio.sleep(2.0 if after_captcha else 1.0)
            if _is_google_search_url(page.url):
                logger.info(
                    "SERP confirmed after re-navigation",
                    extra={
                        "event_type": "serp_confirmed",
                        "payload": {"attempt": attempt, "url": page.url or ""},
                    },
                )
                return True
        except Exception as exc:
            logger.error(
                f"SERP re-navigation failed: {exc}",
                extra={
                    "event_type": "serp_renav_error",
                    "payload": {"attempt": attempt, "error": str(exc)},
                },
            )
    logger.error(
        "Could not land on Google search results page",
        extra={"event_type": "serp_verify_failed", "payload": {"url": page.url or ""}},
    )
    return False


async def _extract_from_data_text_ad(
    page: Page, search_query: str, seen_urls: set[str], results: List[Dict[str, str]]
) -> None:
    """Primary2026 path: grouped text ads use ``data-text-ad="1"`` on each card."""
    try:
        loc = page.locator('[data-text-ad="1"]')
        n = await loc.count()
        for i in range(n):
            try:
                container = loc.nth(i)
                if not await container.is_visible():
                    continue
                link = container.locator("a[data-ved][href]").first
                if not await link.is_visible():
                    link = container.locator("a[href]").first
                    if not await link.is_visible():
                        continue
                href = await link.get_attribute("href")
                headline = ""
                try:
                    h3 = container.locator("h3").first
                    if await h3.is_visible():
                        headline = (await h3.inner_text()).strip()
                except Exception:
                    pass
                if not headline:
                    headline = (await link.inner_text()).strip()
                _append_link_if_sponsored(href, headline, search_query, seen_urls, results)
            except Exception:
                continue
    except Exception:
        pass


async def _extract_from_sponsored_results_header(
    page: Page, search_query: str, seen_urls: set[str], results: List[Dict[str, str]]
) -> None:
    """Fallback: section header 'Sponsored results' + ancestor scope for links."""
    try:
        hdr = page.get_by_text("Sponsored results", exact=True)
        if await hdr.count() == 0:
            hdr = page.get_by_text("Sponsored results")
        if await hdr.count() == 0:
            return
        header = hdr.first

        for depth in (3, 4, 5, 6, 8):
            try:
                section = header.locator(f"xpath=ancestor::div[{depth}]")
                if await section.count() == 0:
                    continue
                links = await section.locator("a[href]").all()
                for link in links:
                    try:
                        if not await link.is_visible():
                            continue
                        href = await link.get_attribute("href")
                        display_text = await link.inner_text()
                        _append_link_if_sponsored(
                            href, display_text, search_query, seen_urls, results
                        )
                    except Exception:
                        continue
            except Exception:
                continue
    except Exception:
        pass


async def _extract_from_u_eierd_containers(
    page: Page, search_query: str, seen_urls: set[str], results: List[Dict[str, str]]
) -> None:
    """Known ad wrapper (often ``uEierd``) + Sponsored — class names change; this is best-effort."""
    try:
        loc = page.locator('div.uEierd:has-text("Sponsored")')
        n = await loc.count()
        for i in range(n):
            try:
                container = loc.nth(i)
                if not await container.is_visible():
                    continue
                for link in await container.locator("a[href]").all():
                    if not await link.is_visible():
                        continue
                    href = await link.get_attribute("href")
                    display_text = await link.inner_text()
                    _append_link_if_sponsored(href, display_text, search_query, seen_urls, results)
            except Exception:
                continue
    except Exception:
        pass


_SPONSORED_JS_FALLBACK = """
() => {
  const rows = [];
  document.querySelectorAll('*').forEach((el) => {
    const t = (el.textContent || '').trim();
    if (t !== 'Sponsored' && t !== 'Ad') return;
    let node = el;
    for (let i = 0; i < 8 && node; i++) {
      node.querySelectorAll('a[href]').forEach((link) => {
        let h = link.getAttribute('href') || '';
        if (!h) return;
        if (h.startsWith('/')) {
          try { h = new URL(h, window.location.href).href; } catch (e) {}
        }
        if (h.startsWith('//')) h = 'https:' + h;
        const tx = (link.innerText || '').trim();
        if (tx.length >= 2) rows.push({ url: h, display_text: tx });
      });
      node = node.parentElement;
    }
  });
  return rows;
}
"""


async def extract_sponsored_ads(page: Page, search_query: str, logger) -> List[Dict[str, str]]:
    """Sponsored extraction: ``data-text-ad``, 'Sponsored results' block, then legacy fallbacks."""
    results: List[Dict[str, str]] = []
    seen_urls: set[str] = set()

    try:
        await page.wait_for_selector('[data-text-ad="1"]', timeout=DATA_TEXT_AD_WAIT_MS)
    except Exception:
        try:
            await page.wait_for_selector("text=Sponsored results", timeout=min(8000, EXTRACT_SPONSORED_TIMEOUT_MS))
        except Exception:
            try:
                await page.wait_for_selector('text="Sponsored"', timeout=min(6000, EXTRACT_SPONSORED_TIMEOUT_MS))
            except Exception:
                try:
                    await page.wait_for_selector('text="Ad"', timeout=min(4000, EXTRACT_SPONSORED_TIMEOUT_MS))
                except Exception:
                    pass

    try:
        await asyncio.sleep(EXTRACT_PAGE_SETTLE_SEC)
        await page.evaluate("window.scrollBy(0, 600)")
        await asyncio.sleep(EXTRACT_INITIAL_SCROLL_PAUSE_SEC)
    except Exception:
        pass

    await _scroll_results_for_lazy_ads(page)

    try:
        await _extract_from_data_text_ad(page, search_query, seen_urls, results)
    except Exception as exc:
        logger.error(
            f"data-text-ad extract failed: {exc}",
            extra={"event_type": "extract_error", "payload": {"error": str(exc)}},
        )

    if not results:
        try:
            await _extract_from_sponsored_results_header(page, search_query, seen_urls, results)
        except Exception as exc:
            logger.error(
                f"Sponsored results header extract failed: {exc}",
                extra={"event_type": "extract_error", "payload": {"error": str(exc)}},
            )

    try:
        await _extract_from_u_eierd_containers(page, search_query, seen_urls, results)
    except Exception as exc:
        logger.error(
            f"uEierd extract pass failed: {exc}",
            extra={"event_type": "extract_error", "payload": {"error": str(exc)}},
        )

    try:
        for label in await page.locator('text="Sponsored"').all():
            await _extract_one_link_from_label(
                label,
                search_query,
                seen_urls,
                results,
                min_depth=2,
                max_depth=8,
                min_headline_len=2,
            )
    except Exception as exc:
        logger.error(
            f"Sponsored label pass failed: {exc}",
            extra={"event_type": "extract_error", "payload": {"error": str(exc)}},
        )

    if not results:
        try:
            for label in await page.locator('text="Ad"').all():
                await _extract_one_link_from_label(
                    label,
                    search_query,
                    seen_urls,
                    results,
                    min_depth=2,
                    max_depth=8,
                    min_headline_len=4,
                )
        except Exception as exc:
            logger.error(
                f"Ad label fallback failed: {exc}",
                extra={"event_type": "extract_error", "payload": {"error": str(exc)}},
            )

    if not results:
        try:
            raw = await page.evaluate(_SPONSORED_JS_FALLBACK)
            if isinstance(raw, list):
                for item in raw:
                    if not isinstance(item, dict):
                        continue
                    href = item.get("url")
                    display_text = item.get("display_text") or ""
                    _append_link_if_sponsored(
                        str(href) if href else None,
                        str(display_text),
                        search_query,
                        seen_urls,
                        results,
                    )
        except Exception as exc:
            logger.error(
                f"JS sponsored fallback failed: {exc}",
                extra={"event_type": "extract_error", "payload": {"error": str(exc)}},
            )

    logger.info(
        "Sponsored extraction completed",
        extra={
            "event_type": "results_extracted",
            "payload": {"query": search_query, "sponsored_links_found": len(results)},
        },
    )
    return results


def _new_context_options() -> dict:
    opts: dict = {
        "user_agent": get_random_user_agent(),
        "locale": "en-US",
        "timezone_id": "America/Chicago",
        "viewport": {"width": VIEWPORT_WIDTH, "height": VIEWPORT_HEIGHT},
    }
    if PROXY_ENABLED:
        opts["proxy"] = {"server": PROXY_STRING}
    return opts


async def navigate_search_and_extract(
    page: Page, search_query: str, iteration: int, logger
) -> Dict[str, object]:
    """Load Google SERP on an existing page, handle CAPTCHA, return sponsored links."""
    url = f"https://www.google.com/search?q={search_query}&hl=en&gl=us"
    await page.goto(url, timeout=TIMEOUT_PAGE_LOAD)
    await page.wait_for_load_state("domcontentloaded")
    await _humanize_after_navigation(page)

    page_text = await page.content()
    page_title = await page.title()
    captcha_resolved = False
    if is_blocked_or_captcha_page(page_text, page.url, page_title):
        can_wait = PAUSE_FOR_MANUAL_CAPTCHA and not HEADLESS_MODE
        if can_wait:
            cleared = await _offer_manual_captcha_solve(page, logger)
            if not cleared:
                return {"results": [], "captcha": True, "error": "captcha_detected"}
            captcha_resolved = True
        else:
            return {"results": [], "captcha": True, "error": "captcha_detected"}

    if not await ensure_on_google_serp(
        page, search_query, logger, after_captcha=captcha_resolved
    ):
        return {"results": [], "captcha": False, "error": "not_on_search_page"}

    await asyncio.sleep(random.uniform(0.25, 0.65))
    results = await extract_sponsored_ads(page, search_query, logger)
    return {"results": results, "captcha": False, "error": ""}


async def open_persistent_session(browser: Browser) -> tuple[BrowserContext, Page]:
    """Single context + tab for the whole run (warmup optional)."""
    context = await browser.new_context(**_new_context_options())
    await STEALTH.apply_stealth_async(context)
    page = await context.new_page()
    if WARMUP_GOOGLE_HOME:
        await page.goto("https://www.google.com/?hl=en&gl=us", timeout=TIMEOUT_PAGE_LOAD)
        await page.wait_for_load_state("domcontentloaded")
        await asyncio.sleep(random.uniform(WARMUP_DELAY_MIN, WARMUP_DELAY_MAX))
    return context, page


async def run_browser_search(
    browser: Browser, search_query: str, iteration: int, logger
) -> Dict[str, object]:
    """Run one iteration in a fresh incognito context (spec default when PERSISTENT_MODE off)."""
    last_error = ""
    for attempt in range(1, MAX_RETRIES_ON_FAILURE + 1):
        context = None
        try:
            context = await browser.new_context(**_new_context_options())
            await STEALTH.apply_stealth_async(context)
            page = await context.new_page()

            if WARMUP_GOOGLE_HOME:
                await page.goto("https://www.google.com/?hl=en&gl=us", timeout=TIMEOUT_PAGE_LOAD)
                await page.wait_for_load_state("domcontentloaded")
                await asyncio.sleep(random.uniform(WARMUP_DELAY_MIN, WARMUP_DELAY_MAX))

            return await navigate_search_and_extract(page, search_query, iteration, logger)
        except Exception as exc:
            last_error = str(exc)
            logger.error(
                f"Search failed on attempt {attempt}",
                extra={
                    "event_type": "search_error",
                    "payload": {
                        "query": search_query,
                        "iteration": iteration,
                        "attempt": attempt,
                        "error": last_error,
                    },
                },
            )
            if attempt < MAX_RETRIES_ON_FAILURE:
                await asyncio.sleep(RETRY_BACKOFF_MULTIPLIER**attempt)
        finally:
            if context:
                await context.close()

    return {"results": [], "captcha": False, "error": last_error}
