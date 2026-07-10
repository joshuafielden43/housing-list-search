"""
Ground-truth integration tests — live portal record-count bounds.

Validates that high-value extractors still return plausible volumes across
adapter families (#662). Bounds live in tests/ground_truth.yaml (human-reviewed).

Run: pytest tests/test_ground_truth.py -m integration

Filters:
  HLS_GT_MODE=all|core|rotate  (default all)
  HLS_GT_NAMES=name1,name2     (explicit subset; overrides mode)
"""

from __future__ import annotations

import os
from datetime import date
from pathlib import Path

import pytest
import yaml

_GROUND_TRUTH_PATH = Path(__file__).parent / "ground_truth.yaml"


def _load_all_cases() -> list[dict]:
    with open(_GROUND_TRUTH_PATH, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return list(data.get("targets") or [])


def _select_cases(cases: list[dict]) -> list[dict]:
    """Apply HLS_GT_NAMES / HLS_GT_MODE selection (#662)."""
    names_raw = (os.environ.get("HLS_GT_NAMES") or "").strip()
    if names_raw:
        want = {n.strip() for n in names_raw.split(",") if n.strip()}
        selected = [c for c in cases if c.get("name") in want]
        missing = want - {c.get("name") for c in selected}
        if missing:
            pytest.fail(f"HLS_GT_NAMES unknown case(s): {sorted(missing)}")
        return selected

    mode = (os.environ.get("HLS_GT_MODE") or "all").strip().lower()
    if mode in ("", "all"):
        return cases
    if mode == "core":
        return [c for c in cases if c.get("tier", "expand") == "core"]
    if mode == "rotate":
        core = [c for c in cases if c.get("tier", "expand") == "core"]
        expand = [c for c in cases if c.get("tier", "expand") != "core"]
        # Alternate expand half by ISO week so weekly CI still samples all families
        # over ~2 weeks without doubling every Monday's wall clock.
        week = date.today().isocalendar().week
        half = [c for i, c in enumerate(expand) if i % 2 == (week % 2)]
        return core + half
    pytest.fail(f"Unknown HLS_GT_MODE={mode!r} (use all|core|rotate)")


def _load_cases() -> list[dict]:
    return _select_cases(_load_all_cases())


@pytest.mark.integration
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
        assert not outcome.had_error, (
            f"{case['name']}: scrape had_error=True with {len(outcome.records)} partial "
            f"record(s) — portal failure, not a volume miss"
        )
        records = outcome.records
        count = len(records)
    else:
        pytest.fail(f"Unknown ground-truth method: {method}")

    assert min_n <= count <= max_n, (
        f"{case['name']} (family={case.get('family', '?')}): "
        f"expected {min_n}–{max_n} records, got {count}"
    )


def test_ground_truth_yaml_covers_adapter_families():
    """#662: yaml must name the inventory adapter families (no live network)."""
    cases = _load_all_cases()
    families = {c.get("family") for c in cases if c.get("family")}
    required = {
        "bloom",
        "pdf",
        "housekeys",
        "midpen",
        "john_stewart",
        "charities_housing",
        "first_housing",
        "eden",
        "eah",
        "gis",
        "alta",
    }
    missing = required - families
    assert not missing, f"ground_truth.yaml missing adapter families: {sorted(missing)}"
    assert len(cases) >= 12, f"expected expanded ground_truth set, got {len(cases)}"
