"""Core Playwright scraping logic."""

from __future__ import annotations

import asyncio
import random
from typing import Dict, List

from playwright.async_api import Browser, Page
from playwright_stealth import Stealth

from config import (
    CAPTCHA_ALERT_SOUND,
    CAPTCHA_GRACE_SECONDS,
    HEADLESS_MODE,
    HUMAN_MOUSE_AND_SCROLL,
    MAX_RETRIES_ON_FAILURE,
    PAUSE_FOR_MANUAL_CAPTCHA,
    POST_SEARCH_SETTLE_MAX,
    POST_SEARCH_SETTLE_MIN,
    PROXY_ENABLED,
    PROXY_STRING,
    RETRY_BACKOFF_MULTIPLIER,
    TIMEOUT_PAGE_LOAD,
    TIMEOUT_SELECTOR,
    VIEWPORT_HEIGHT,
    VIEWPORT_WIDTH,
    WARMUP_DELAY_MAX,
    WARMUP_DELAY_MIN,
    WARMUP_GOOGLE_HOME,
)
from extractors import is_valid_sponsored_link, unpack_google_redirect_url
from utils import get_random_user_agent, play_captcha_alert

STEALTH = Stealth()


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

async def extract_sponsored_ads(page: Page, search_query: str, logger) -> List[Dict[str, str]]:
    """Extract sponsored links using text-based labels only."""
    results: List[Dict[str, str]] = []

    try:
        await page.wait_for_selector('text="Sponsored"', timeout=TIMEOUT_SELECTOR)
    except Exception:
        pass

    for label_text in ("Sponsored", "Ad"):
        labels = await page.locator(f'text="{label_text}"').all()
        for label in labels:
            try:
                container = label.locator(".. >> .. >> ..").first
                link = container.locator("a[href]").first
                if not await link.is_visible():
                    continue

                href = await link.get_attribute("href")
                display_text = await link.inner_text()
                if not is_valid_sponsored_link(href, display_text):
                    continue

                actual_url = unpack_google_redirect_url(href or "")
                results.append(
                    {
                        "url": actual_url,
                        "display_text": display_text,
                        "source_query": search_query,
                    }
                )
            except Exception:
                continue
        if results:
            break

    logger.info(
        "Sponsored extraction completed",
        extra={
            "event_type": "results_extracted",
            "payload": {"query": search_query, "sponsored_links_found": len(results)},
        },
    )
    return results


async def run_browser_search(
    browser: Browser, search_query: str, iteration: int, logger
) -> Dict[str, object]:
    """Run one iteration for a single query and return result metadata."""
    last_error = ""
    for attempt in range(1, MAX_RETRIES_ON_FAILURE + 1):
        context = None
        try:
            context_options = {
                "user_agent": get_random_user_agent(),
                "locale": "en-US",
                "timezone_id": "America/Chicago",
                "viewport": {"width": VIEWPORT_WIDTH, "height": VIEWPORT_HEIGHT},
            }
            if PROXY_ENABLED:
                context_options["proxy"] = {"server": PROXY_STRING}

            context = await browser.new_context(**context_options)
            await STEALTH.apply_stealth_async(context)
            page = await context.new_page()

            if WARMUP_GOOGLE_HOME:
                await page.goto("https://www.google.com/?hl=en&gl=us", timeout=TIMEOUT_PAGE_LOAD)
                await page.wait_for_load_state("domcontentloaded")
                await asyncio.sleep(random.uniform(WARMUP_DELAY_MIN, WARMUP_DELAY_MAX))

            url = f"https://www.google.com/search?q={search_query}&hl=en&gl=us"
            await page.goto(url, timeout=TIMEOUT_PAGE_LOAD)
            await page.wait_for_load_state("domcontentloaded")
            await _humanize_after_navigation(page)

            page_text = await page.content()
            page_title = await page.title()
            if is_blocked_or_captcha_page(page_text, page.url, page_title):
                can_wait = PAUSE_FOR_MANUAL_CAPTCHA and not HEADLESS_MODE
                if can_wait:
                    cleared = await _offer_manual_captcha_solve(page, logger)
                    if not cleared:
                        return {"results": [], "captcha": True, "error": "captcha_detected"}
                else:
                    return {"results": [], "captcha": True, "error": "captcha_detected"}

            results = await extract_sponsored_ads(page, search_query, logger)
            return {"results": results, "captcha": False, "error": ""}
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
