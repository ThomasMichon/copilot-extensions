"""Explicit source process-boundary qualification, not installed acceptance."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import textwrap

import pytest

from worktree_manager.launch_attempts import LaunchAttempts


@pytest.mark.parametrize("include_input", [False, True])
def test_actual_manager_to_selected_engine_reads_without_mutation(tmp_path, monkeypatch, include_input):
    raw = os.environ.get("WORKTREE_MANAGER_TEST_ENGINE_ARGV")
    if not raw:
        pytest.skip("Explicit prepared engine argv required for cross-runtime source qualification")
    engine = json.loads(raw)
    assert isinstance(engine, list) and engine and all(isinstance(part, str) for part in engine)
    assert Path(engine[0]).is_absolute() and Path(engine[0]).is_file()
    redirected_git = tmp_path / "unselected.git"
    monkeypatch.setenv("GIT_DIR", str(redirected_git))
    monkeypatch.setenv("GIT_WORK_TREE", str(tmp_path / "unselected-worktree"))
    monkeypatch.setenv("GIT_INDEX_FILE", str(tmp_path / "unselected-index"))
    environment = {
        key: value for key, value in os.environ.items()
        if not key.upper().startswith(("AGENT_", "COPILOT_", "WORKTREE_", "PYTHON", "GIT_"))
    }
    environment.update({
        "AGENT_HOME": str(tmp_path), "HOME": str(tmp_path), "USERPROFILE": str(tmp_path),
        "PYTHONUTF8": "1",
    })
    flags = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
    anchor = tmp_path / "anchor"
    anchor.mkdir()
    subprocess.run(
        ["git", "init", "--quiet", str(anchor)],
        env=environment, check=True, timeout=15, **flags,
    )
    assert (anchor / ".git").is_dir()
    assert not redirected_git.exists()
    project = tmp_path / ".example"
    project.mkdir()
    (project / "config.yaml").write_text(json.dumps({
        "repo_name": "example", "machine": "fixture-host",
        "repos": {"example": {"anchor": str(anchor), "default_branch": "dev"}},
    }), encoding="utf-8")
    attempt, operation = "a" * 32, "b" * 32
    folder = project / "worktrees" / "launch-attempts" / attempt
    folder.mkdir(parents=True)
    (folder / (operation + ".json")).write_text(json.dumps({
        "schema": "worktree-launch-resolution", "version": 1,
        "attempt_id": attempt, "operation_id": operation, "events": [
            {"at": "2026-10-24T00:00:00+00:00", "phase": "resolution-requested", "details": {}},
        ],
    }), encoding="utf-8")
    store = LaunchAttempts(tmp_path / "manager")
    prompt = "  synthetic private fixture\nexact input "
    store.record_intent(
        attempt, action="new", project="example", surface="native-reader",
        is_local=True, prompt=prompt, options={"no_mux": True},
    )
    before = {p.relative_to(tmp_path): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    script = textwrap.dedent("""
        import json, sys
        from pathlib import Path
        from worktree_manager import __main__ as cli, engine_client, launch_attempts
        root = Path(sys.argv[1])
        launch_attempts.default_root = lambda: root
        engine_client.set_engine_command(json.loads(sys.argv[2]))
        sys.argv = [sys.argv[0], *sys.argv[3:]]
        cli.console_entry()
    """)
    result = subprocess.run(
        [sys.executable, "-c", script, str(tmp_path / "manager"), raw,
         "launch-attempts", attempt, "--json", *(["--include-input"] if include_input else [])],
        cwd=anchor, env=environment, capture_output=True, text=True, timeout=150, **flags,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads(result.stdout)
    assert report["engine"]["status"] == "read"
    assert report["engine"]["receipts"][0]["operation_id"] == operation
    assert report["engine"]["receipts"][0]["evidence"]["events"][0]["phase"] == "resolution-requested"
    assert report["prompt_acceptance"] == "unknown"
    if include_input:
        assert report["attempt"]["input"]["prompt"] == prompt
    else:
        assert "synthetic private fixture" not in result.stdout
    after = {p.relative_to(tmp_path): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    assert before == after
