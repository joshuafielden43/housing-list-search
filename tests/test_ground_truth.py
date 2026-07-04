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
    elif method == "run_target":
        from housing_list_search.runner import run_target

        records = run_target(case["target"])
        count = len(records)
    else:
        pytest.fail(f"Unknown ground-truth method: {method}")

    assert min_n <= count <= max_n, f"{case['name']}: expected {min_n}–{max_n} records, got {count}"
