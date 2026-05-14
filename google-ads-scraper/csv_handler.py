"""CSV input and output helpers."""

from __future__ import annotations

import csv
import os
import urllib.parse
from pathlib import Path
from typing import Dict, List

INPUT_HEADERS = ["profession", "location", "pincode"]
OUTPUT_HEADERS = [
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


def load_input_csv(input_file: str) -> List[Dict[str, str]]:
    """Read input CSV and enforce required headers."""
    path = Path(input_file)
    if not path.exists():
        raise FileNotFoundError(f"Input file not found: {input_file}")

    # utf-8-sig strips UTF-8 BOM (\ufeff) from first cell — common for Excel / Notepad saves on Windows
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames != INPUT_HEADERS:
            raise ValueError(
                f"Invalid CSV headers. Expected {INPUT_HEADERS}, got {reader.fieldnames}"
            )

        rows: List[Dict[str, str]] = []
        for row in reader:
            rows.append(
                {
                    "profession": (row.get("profession") or "").strip(),
                    "location": (row.get("location") or "").strip(),
                    "pincode": (row.get("pincode") or "").strip(),
                }
            )
        return rows


def format_query(profession: str, location: str, pincode: str) -> str:
    """Build and URL-encode search query."""
    query = f"{profession} at {location} near {pincode}".strip()
    return urllib.parse.quote_plus(query)


def write_results_csv(output_file: str, all_results: Dict[str, List[Dict[str, str]]]) -> None:
    """Write full snapshot: header plus rows sorted by location then domain.

    Opens in ``w`` (truncates), writes atomically from the caller's perspective,
    then flushes and fsyncs so another process or Excel refresh sees completed
    rows after each pincode when this is called incrementally from ``main``.
    """
    path = Path(output_file)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=OUTPUT_HEADERS)
        writer.writeheader()

        for location in sorted(all_results.keys()):
            location_results = sorted(all_results[location], key=lambda row: row["domain"])
            for result in location_results:
                writer.writerow(result)
        handle.flush()
        try:
            os.fsync(handle.fileno())
        except OSError:
            pass
