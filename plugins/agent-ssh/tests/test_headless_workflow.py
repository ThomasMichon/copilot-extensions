"""The native headless lane is explicit, source-bound and credential-free."""

from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[3]


def _workflow():
    return yaml.safe_load(
        (ROOT / ".github/workflows/installation-context-acceptance.yml").read_text(),
    )


def test_headless_lane_requires_explicit_manual_selection():
    workflow = _workflow()
    trigger = workflow.get("on", workflow.get(True))
    assert set(trigger) == {"workflow_dispatch"}
    selection = trigger["workflow_dispatch"]["inputs"]["ssh_headless"]
    assert selection["type"] == "boolean" and selection["default"] is False
    job = workflow["jobs"]["ssh-headless-windows"]
    assert job["if"] == "${{ inputs.ssh_headless }}"
    assert job["runs-on"] == "windows-11-arm"
    assert job["strategy"]["matrix"]["python_arch"] == ["arm64", "x64"]
    assert workflow["permissions"] == {"contents": "read"}
    for name in ("derived-diagnostics-posix", "derived-diagnostics-windows"):
        assert "!inputs.ssh_headless" in workflow["jobs"][name]["if"]


def test_headless_lane_pins_source_client_bytes_and_signature():
    steps = _workflow()["jobs"]["ssh-headless-windows"]["steps"]
    checkout = steps[0]
    assert checkout["with"]["ref"] == "${{ github.sha }}"
    assert checkout["with"]["persist-credentials"] is False
    preparation = next(step["run"] for step in steps if "pinned native ARM64" in step.get("name", ""))
    assert "698c6aec31c1dd0fb996206e8741f4531a97355686b5431ef347d531b07fcd42" in preparation
    assert "fd87ccdfbd8be33d22b67fa3f1c94bb2327a3e7d24355bf7fd2485d356f1976d" in preparation
    assert preparation.index("Get-FileHash") < preparation.index("ExtractToDirectory")
    assert "Get-AuthenticodeSignature" in preparation
    assert "Microsoft Corporation" in preparation
    assert "msiexec" not in preparation.casefold()


def test_native_lane_uses_exact_setup_interpreter_and_containment():
    job = _workflow()["jobs"]["ssh-headless-windows"]
    assert job["env"]["SSH_MANAGER_WINDOWS_JUMP_TEST"] == "1"
    setup = next(step for step in job["steps"] if step.get("id") == "python")
    assert setup["with"] == {
        "python-version": "3.13", "architecture": "${{ matrix.python_arch }}",
    }
    qualification = next(
        step for step in job["steps"] if "two parent shapes" in step.get("name", "")
    )
    assert qualification["env"]["UV_PYTHON"] == "${{ steps.python.outputs.python-path }}"
    assert "tools/run-plugin-tests.py agent-ssh --allow-explicit-tiers" in qualification["run"]
    assert "--test-file tests/test_headless_probe_windows.py" in qualification["run"]
