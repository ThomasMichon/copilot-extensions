"""Reuse explicitly admitted in-process projection cases; CLI cases stay development-only."""

import pytest

from bundle_input_cases import TestBundleInputs as _InputCases


class TestBundleInputs(_InputCases):
    @pytest.fixture(autouse=True)
    def synthetic_marker_defaults(self, monkeypatch, windows_target):
        monkeypatch.setattr("packaging.markers.default_environment", lambda: dict(windows_target))
