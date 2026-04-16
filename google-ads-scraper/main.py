"""Entry point for Google Ads sponsored results scraper."""

from __future__ import annotations

import asyncio
import random
from collections import defaultdict
from typing import Dict, List

from playwright.async_api import async_playwright

from config import (
    CAPTCHA_COOLDOWN,
    CHROMIUM_EXTRA_ARGS,
    COMPLIANCE_MODE,
    DELAY_BETWEEN_ITERATIONS_MAX,
    DELAY_BETWEEN_ITERATIONS_MIN,
    DELAY_BETWEEN_QUERIES,
    DELAY_BETWEEN_QUERIES_JITTER_MAX,
    DELAY_BETWEEN_QUERIES_JITTER_MIN,
    HEADLESS_MODE,
    INPUT_FILE,
    ITERATIONS_PER_QUERY,
    MAX_CAPTCHA_ENCOUNTERS,
    OUTPUT_FILE,
    PERSISTENT_MODE,
    USE_SYSTEM_CHROME,
)
from csv_handler import format_query, load_input_csv, write_results_csv
from deduplicator import DeduplicationIndex
from extractors import current_timestamp, extract_domain, extract_website_name
from scraper import navigate_search_and_extract, open_persistent_session, run_browser_search
from utils import setup_logger

LEGAL_DISCLAIMER = """
WARNING: This tool is intended for internal market research only.
Automated scraping of Google Search may violate Google's Terms of Service and robots policy.
You are responsible for legal and compliance decisions before running this software.
""".strip()


def print_legal_disclaimer() -> None:
    """Print startup compliance warning."""
    print(LEGAL_DISCLAIMER)


async def run() -> int:
    """Run end-to-end scraping workflow."""
    if COMPLIANCE_MODE:
        print_legal_disclaimer()

    logger = setup_logger()
    logger.info(
        "Scraper startup",
        extra={
            "event_type": "startup",
            "payload": {"message": "COMPLIANCE_MODE active. Internal use only."},
        },
    )

    rows = load_input_csv(INPUT_FILE)
    all_results: Dict[str, List[Dict[str, str]]] = defaultdict(list)
    dedupe = DeduplicationIndex()
    captcha_count = 0

    launch_kwargs = {
        "headless": HEADLESS_MODE,
        "args": list(CHROMIUM_EXTRA_ARGS),
    }
    if USE_SYSTEM_CHROME:
        launch_kwargs["channel"] = "chrome"

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(**launch_kwargs)
        persistent_context = None
        try:
            if PERSISTENT_MODE:
                persistent_context, persistent_page = await open_persistent_session(browser)
                logger.info(
                    "Persistent session started",
                    extra={
                        "event_type": "persistent_session",
                        "payload": {"message": "Single tab will run all searches until done."},
                    },
                )

            for row_index, row in enumerate(rows):
                profession = row["profession"]
                location = row["location"]
                pincode = row["pincode"]
                search_query = format_query(profession, location, pincode)

                for iteration in range(1, ITERATIONS_PER_QUERY + 1):
                    logger.info(
                        "Query started",
                        extra={
                            "event_type": "query_started",
                            "payload": {
                                "profession": profession,
                                "location": location,
                                "pincode": pincode,
                                "iteration": iteration,
                            },
                        },
                    )

                    if PERSISTENT_MODE:
                        outcome = await navigate_search_and_extract(
                            persistent_page, search_query, iteration, logger
                        )
                    else:
                        outcome = await run_browser_search(browser, search_query, iteration, logger)

                    if outcome["captcha"]:
                        captcha_count += 1
                        logger.error(
                            "CAPTCHA detected",
                            extra={
                                "event_type": "error",
                                "payload": {
                                    "error_type": "CAPTCHA",
                                    "query": search_query,
                                    "iteration": iteration,
                                    "captcha_count": captcha_count,
                                },
                            },
                        )
                        if captcha_count >= MAX_CAPTCHA_ENCOUNTERS:
                            logger.error(
                                "CAPTCHA threshold reached; cooling down",
                                extra={
                                    "event_type": "captcha_cooldown",
                                    "payload": {"cooldown_seconds": CAPTCHA_COOLDOWN},
                                },
                            )
                            await asyncio.sleep(CAPTCHA_COOLDOWN)
                            return 1
                        await asyncio.sleep(CAPTCHA_COOLDOWN)
                        continue

                    new_records = 0
                    for link in outcome["results"]:
                        url = link["url"]
                        domain = extract_domain(url)
                        result = {
                            "profession": profession,
                            "location": location,
                            "pincode": pincode,
                            "website_name": extract_website_name(link["display_text"]),
                            "url": url,
                            "domain": domain,
                            "run_number": iteration,
                            "timestamp": current_timestamp(),
                        }
                        if not dedupe.is_duplicate(result):
                            all_results[location].append(result)
                            new_records += 1

                    logger.info(
                        "Iteration completed",
                        extra={
                            "event_type": "results_extracted",
                            "payload": {
                                "query": search_query,
                                "sponsored_links_found": len(outcome["results"]),
                                "unique_new_records": new_records,
                            },
                        },
                    )

                    await asyncio.sleep(
                        random.uniform(
                            DELAY_BETWEEN_ITERATIONS_MIN,
                            DELAY_BETWEEN_ITERATIONS_MAX,
                        )
                    )

                if row_index < len(rows) - 1:
                    await asyncio.sleep(
                        DELAY_BETWEEN_QUERIES
                        + random.uniform(
                            DELAY_BETWEEN_QUERIES_JITTER_MIN,
                            DELAY_BETWEEN_QUERIES_JITTER_MAX,
                        )
                    )
        finally:
            if persistent_context:
                await persistent_context.close()
            await browser.close()

    write_results_csv(OUTPUT_FILE, all_results)
    logger.info(
        "Scrape completed",
        extra={
            "event_type": "completed",
            "payload": {"records_written": sum(len(v) for v in all_results.values())},
        },
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run()))
