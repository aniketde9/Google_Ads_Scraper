"""Runtime utilities: delays, UA pool, and logging."""

from __future__ import annotations

import json
import logging
import random
import time
from typing import Dict

from config import LOG_FILE

USER_AGENT_POOL = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
]


class JsonFormatter(logging.Formatter):
    """Simple JSON formatter for structured logs."""

    def format(self, record: logging.LogRecord) -> str:
        payload: Dict[str, object] = {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(record.created)),
            "event_type": getattr(record, "event_type", "log"),
            "message": record.getMessage(),
        }
        extra_payload = getattr(record, "payload", None)
        if isinstance(extra_payload, dict):
            payload.update(extra_payload)
        return json.dumps(payload, ensure_ascii=True)


def setup_logger() -> logging.Logger:
    """Configure logger for scraper."""
    logger = logging.getLogger("google_ads_scraper")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()

    handler = logging.FileHandler(LOG_FILE, encoding="utf-8")
    handler.setFormatter(JsonFormatter())
    logger.addHandler(handler)
    logger.propagate = False
    return logger


def get_random_user_agent() -> str:
    """Return random user-agent string."""
    return random.choice(USER_AGENT_POOL)
