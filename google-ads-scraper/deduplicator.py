"""Deduplication helpers for scraper results."""

from __future__ import annotations

from typing import Dict, Set, Tuple

from extractors import normalize_domain


class DeduplicationIndex:
    """Tracks seen result keys for O(1) duplicate checks."""

    def __init__(self) -> None:
        self._seen: Set[Tuple[str, str, str]] = set()

    def is_duplicate(self, result: Dict[str, str]) -> bool:
        key = (
            normalize_domain(result["domain"]),
            result["profession"].strip().lower(),
            result["location"].strip().lower(),
        )
        if key in self._seen:
            return True
        self._seen.add(key)
        return False
