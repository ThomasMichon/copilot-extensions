"""Exercise the actual payload entry with an isolated, credential-free SDK."""

import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

from budget_guidance.cli import main


PLUGIN = Path(__file__).resolve().parents[1]


def _marker(result, marker):
    matches = [line.split(marker, 1)[1] for line in result.stdout.splitlines() if marker in line]
    assert len(matches) == 1, (
        f"expected exactly one {marker} marker, got {len(matches)}\n"
        f"{result.stdout}\n{result.stderr}"
    )
    return json.loads(matches[0])


@pytest.mark.parametrize("output", ["", "MARKER={}\nMARKER={}\n"])
def test_host_quota_marker_diagnoses_missing_or_duplicate_output(output):
    result = subprocess.CompletedProcess([], 0, output, "synthetic-stderr")
    with pytest.raises(AssertionError, match="expected exactly one MARKER="):
        _marker(result, "MARKER=")


def test_host_quota_entry_and_boundaries(tmp_path, capsys):
    node = shutil.which("node")
    assert node is not None, "Node.js is required for the extension contract tests"
    # This test process runs only under the repository's bounded test runner.
    result = subprocess.run(
        [node, "--test", str(PLUGIN / "tests" / "host-quota.test.mjs")],
        cwd=PLUGIN, text=True, capture_output=True, timeout=30,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    capture = _marker(result, "ENTRY_CAPTURE=")
    capture_path = tmp_path / "capture.json"
    capture_path.write_text(json.dumps(capture), encoding="utf-8")
    assert main([
        "quota", "--capture", str(capture_path), "--session-id", "session-example", "--json",
    ]) == 0
    reading = json.loads(capsys.readouterr().out)
    assert reading["attribution"] == "caller-declared-capture"
    assert reading["availability"] == "cached"
    assert reading["freshness"] == "unknown"
    assert reading["allowance"] == "1000"
    assert reading["calculated"] is None
    assert reading["advice"]["concurrency"] is None
    rejected = _marker(result, "ENTRY_STRING_RESULT=")
    assert rejected["capture"] is None
    assert rejected["diagnostics"] == ["quota-projection-invalid"]
    capture["projection"]["snapshots"]["premium_interactions"]["entitlementRequests"] = "1000"
    capture_path.write_text(json.dumps(capture), encoding="utf-8")
    assert main([
        "quota", "--capture", str(capture_path), "--session-id", "session-example", "--json",
    ]) == 2
    error = json.loads(capsys.readouterr().out)
    assert error["error"] == "quota.entitlementRequests must be a finite number"
    provider_capture = _marker(result, "PROVIDER_CAPTURE=")
    capture_path.write_text(json.dumps(provider_capture), encoding="utf-8")
    provider_args = [
        "quota", "--capture", str(capture_path), "--session-id", "session-example",
        "--provider-id", "provider-example", "--quota-id", "quota-example", "--json",
    ]
    assert main(provider_args) == 1
    provider_reading = json.loads(capsys.readouterr().out)
    assert provider_reading["schema"] == "copilot-extensions.session-quota-reading"
    assert provider_reading["availability"] == "unavailable"
    assert "provider-observation-after-acquisition" in provider_reading["diagnostics"]
    assert provider_reading["provider_evidence"]["observation_kind"] == "account_snapshot"
    assert provider_reading["calculated"] is None
    provider_capture["projection"]["providerQuotas"][0]["observationKind"] = "unknown"
    capture_path.write_text(json.dumps(provider_capture), encoding="utf-8")
    assert main(provider_args) == 2
    assert json.loads(capsys.readouterr().out)["error"] == "provider observationKind is unsupported"
