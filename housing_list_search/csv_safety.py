"""
CSV formula-injection guards for export paths.

Spreadsheet tools (Excel, LibreOffice, Google Sheets) may interpret cell values
starting with =, +, -, @, tab, or carriage return as formulas. Prefix those
values with a single quote so imports stay literal.
"""

from __future__ import annotations

from typing import Any, Mapping

# OWASP CSV injection guidance — leading characters that trigger formula parsing.
_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def sanitize_csv_field(value: Any) -> Any:
    """Escape a single CSV cell value when it could be interpreted as a formula."""
    if value is None:
        return ""
    if not isinstance(value, str):
        return value
    if not value:
        return value
    if value[0] in _FORMULA_PREFIXES:
        return "'" + value
    return value


def sanitize_csv_row(row: Mapping[str, Any]) -> dict[str, Any]:
    """Return a copy of row with string fields sanitized for CSV export."""
    return {key: sanitize_csv_field(val) for key, val in row.items()}