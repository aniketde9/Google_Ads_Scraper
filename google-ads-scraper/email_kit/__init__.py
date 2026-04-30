"""Vendored email extraction and verification helpers for results enrichment."""

from email_kit.email_public import (
    email_domain_matches_site,
    extract_emails_from_text,
    generate_personal_guesses,
    hostname_from_url,
    is_generic_localpart,
    sanitize_author_name_for_guessing,
)
from email_kit.truth_reactor import EmailTruthReactor
from email_kit.types import VerificationResult

__all__ = [
    "EmailTruthReactor",
    "VerificationResult",
    "extract_emails_from_text",
    "generate_personal_guesses",
    "is_generic_localpart",
    "sanitize_author_name_for_guessing",
    "email_domain_matches_site",
    "hostname_from_url",
]
