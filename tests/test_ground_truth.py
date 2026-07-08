"""
Ground-truth integration tests — live portal record-count bounds.

Validates that high-value extractors still return plausible volumes.
Bounds live in tests/ground_truth.yaml (human-reviewed).

Run: pytest tests/test_ground_truth.py -m integration
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.integration

_GROUND_TRUTH_PATH = Path(__file__).parent / "ground_truth.yaml"


def _load_cases():
    with open(_GROUND_TRUTH_PATH, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return data.get("targets") or []


@pytest.mark.parametrize("case", _load_cases(), ids=lambda c: c["name"])
def test_ground_truth_record_counts(case):
    method = case["method"]
    min_n = case["min_records"]
    max_n = case["max_records"]

    if method == "extract_target":
        from housing_list_search.extraction import extract_target

        records = extract_target(case["url"], case.get("authority", ""))
        count = len(records)
    elif method in ("run_target", "scrape_target"):
        from housing_list_search.dispatch import scrape_target

        outcome = scrape_target(case["target"])
        records = outcome.records
        count = len(records)
    else:
        pytest.fail(f"Unknown ground-truth method: {method}")

    assert min_n <= count <= max_n, f"{case['name']}: expected {min_n}–{max_n} records, got {count}"
