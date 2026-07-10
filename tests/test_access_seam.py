"""Access seam: production code must not bypass access.py (#1060)."""

from __future__ import annotations

import ast
from pathlib import Path

import housing_list_search.access as access

PACKAGE = Path(__file__).resolve().parents[1] / "housing_list_search"

# Implementation modules of the Access seam — may import each other.
_IMPL = frozenset({"access.py", "scraper.py", "playwright_nav.py"})


def _banned_imports(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    bad: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            if node.module in (
                "housing_list_search.scraper",
                "housing_list_search.playwright_nav",
            ):
                bad.append(f"{path.name}:{node.lineno} from {node.module}")
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name in (
                    "housing_list_search.scraper",
                    "housing_list_search.playwright_nav",
                ):
                    bad.append(f"{path.name}:{node.lineno} import {alias.name}")
    return bad


def test_production_modules_use_access_not_impl():
    """Adapters / extraction / pipeline must import access, not scraper or playwright_nav."""
    violations: list[str] = []
    for path in PACKAGE.rglob("*.py"):
        if path.name in _IMPL:
            continue
        violations.extend(_banned_imports(path))
    assert not violations, "bypass Access seam:\n" + "\n".join(violations)


def test_access_exports_http_and_browser():
    for name in (
        "polite_get",
        "polite_post",
        "require_response",
        "SourceFetchError",
        "URLPolicyError",
        "validate_http_url",
        "browser_page",
        "safe_goto",
        "shutdown_playwright",
    ):
        assert hasattr(access, name), f"access missing {name}"
