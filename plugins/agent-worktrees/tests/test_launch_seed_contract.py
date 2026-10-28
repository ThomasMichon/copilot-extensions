from __future__ import annotations

import json
from pathlib import Path
import subprocess

import pytest

from agent_worktrees import launch_seed_contract as contract


@pytest.mark.parametrize("fault", [
    None, "old", "full-environment-only", "malformed", "failed", "selector-changed", "custody-unknown",
])
def test_discovery_queries_only_the_selected_completed_runtime_without_admission(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fault: str | None,
) -> None:
    root = tmp_path / "runtime"
    slot = root / "versions" / "selected"
    python = slot / "bin" / "python"
    bootstrap = slot / ".runtime-bootstrap" / "runtime_launcher.py"
    python.parent.mkdir(parents=True)
    bootstrap.parent.mkdir()
    python.touch()
    bootstrap.touch()
    (slot / ".install-complete.json").touch()
    selector = root / "current-version"
    selector.write_text("selected\n", encoding="utf-8")
    descriptor = {
        "contract": "agent-worktrees.native-seed", "version": 1,
        "preparation_output_formats": ["json", "nul"],
        "nul_reply_schema": "agent-worktrees.native-seed-reply.v1",
        "setup_process_start_time_sources": ["caller", "provider"],
        "backend_execution_modes": ["posix-exec-v1"] if fault == "old" else ["posix-exec-env-v1"],
        "preparation_environment_modes": (
            ["full"] if fault == "full-environment-only" else ["full", "execution-hint"]
        ),
    }
    queries = []

    def query(command: list[str], **kwargs) -> subprocess.CompletedProcess:
        queries.append(command)
        assert command == [
            str(python), "-I", "-S", "-B", "-X", "utf8", str(bootstrap),
            "-m", "agent_worktrees.launch_seed_native", "--describe-contract",
        ]
        assert kwargs["timeout"] == 10
        assert "PYTHONPATH" not in kwargs["env"]
        if fault == "custody-unknown":
            raise RuntimeError("The caller cannot establish child custody")
        if fault == "selector-changed":
            selector.write_text("replacement", encoding="utf-8")
        return subprocess.CompletedProcess(
            command, 3 if fault == "failed" else 0,
            "not JSON" if fault == "malformed" else json.dumps(descriptor), "",
        )

    monkeypatch.setattr(contract, "run_caller_owned", query)
    assert contract.supports_posix_setup(root) is (fault is None)
    assert len(queries) == 1
    assert not list(root.rglob("*.execution.json"))


def test_discovery_does_not_run_an_incomplete_slot(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "runtime"
    root.mkdir()
    (root / "current-version").write_text("../foreign", encoding="utf-8")
    monkeypatch.setattr(contract, "run_caller_owned", lambda *_a, **_k: pytest.fail("Unsafe runtime"))
    assert not contract.supports_posix_setup(root)
