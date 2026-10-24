"""Synthetic Linux/WSL native hold + original hook + resident write qualification."""

from __future__ import annotations

from pathlib import Path
import subprocess
import sys
import textwrap

import pytest

from agent_worktrees import native_source_context


@pytest.mark.parametrize("transport", ["resident", "registration-unavailable", "observation-unavailable"])
def test_original_hook_and_real_backend_join_through_resident(tmp_path, transport):
    try:
        native_source_context.epoch()
    except (OSError, ValueError, IndexError):
        pytest.skip("Native original-source evidence is qualified only on non-container Linux/WSL")
    hooks = Path(__file__).resolve().parents[2] / "scripts" / "hook_client.py"
    backend = tmp_path / "backend.py"
    backend.write_text(textwrap.dedent("""
        import importlib.util, json, os, subprocess, sys
        from pathlib import Path
        from agent_worktrees import config as cfg, locks, native_lifecycle_source, sessions, tracking, tracking_write
        from agent_worktrees import launch_execution, launch_native_observation
        root, hooks = Path(sys.argv[1]), sys.argv[2]
        sid = "00000000-0000-4000-8000-000000000001"
        cfg.set_active_project("demo")
        sessions._session_state_dir = lambda: root / "sessions"
        directory = sessions._session_state_dir() / sid
        directory.mkdir(parents=True)
        endpoint = json.loads(os.environ["FIXTURE_ENDPOINT"])
        locks.read_lock = lambda *a: endpoint
        tracking_write.run_direct = lambda *a, **kw: sys.exit("unexpected backend direct fallback")
        hook_code = (
            "import importlib.util,json,sys\\n"
            "spec=importlib.util.spec_from_file_location('original_hook',sys.argv[1])\\n"
            "module=importlib.util.module_from_spec(spec)\\n"
            "spec.loader.exec_module(module)\\n"
            "value=module._enrich_session_payload({'sessionId':sys.argv[2]})\\n"
            "print(json.dumps(value))\\n"
        )
        with (directory / ("inuse." + str(os.getpid()) + ".hold")).open("w"):
            result = subprocess.run(
                [sys.executable, "-c", hook_code, hooks, sid],
                capture_output=True, text=True, timeout=15,
            )
            assert result.returncode == 0, result.stderr
            payload = json.loads(result.stdout)
            witness = payload["_agentWorktrees"]["nativeSource"]
            token = native_lifecycle_source.start_identity(sid, witness)
            assert token
            if sys.argv[3] == "registration-unavailable":
                locks.read_lock = lambda *a: None
            tracking.register_session("wt-a", sid, source_identity=token)
            if sys.argv[3] == "observation-unavailable":
                locks.read_lock = lambda *a: None
            record = cfg.tracking_dir() / "wt-a.yaml"
            launch_native_observation.observe_registration(
                record, sid, token, payload=payload, witness=witness, resident=True,
            )
            reference = json.loads(os.environ[launch_execution.NATIVE_HINT_ENV])
            observation = launch_native_observation.read(
                record.parent, reference["attempt_id"], reference["operation_id"],
            )
            assert observation["native_pid"] == os.getpid()
        print("original-source-observed")
    """), encoding="utf-8")
    driver = textwrap.dedent("""
        import json, os, sys
        from pathlib import Path
        from agent_worktrees import config as cfg, config_authority, launch_execution
        from agent_worktrees import launch_seed_exec, launch_seed_state, launch_native_observation
        from agent_worktrees import sessions, tracking, tracking_write
        root = Path(sys.argv[1])
        project = cfg.project_dir("demo")
        project.mkdir(parents=True, exist_ok=True)
        (root / ".agent-worktrees").mkdir()
        (root / ".agent-worktrees" / "machines.yaml").write_text("machines: {}\\n")
        (project / "config.yaml").write_text(json.dumps({
            "repo_name": "demo", "machine": "test",
            "repos": {"demo": {"anchor": str(root), "default_branch": "dev"}},
        }))
        cfg.set_active_project("demo")
        config = cfg.Config(
            srcroot=str(root), machine="test", platform=cfg.detect_platform(), repo_name="demo",
            repos={"demo": cfg.RepoConfig(anchor=str(root), worktree_root=str(root / "trees"))},
        )
        config_authority.load_project_authority_config = lambda *a, **kw: config
        cfg.load_project_config = lambda *a, **kw: config
        sessions._session_state_dir = lambda: root / "sessions"
        checkout = root / "checkout"
        checkout.mkdir()
        record = tracking.create_new_record(
            "wt-a", "worktree/wt-a", str(checkout), "demo", "test",
            cfg.detect_platform(), cfg.tracking_dir(),
        ).yaml_path
        server = tracking_write.start_server(tracking_write.compute)
        server.start()
        endpoint = tracking_write.rendezvous_fields(server)
        os.environ["FIXTURE_ENDPOINT"] = json.dumps(endpoint)
        def dispatch(verb, args, **kw):
            return tracking_write.dispatch(
                verb, args, read_lock_data=lambda: endpoint, ensure_monitor=None,
                boot_wait_s=0, request_deadline_s=2, **kw,
            )
        launch_seed_state._dispatch = dispatch
        tracking_write.run_direct = lambda *a, **kw: sys.exit("unexpected direct fallback")
        correlation = launch_execution.Correlation("a" * 32, "b" * 32)
        try:
            result = launch_seed_exec.launch(
                record, [sys.executable, sys.argv[2], str(root), sys.argv[3], sys.argv[4]],
                invoke=True, correlation=correlation,
            )
            assert result == (0 if sys.argv[4] == "resident" else 1)
            file, = (record.parent / "launch-attempts" / correlation.attempt_id).glob("*.execution.json")
            result = launch_native_observation.joined(record.parent, correlation.attempt_id, file.name.split(".")[0])
            if sys.argv[4] == "resident":
                assert result["status"] == "native-session-observed"
                assert result["prompt_acceptance"] == "unknown"
            else:
                assert result == {"status": "unknown", "reason": "native-observation-missing"}
        finally:
            server.close()
        print("native-observation-join-pass")
    """)
    result = subprocess.run(
        [sys.executable, "-c", driver, str(tmp_path), str(backend), str(hooks), transport],
        capture_output=True, text=True, timeout=40,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    expected = ["native-observation-join-pass"]
    if transport == "resident":
        expected.insert(0, "original-source-observed")
    else:
        assert "unexpected backend direct fallback" in result.stderr
    assert result.stdout.splitlines() == expected
