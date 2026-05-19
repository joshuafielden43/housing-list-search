#!/usr/bin/env python3
"""
Doctor script for Housing List Search.

Run this to verify your environment is ready to use the tool.

Usage:
    python scripts/doctor.py
    ./scripts/doctor.py

Intended to be run by humans and by Hermes-style agents after
cloning or when things feel broken.
"""

import sys
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
        from housing_list_search.registry import load_targets_to_db
        load_targets_to_db()  # This reloads from TARGETS.md into SQLite
        print("✅ Registry loads TARGETS.md successfully")
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


def main():
    print("🏥 Housing List Search — Environment Doctor")
    print("   (Run this after cloning or when things feel broken)")

    results = []

    section("Python & Dependencies")
    results.append(check_python_version())
    results.append(check_requirements())

    section("Package Import Health")
    results.append(check_package_imports())

    section("Configuration")
    results.append(check_targets_file())
    results.append(check_registry_load())

    section("Optional but Recommended")
    results.append(check_playwright())

    section("Summary")

    if all(results):
        print("✅ All critical checks passed. Your environment looks healthy.")
        print("   You should be able to run: python main.py --run")
        sys.exit(0)
    else:
        print("❌ One or more checks failed. Fix the issues above and re-run this doctor.")
        sys.exit(1)


if __name__ == "__main__":
    main()
