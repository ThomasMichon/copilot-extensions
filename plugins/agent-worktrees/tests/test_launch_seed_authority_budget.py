"""Receiving-side seed transactions must not discover unrelated PR policies."""

from __future__ import annotations

import json
import subprocess
import sys
import textwrap

import pytest


@pytest.mark.guard
@pytest.mark.parametrize("kind", ["new", "resume"])
def test_seed_daemon_transactions_exclude_pr_discovery(tmp_path, kind):
    script = textwrap.dedent("""
        import json
        import sys
        import time
        from pathlib import Path
        from agent_worktrees import activity, config as cfg, launch_seed_state as state, tracking, tracking_write

        root = Path(sys.argv[1])
        kind = sys.argv[2]
        project = cfg.project_dir("demo")
        project.mkdir(parents=True, exist_ok=True)
        (project / "config.yaml").write_text(json.dumps({
            "repo_name": "demo", "machine": "test",
            "repos": {"demo": {"anchor": str(root), "default_branch": "dev"}},
        }), encoding="utf-8")
        cfg.set_active_project("demo")
        discovery = []
        def forbidden_discovery():
            discovery.append(True)
            raise AssertionError("mutation authority entered unrelated PR discovery")
        cfg._control_plane_related_pr_map = forbidden_discovery
        path = tracking.create_new_record(
            "wt-a", "worktree/wt-a", str(root / "checkout"), "demo", "test",
            cfg.detect_platform(), root,
        ).yaml_path
        server = tracking_write.start_server(tracking_write.compute)
        server.start()
        endpoint = tracking_write.rendezvous_fields(server)
        def dispatch(verb, args, **kwargs):
            return tracking_write.dispatch(
                verb, args, read_lock_data=lambda: endpoint,
                ensure_monitor=None, boot_wait_s=0, request_deadline_s=2,
            )
        state._dispatch = dispatch
        def no_fallback(*args, **kwargs):
            raise AssertionError("real daemon transaction fell back to direct mutation")
        tracking_write.run_direct = no_fallback
        started = time.monotonic()
        try:
            seed = state.stage(path, kind=kind, text="quoted ' intent\\nsecond line")
            first = state.take(path, seed_id=seed.seed_id)
            assert state.restore(path, first)["restored"]
            second = state.take(path, seed_id=seed.seed_id)
            assert state.finish(path, second)["finished"]
            assert state.peek(path) is None
            assert not discovery, discovery
            events = [
                json.loads(line) for line in activity.log_path().read_text(encoding="utf-8").splitlines()
            ]
            stages = [event for event in events if event.get("event") == "launch_preparation"]
            for verb in ("stage", "take", "restore", "finish"):
                matching = [
                    event for event in stages if event["phase"] == "Executing launch_seed_" + verb
                ]
                assert matching and matching[0]["state"] == "started", matching
                assert matching[-1]["state"] == "finished", matching
                assert all(event["worktree_id"] == "wt-a" for event in matching)
            assert "quoted ' intent" not in activity.log_path().read_text(encoding="utf-8")
            print(json.dumps({"kind": seed.kind, "elapsed": time.monotonic() - started}))
        finally:
            server.close()
    """)
    result = subprocess.run(
        [sys.executable, "-c", script, str(tmp_path), kind],
        capture_output=True, text=True, timeout=20, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    evidence = json.loads(result.stdout)
    assert evidence["kind"] == kind
    assert evidence["elapsed"] < 8
