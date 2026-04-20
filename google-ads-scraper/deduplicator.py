"""Deduplication helpers for scraper results."""

from __future__ import annotations

from typing import Dict, Set, Tuple

from extractors import normalize_domain


class DeduplicationIndex:
    """Tracks seen result keys for O(1) duplicate checks."""

    def __init__(self) -> None:
        self._seen: Set[Tuple[str, str, str]] = set()
        self._counts_by_domain: Dict[str, int] = {}

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

    def _global_domain_key(self, result: Dict[str, str]) -> str:
        return normalize_domain(result["domain"])

    def register_sighting(self, result: Dict[str, str]) -> tuple[bool, int, str]:
        """Track every sighting and return (is_new_result, global_count, domain_key)."""
        domain_key = self._global_domain_key(result)
        current_count = self._counts_by_domain.get(domain_key, 0) + 1
        self._counts_by_domain[domain_key] = current_count
        return (not self.is_duplicate(result), current_count, domain_key)
