"""Exercise the actual payload entry with an isolated, credential-free SDK."""

import json
from pathlib import Path
import shutil
import subprocess
from typing import Any

from agent_procutil import no_window_kwargs
import pytest

from budget_guidance.cli import main


PLUGIN = Path(__file__).resolve().parents[1]


def _marker(result: subprocess.CompletedProcess[str], marker: str) -> dict[str, Any]:
    matches = [line.split(marker, 1)[1] for line in result.stdout.splitlines() if marker in line]
    assert len(matches) == 1, (
        f"expected exactly one {marker} marker, got {len(matches)}\n"
        f"{result.stdout}\n{result.stderr}"
    )
    value: dict[str, Any] = json.loads(matches[0])
    return value


@pytest.mark.parametrize("output", ["", "MARKER={}\nMARKER={}\n"])
def test_host_quota_marker_diagnoses_missing_or_duplicate_output(output: str) -> None:
    result = subprocess.CompletedProcess([], 0, output, "synthetic-stderr")
    with pytest.raises(AssertionError, match="expected exactly one MARKER="):
        _marker(result, "MARKER=")


@pytest.mark.parametrize("layout", ["source", "packaged"])
def test_host_quota_entry_and_boundaries(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], layout: str,
) -> None:
    node = shutil.which("node")
    assert node is not None, "Node.js is required for the extension contract tests"
    selected = PLUGIN
    if layout == "packaged":
        selected = tmp_path / "fixture"
        shutil.copytree(PLUGIN / "extensions", selected / "extensions")
        shutil.copyfile(
            PLUGIN.parents[1] / "libs" / "copilot-driver-core" / "src"
            / "agent_remote_driver" / "bridge_extension" / "sdk-compat.mjs",
            selected / "extensions" / "budget-guidance" / "sdk-compat.mjs",
        )
        (selected / "tests" / "fixtures").mkdir(parents=True)
        for relative in ("tests/host-quota.test.mjs", "tests/fixtures/host-sdk.mjs"):
            shutil.copyfile(PLUGIN / relative, selected / relative)
    result = subprocess.run(
        [node, "--test", str(selected / "tests" / "host-quota.test.mjs")],
        cwd=selected, text=True, capture_output=True, timeout=30, **no_window_kwargs(),
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
