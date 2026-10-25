"""Explicit source process-boundary qualification, not installed acceptance."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import textwrap
import time

import pytest

from worktree_manager.launch_attempts import LaunchAttempts


@pytest.mark.parametrize("include_input", [False, True])
@pytest.mark.parametrize("summary", [False, True])
def test_actual_manager_to_selected_engine_reads_without_mutation(tmp_path, monkeypatch, include_input, summary):
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
    if summary:
        assert engine[1:] == ["-m", "agent_worktrees"], "Summary qualification requires a prepared Python engine argv"
        # Put execution and the independently joined native observation beyond page one.
        for index in range(100):
            identity = f"{index:032x}"
            events = [
                {"at": "2026-10-24T00:00:00+00:00", "phase": "resolution-requested", "details": {}},
            ]
            if index == 0:
                events.append({
                    "at": "2026-10-24T00:00:01+00:00", "phase": "unknown",
                    "details": {"exit_code": 2},
                })
            (folder / (identity + ".json")).write_text(json.dumps({
                "schema": "worktree-launch-resolution", "version": 1,
                "attempt_id": attempt, "operation_id": identity, "events": events,
            }), encoding="utf-8")
        epoch = {
            "platform": "linux", "environment_id": "e" * 32,
            "boot_id": "00000000-0000-4000-8000-000000000001",
        }
        resolution = "c" * 32
        session = "00000000-0000-4000-8000-000000000002"
        (folder / (operation + ".execution.json")).write_text(json.dumps({
            "schema": "worktree-launch-execution", "version": 2,
            "attempt_id": attempt, "operation_id": operation, "resolution_id": resolution,
            "parent_id": "f" * 32, "worktree_id": "example-worktree", "creation_nonce": "nonce",
            "native_epoch": epoch, "events": [
                {"at": "2026-10-24T00:00:00+00:00", "phase": "execution-requested", "details": {}},
                {"at": "2026-10-24T00:00:01+00:00", "phase": "process-spawned", "details": {
                    "pid": 42, "process_start_time": "100", "boundary": "backend-command",
                }},
                {"at": "2026-10-24T00:00:03+00:00", "phase": "execution-returned", "details": {
                    "exit_code": 3, "process_exit_code": 0, "receipt_uncertain": False,
                }},
            ],
        }), encoding="utf-8")
        (folder / ("f" * 32 + ".execution.json")).write_text(json.dumps({
            "schema": "worktree-launch-execution", "version": 1,
            "attempt_id": attempt, "operation_id": "f" * 32, "resolution_id": resolution,
            "parent_id": None, "worktree_id": "example-worktree", "creation_nonce": "nonce",
            "events": [
                {"at": "2026-10-24T00:00:00+00:00", "phase": "execution-requested", "details": {}},
                {"at": "2026-10-24T00:00:01+00:00", "phase": "process-spawned", "details": {
                    "pid": 41, "process_start_time": None, "boundary": "launcher-command",
                }},
                {"at": "2026-10-24T00:00:04+00:00", "phase": "execution-returned", "details": {
                    "exit_code": 0, "process_exit_code": 0, "receipt_uncertain": False,
                }},
            ],
        }), encoding="utf-8")
        fault_script = textwrap.dedent("""
            import json, os, subprocess, sys
            from pathlib import Path
            from types import SimpleNamespace
            from agent_worktrees import launch_execution as execution, launch_seed_state
            directory, attempt, resolution = sys.argv[1:]
            execution.tracking.load_record = lambda path: SimpleNamespace(
                worktree_id="example-worktree", creation_nonce="nonce")
            execution.uuid4 = lambda: SimpleNamespace(hex="e" * 32)
            execution._epoch = lambda: None
            def dispatch(verb, args, **kwargs):
                assert verb == "launch_attempt_execution"
                if args["phase"] == "seed-claimed":
                    raise OSError("synthetic earlier receipt failure")
                return execution._apply(args)
            launch_seed_state._dispatch = dispatch
            def work(owner):
                owner.observed("seed-claimed", seed_id="1" * 32, handoff_id="2" * 32)
                flags = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
                child = subprocess.Popen([sys.executable, "-c", "pass"], **flags)
                try:
                    owner.spawned(child.pid, invoke=True)
                finally:
                    owner.process_exit_code = child.wait(timeout=15)
                return owner.process_exit_code
            result = execution.run(Path(directory) / "example-worktree.json",
                                   execution.Correlation(attempt, resolution), work)
            print(json.dumps({"actual_wrapper_exit": result}))
        """)
        fault_result = subprocess.run(
            [*engine[:1], "-c", fault_script, str(folder.parent.parent), attempt, resolution],
            cwd=anchor, env=environment, capture_output=True, text=True,
            check=True, timeout=30, **flags,
        )
        assert json.loads(fault_result.stdout) == {"actual_wrapper_exit": 3}
        fault_receipt = json.loads((folder / ("e" * 32 + ".execution.json")).read_text(encoding="utf-8"))
        assert fault_receipt["events"][-1]["details"] == {
            "exit_code": 0, "process_exit_code": 0, "receipt_uncertain": True,
        }
        # Derive the source identity with the actual selected engine, not a copied algorithm.
        identity_script = (
            "import json; from agent_worktrees import native_source_context as n; "
            f"print(n.identity({session!r},42,'100',{epoch!r}))"
        )
        identity_result = subprocess.run(
            [*engine[:1], "-c", identity_script], cwd=anchor, env=environment,
            capture_output=True, text=True, check=True, timeout=30, **flags,
        )
        (folder / (operation + ".native.json")).write_text(json.dumps({
            "schema": "worktree-launch-native-observation", "version": 1,
            "attempt_id": attempt, "operation_id": operation, "resolution_id": resolution,
            "worktree_id": "example-worktree", "creation_nonce": "nonce",
            "native_session_id": session, "native_pid": 42, "native_birth": "100",
            "native_epoch": epoch, "source_identity": identity_result.stdout.strip(),
            "ancestors": [{"pid": 42, "start_time": "100"}], "at": "2026-10-24T00:00:02+00:00",
        }), encoding="utf-8")
    store = LaunchAttempts(tmp_path / "manager")
    prompt = "  synthetic private fixture\nexact input "
    store.record_intent(
        attempt, action="new", project="example", surface="native-reader",
        is_local=True, prompt=prompt, options={"no_mux": True},
    )
    store.append(attempt, owner="manager", phase="launcher-returned", exit_code=0)
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
    started = time.monotonic()
    result = subprocess.run(
        [sys.executable, "-c", script, str(tmp_path / "manager"), raw,
         "launch-attempts", attempt, "--json",
         *(["--summary"] if summary else ["--event-limit", "1"]),
         *(["--include-input"] if include_input else [])],
        cwd=anchor, env=environment, capture_output=True, text=True, timeout=150, **flags,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    elapsed = time.monotonic() - started
    report = json.loads(result.stdout)
    assert report["engine"]["status"] == "read"
    if summary:
        assert report["engine"]["pages_read"] == 2
        assert report["engine"]["receipts_read"] == 105
        assert report["summary"]["boundaries"]["allocation"]["status"] == "unknown"
        assert report["summary"]["boundaries"]["backend_start"]["evidence"][0]["operation_id"] == operation
        assert report["summary"]["boundaries"]["native_session"]["evidence"][0]["native_session_id"] == session
        assert report["summary"]["observation"] == "non-atomic"
        assert report["summary"]["errors"] == []
        owners = report["summary"]["owner_observations"]
        assert len(owners["resolution"]) == 101
        assert owners["resolution"][0]["terminal_phase"] == "unknown"
        assert owners["resolution"][0]["recorded_exit_code"] == 2
        assert owners["resolution"][0]["wrapper_exit_code"] is None
        assert owners["resolution"][-1]["terminal_phase"] is None
        assert len(owners["execution"]) == 3
        assert owners["execution"][0]["parent_id"] == "f" * 32
        assert owners["execution"][0]["recorded_exit_code"] == 3
        assert owners["execution"][0]["wrapper_exit_code"] is None
        assert owners["execution"][0]["process_exit_code"] == 0
        assert owners["execution"][0]["receipt_uncertain"] is False
        assert owners["execution"][1]["recorded_exit_code"] == 0
        assert owners["execution"][1]["process_exit_code"] == 0
        assert owners["execution"][1]["receipt_uncertain"] is True
        assert owners["execution"][1]["wrapper_exit_code"] is None
        assert owners["execution"][1]["parent_id"] is None
        assert owners["execution"][2]["recorded_exit_code"] == 0
        assert owners["execution"][2]["receipt_uncertain"] is False
        assert owners["execution"][2]["wrapper_exit_code"] is None
        assert owners["admission"] == []
        print(f"summary source read: receipts=105 pages=2 elapsed_seconds={elapsed:.6f}")
    else:
        assert report["engine"]["receipts"][0]["operation_id"] == operation
        assert report["engine"]["receipts"][0]["evidence"]["events"][0]["phase"] == "resolution-requested"
    assert report["prompt_acceptance"] == "unknown"
    assert report["attempt"]["events"][0]["phase"] == "confirmed"
    assert report["attempt"]["events_has_more"] is (not summary)
    assert report["attempt"]["manager_outcome"] == "returned"
    assert report["attempt"]["manager_outcome_scope"] == "manager-boundary"
    assert report["attempt"]["last_manager_event"]["phase"] == "launcher-returned"
    if include_input:
        assert report["attempt"]["input"]["prompt"] == prompt
    else:
        assert "synthetic private fixture" not in result.stdout
    after = {p.relative_to(tmp_path): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    assert before == after
