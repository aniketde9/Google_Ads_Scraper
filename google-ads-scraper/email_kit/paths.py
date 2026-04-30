"""Project paths for email reactor data (cache, lists)."""

from __future__ import annotations

from pathlib import Path


def project_root() -> Path:
    """google-ads-scraper directory (parent of email_kit)."""
    return Path(__file__).resolve().parent.parent
