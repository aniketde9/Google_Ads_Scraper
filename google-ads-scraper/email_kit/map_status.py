"""Map reactor output to CSV-friendly status strings."""

from __future__ import annotations

from email_kit.types import VerificationResult


def map_verification_to_notes(r: VerificationResult) -> tuple[str, str]:
    """Return (status_label, reason) for enriched CSV."""
    if r.skipped:
        return "unverified", f"reactor:skipped:{r.skip_reason}"
    if r.error and r.final_label == "Reactor error":
        err = (r.error or "")[:120]
        return "unverified", f"reactor:error:{err}"
    if not r.syntax_valid:
        return "invalid", "reactor:invalid:syntax"
    if r.disposable or not r.mx_valid or r.parked:
        return "invalid", "reactor:invalid:dns_or_list"
    if r.smtp_not_run:
        return "unverified", f"reactor:dns_only:{r.category}"
    if r.category == "deliverable":
        return "verified", "reactor:deliverable"
    if r.category == "risky":
        return "risky", "reactor:risky"
    if r.category == "invalid":
        return "invalid", "reactor:invalid:smtp_or_score"
    return "unverified", f"reactor:unknown:{r.category}"
