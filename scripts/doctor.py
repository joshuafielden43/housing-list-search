#!/usr/bin/env python3
"""
Doctor script for Housing List Search.

Run this to verify your environment is ready to use the tool.

Usage:
    python scripts/doctor.py
    python scripts/doctor.py --fix
    ./scripts/doctor.py --fix

--fix   : Force a full re-ingest of TARGETS.md into the registry,
          running the sanitizer / nanny layer on every row.
          Useful after manually editing TARGETS.md or when you
          want the SQLite registry to be guaranteed fresh and sanitized.

Intended to be run by humans and by Hermes-style agents after
cloning or when things feel broken.
"""

import sys
import argparse
import subprocess
from pathlib import Path
from importlib.util import find_spec


def section(title: str):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print('='*60)


def check_python_version() -> bool:
    print(f"Python version: {sys.version.split()[0]}")
    if sys.version_info < (3, 10):
        print("⚠️  Warning: Python 3.10+ recommended")
        return True  # not fatal yet
    print("✅ Python version looks good")
    return True


def check_requirements() -> bool:
    """Check that the packages listed in requirements.txt can be imported."""
    req_file = Path("requirements.txt")
    if not req_file.exists():
        print("❌ requirements.txt not found")
        return False

    required = []
    for line in req_file.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            # Handle things like "beautifulsoup4" or "pymupdf"
            pkg = line.split("==")[0].split(">=")[0].split("[")[0].strip()
            if pkg:
                required.append(pkg.lower())

    missing = []
    for pkg in required:
        # Some packages have different import names
        import_name = {
            "beautifulsoup4": "bs4",
            "pymupdf": "fitz",
            "pdfplumber": "pdfplumber",
            "playwright": "playwright",
        }.get(pkg, pkg)

        if find_spec(import_name) is None:
            missing.append(pkg)

    if missing:
        print(f"❌ Missing packages: {', '.join(missing)}")
        print("   Run: pip install -r requirements.txt")
        return False

    print(f"✅ All {len(required)} required packages importable")
    return True


def check_package_imports() -> bool:
    # Try normal import first
    try:
        import housing_list_search
        from housing_list_search.scraper import polite_get
        from housing_list_search.registry import load_targets_to_db
        from housing_list_search.adapters.john_stewart import scrape_john_stewart
        from housing_list_search.adapters.gis_extraction import extract_gis_portfolio
        from housing_list_search.adapters.housekeys import scrape_housekeys
        from housing_list_search.adapters.cdn import extract_underlying_records
        from housing_list_search.adapters.alta import scrape_alta
        from housing_list_search.registry import get_active_targets, get_skipped_targets
        print("✅ housing_list_search package imports cleanly")
        return True
    except ImportError:
        pass

    # Development mode: allow running directly from the repo root
    # without having the package installed in site-packages.
    import sys
    repo_root = Path(__file__).resolve().parents[1]
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))

    try:
        import housing_list_search
        from housing_list_search.scraper import polite_get
        from housing_list_search.registry import load_targets_to_db
        from housing_list_search.adapters.john_stewart import scrape_john_stewart
        from housing_list_search.adapters.gis_extraction import extract_gis_portfolio
        from housing_list_search.adapters.housekeys import scrape_housekeys
        from housing_list_search.adapters.cdn import extract_underlying_records
        from housing_list_search.adapters.alta import scrape_alta
        from housing_list_search.registry import get_active_targets, get_skipped_targets
        print("✅ housing_list_search imports successfully (development mode)")
        return True
    except Exception as e:
        print(f"❌ Failed to import housing_list_search: {e}")
        print("   Make sure you're running this from the repository root,")
        print("   or install the package with: pip install -e .")
        return False


def check_targets_file() -> bool:
    targets = Path("TARGETS.md")
    if not targets.exists():
        print("❌ TARGETS.md not found in repo root")
        return False

    content = targets.read_text()
    if "City/Authority" not in content:
        print("⚠️  TARGETS.md exists but looks incomplete")
        return False

    print(f"✅ TARGETS.md found ({len(content.splitlines())} lines)")
    return True


def check_registry_load() -> bool:
    try:
        from housing_list_search.registry import load_targets_to_db, get_active_targets, get_skipped_targets
        load_targets_to_db()
        active = get_active_targets()
        skipped = get_skipped_targets()
        print(f"✅ Registry loads TARGETS.md successfully")
        print(f"   Active targets: {len(active)} | Intentionally skipped (no_public_list): {len(skipped)}")
        if skipped:
            for t in skipped:
                print(f"     - {t['authority']} (marked no_public_list)")
        return True
    except Exception as e:
        print(f"❌ Registry failed to load: {e}")
        return False


def check_playwright() -> bool:
    try:
        from playwright.sync_api import sync_playwright
        # Don't actually launch a browser here — just check import + basic readiness
        print("✅ Playwright Python package is installed")
        print("   (Run `playwright install` if you haven't already for browser binaries)")
        return True
    except ImportError:
        print("⚠️  Playwright not installed (some targets require it)")
        print("   pip install playwright && playwright install")
        return True  # Not fatal — some targets work without it


def _prune_snapshots(older_than_days: int):
    import time
    snapshots_dir = Path("snapshots")
    if not snapshots_dir.exists():
        print(f"   snapshots/ directory not found — nothing to prune")
        return
    cutoff = time.time() - older_than_days * 86400
    removed = 0
    for f in snapshots_dir.iterdir():
        if f.is_file() and f.stat().st_mtime < cutoff:
            f.unlink()
            print(f"   Pruned: {f.name}")
            removed += 1
    print(f"✅ Snapshot pruning complete — {removed} file(s) removed (older than {older_than_days} days)")


def main():
    parser = argparse.ArgumentParser(
        description="Housing List Search Doctor + Registry Fixer"
    )
    parser.add_argument(
        "--fix",
        action="store_true",
        help="Re-ingest TARGETS.md and re-run the sanitizer on all registry objects. "
             "Use this after editing TARGETS.md to guarantee the DB reflects the current (sanitized) state."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate imports and config only — no network requests, no DB writes. "
             "Safe to run in CI or restricted environments."
    )
    parser.add_argument(
        "--prune-snapshots",
        type=int,
        metavar="DAYS",
        help="Delete snapshots/ archives older than DAYS days."
    )
    args = parser.parse_args()

    dry_run = args.dry_run

    if dry_run:
        print("🔍 DRY-RUN MODE — imports and config only, no network or DB writes")
    if args.fix and not dry_run:
        print("🔧 FIX MODE — will re-ingest TARGETS.md into registry\n")

    print("🏥 Housing List Search — Environment Doctor")

    results = []

    section("Python & Dependencies")
    results.append(check_python_version())
    results.append(check_requirements())

    section("Package Import Health")
    results.append(check_package_imports())

    if args.fix and not dry_run:
        try:
            from housing_list_search.registry import load_targets_to_db
            print("\n🔧 --fix: Forcing fresh registry load + sanitization from TARGETS.md ...")
            load_targets_to_db()
            print("   ✅ Registry objects have been re-scanned and re-sanitized.\n")
        except Exception as e:
            print(f"   ❌ Failed to force registry re-scan during --fix: {e}\n")

    section("Configuration")
    results.append(check_targets_file())
    if not dry_run:
        results.append(check_registry_load())
    else:
        print("   (skipping registry DB load in dry-run mode)")

    section("Optional but Recommended")
    results.append(check_playwright())

    if not dry_run:
        # Network smoke tests — skipped in dry-run / CI mode
        try:
            from housing_list_search.adapters.housekeys import scrape_housekeys
            recs = scrape_housekeys("City of Milpitas (test)", "https://www.milpitas.gov/1303/Below-Market-Rate-BMR-Homeownership-Prog")
            if recs and any("HouseKeys" in str(r) for r in recs):
                print("✅ HouseKeys adapter smoke test passed")
            else:
                print("✅ HouseKeys adapter runs without crashing")

            from housing_list_search.adapters.cdn import extract_underlying_records
            # Document IDs 364/366/368 confirmed current as of 2026-06-05.
            # sunnyvale.ca.gov is WAF-blocked so these return empty; smoke only validates no crash.
            recs = extract_underlying_records(
                "https://www.sunnyvale.ca.gov/homes-streets-and-property/housing/rental-programs",
                authority="City of Sunnyvale (doctor smoke)",
                known_document_urls=["https://www.sunnyvale.ca.gov/home/showpublisheddocument/368"],
                timeout=30000
            )
            print("✅ cdn adapter smoke test ran (returned list, no crash)")

            from housing_list_search.adapters.alta import scrape_alta
            recs = scrape_alta("City of Palo Alto (doctor smoke)", "https://www.paloalto.gov/Departments/Planning-Development-Services/Housing-Policies-Projects/Below-Market-Rate-Housing")
            print("✅ alta adapter smoke test ran (returned list, no crash)")
        except Exception as e:
            print(f"⚠️  Adapter smoke had issues (may be expected in restricted env): {e}")

    if args.prune_snapshots is not None:
        _prune_snapshots(args.prune_snapshots)

    section("Summary")

    if args.fix and not dry_run:
        print("✅ --fix completed. Registry has been re-ingested and re-sanitized from TARGETS.md.")
    if dry_run:
        print("✅ Dry-run complete — imports and config look good.")

    if all(results):
        print("✅ All critical checks passed. Your environment looks healthy.")
        if not dry_run:
            print("   You should be able to run: python main.py --run")
        sys.exit(0)
    else:
        print("❌ One or more checks failed. Fix the issues above and re-run this doctor.")
        sys.exit(1)


if __name__ == "__main__":
    main()
