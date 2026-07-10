"""doctor.check_marker_ocr_safety must not crash when marker is absent (CI)."""

from __future__ import annotations

import importlib.util

import scripts.doctor as doctor


def test_marker_check_survives_missing_package(monkeypatch, capsys):
    """find_spec('marker.converters.pdf') raises ModuleNotFoundError without parent —
    check must use top-level name and return True (warn-only)."""

    real_find_spec = importlib.util.find_spec

    def fake_find_spec(name, package=None):
        if name == "marker" or name.startswith("marker."):
            if name != "marker":
                raise ModuleNotFoundError("No module named 'marker'")
            return None
        return real_find_spec(name, package)

    monkeypatch.setattr(doctor, "find_spec", fake_find_spec)
    monkeypatch.delenv("HLS_DISABLE_MARKER_PDF", raising=False)
    monkeypatch.delenv("HLS_ENABLE_MARKER_PDF", raising=False)

    assert doctor.check_marker_ocr_safety() is True
    out = capsys.readouterr().out
    assert "not in this env" in out


def test_marker_check_dotted_path_would_have_crashed(monkeypatch):
    """Document the CI footgun: dotted find_spec raises when parent missing."""

    def boom(name, package=None):
        if "." in name and name.startswith("marker"):
            raise ModuleNotFoundError("No module named 'marker'")
        return None

    monkeypatch.setattr(doctor, "find_spec", boom)
    # Our check only calls find_spec("marker") — must not raise
    assert doctor.check_marker_ocr_safety() is True
