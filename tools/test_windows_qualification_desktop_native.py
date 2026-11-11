"""Explicit hosted-only desktop calibration, not an ordinary tool/unit test."""

import json
import os
from pathlib import Path
import platform

import pytest

from windows_qualification_desktop import capture, require_hosted_owner, validate_evidence


@pytest.mark.timeout(50)
@pytest.mark.portfolio_tier("T3")
@pytest.mark.effect("filesystem")
@pytest.mark.effect("process")
@pytest.mark.effect("host-state")
def test_hosted_desktop_capability(record_testsuite_property):
    try:
        require_hosted_owner(os.environ, system=platform.system(), contained=True)
    except ValueError as error:
        pytest.skip(str(error))
    root = Path(__file__).resolve().parents[1]
    nonce = os.environ["WINDOWS_QUALIFICATION_DESKTOP_REQUEST"]
    report = capture(root, Path(os.environ["WINDOWS_QUALIFICATION_DESKTOP_OUTPUT"]), nonce)
    record_testsuite_property("desktop_capability", json.dumps({
        key: report.get(key) for key in
        ("outcome", "visible_consent_consumed", "cleanup_confirmed", "error")
    }, sort_keys=True))
    assert report["cleanup_confirmed"], "desktop calibration cleanup is unconfirmed"
    if report["outcome"] == "unsupported":
        pytest.skip("disposable hosted desktop unavailable; see capability evidence")
    validate_evidence(report, nonce)
