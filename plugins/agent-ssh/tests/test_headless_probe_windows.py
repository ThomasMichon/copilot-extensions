"""Explicit disposable-runner proof; never launch native SSH on a shared host."""

from __future__ import annotations

import json
import os
import platform
import subprocess
import sys
import sysconfig
from pathlib import Path

import pytest
from agent_procutil import no_window_kwargs, windowless_python


@pytest.mark.skipif(
    sys.platform != "win32" or os.environ.get("SSH_MANAGER_WINDOWS_JUMP_TEST") != "1",
    reason="explicit hosted Windows ARM64 headless-probe qualification",
)
@pytest.mark.parametrize("parent", ["console", "gui"])
def test_selected_client_jump_chain_is_headless_and_reaped(tmp_path, capsys, parent):
    assert os.environ.get("GITHUB_ACTIONS") == "true"
    assert os.environ.get("RUNNER_ENVIRONMENT") == "github-hosted"
    assert os.environ.get("RUNNER_ARCH", "").casefold() == "arm64"
    selected = Path(os.environ["SSH_MANAGER_SELECTED_CLIENT"])
    assert selected.is_file(), "The pinned selected native client must be present"
    fixture = (
        Path(__file__).resolve().parents[3] / "libs" / "ssh-manager"
        / "tests" / "fixtures" / "windows_jump_probe.py"
    )
    result_file = tmp_path / "native-result.json"
    home = tmp_path / "home"
    home.mkdir()
    python = windowless_python(require_windowless=True) if parent == "gui" else sys.executable
    result = subprocess.run(
        [python, str(fixture), str(tmp_path), str(result_file), sysconfig.get_path("purelib")],
        env={**os.environ, "HOME": str(home), "USERPROFILE": str(home)},
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
        timeout=30, check=False, **no_window_kwargs(),
    )
    assert result_file.is_file(), result.stderr.decode(errors="replace")
    evidence = json.loads(result_file.read_text(encoding="utf-8"))
    with capsys.disabled():
        print(json.dumps({"parent": parent, "native_evidence": evidence}), flush=True)
    assert result.returncode == 0 and evidence["ok"], evidence
    assert evidence["parent_console_handle"] == 0
    assert evidence["parent_python_version"] == platform.python_version()
    assert evidence["observer_control_detected_and_retired"] is True
    canary = evidence["observer_canary"]
    assert canary["windows"] and all(
        window["pid"] == canary["process"] for window in canary["windows"]
    )
    assert evidence["cached_profile_refresh"] == {
        "same_file": True, "source_cache_unchanged": True,
        "cached_hostname": "cached-old.invalid", "refreshed_hostname": "cached-new.invalid",
        "refreshed_jump": "cache-first.invalid,cache-last.invalid", "metadata_queries": 5,
        "programmatic_proxy_preserved": True, "cli_proxy_override_preserved": True,
        "programmatic_proxy_option_preserved": True,
    }
    expected_arch = os.environ["SSH_MANAGER_EXPECTED_PYTHON_ARCH"]
    assert evidence["parent_platform"] == {
        "arm64": "win-arm64", "x64": "win-amd64",
    }[expected_arch]
    assert len(evidence["cycles"]) == 2
    assert {cycle["kind"] for cycle in evidence["cycles"]} == {"timeout", "cancel"}
    assert all(cycle["ssh_processes"] == 3 for cycle in evidence["cycles"])
    assert all(cycle["metadata_queries"] == 4 for cycle in evidence["cycles"])
    assert sum(cycle["version_queries"] for cycle in evidence["cycles"]) <= 1
    assert all(cycle["cleanup_confirmed"] for cycle in evidence["cycles"])
    assert all(not any(cycle["console_handles"]) for cycle in evidence["cycles"])
    assert all(not cycle["inherited_descriptor_state"] for cycle in evidence["cycles"])
    assert evidence["new_visible_terminal_windows"] == 0
    assert evidence["terminal_took_foreground"] is False
