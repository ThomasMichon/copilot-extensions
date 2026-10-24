"""Explicit process/IPC qualification; excluded from the default unit portfolio."""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
import time

import pytest

from agent_worktrees import launch_attempt as attempts


def test_actual_daemon_owns_receipt_writes_without_direct_fallback(tmp_path):
    script = textwrap.dedent("""
        import argparse, json, sys
        from pathlib import Path
        from agent_worktrees import config as cfg, launch_attempt, launch_seed_state, output, tracking_write
        root = Path(sys.argv[1])
        cfg.tracking_dir = lambda: root
        server = tracking_write.start_server(tracking_write.compute)
        server.start()
        endpoint = tracking_write.rendezvous_fields(server)
        def dispatch(verb, args, **kwargs):
            return tracking_write.dispatch(
                verb, args, read_lock_data=lambda: endpoint, ensure_monitor=None,
                boot_wait_s=0, request_deadline_s=2, **kwargs,
            )
        launch_seed_state._dispatch = dispatch
        tracking_write.run_direct = lambda *a, **kw: sys.exit("unexpected direct fallback")
        def work():
            output._json_output({"action": "exec", "worktree_id": "wt-a"})
            return 0
        try:
            with output.capture_json_output() as captured:
                result = launch_attempt.resolve(argparse.Namespace(launch_attempt_id="a" * 32), work)
            plan = json.loads(captured.getvalue())
            saved = launch_attempt.read(root, "a" * 32, plan["launch_attempt_receipt"]["operation_id"])
            assert result == 0 and saved["events"][-1]["phase"] == "plan-ready"
            print("daemon-receipt-pass")
        finally:
            server.close()
    """)
    result = subprocess.run(
        [sys.executable, "-c", script, str(tmp_path)],
        capture_output=True, text=True, timeout=20,
        **({"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip() == "daemon-receipt-pass"


def test_process_loss_after_receipt_does_not_manufacture_a_terminal_outcome(tmp_path):
    script = textwrap.dedent("""
        import argparse, sys, time
        from pathlib import Path
        from agent_worktrees import config as cfg, launch_attempt, launch_seed_state
        root = Path(sys.argv[1])
        cfg.tracking_dir = lambda: root
        launch_seed_state._dispatch = lambda verb, args, **kwargs: launch_attempt._apply(args)
        def work():
            (root / "entered").write_text("entered", encoding="utf-8")
            time.sleep(60)
        launch_attempt.resolve(argparse.Namespace(launch_attempt_id="a" * 32), work)
    """)
    process = subprocess.Popen(
        [sys.executable, "-c", script, str(tmp_path)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        **({"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}),
    )
    try:
        deadline = time.monotonic() + 15
        while not (tmp_path / "entered").exists() and time.monotonic() < deadline:
            if process.poll() is not None:
                pytest.fail(str(process.communicate()))
            time.sleep(0.02)
        assert (tmp_path / "entered").exists()
    finally:
        process.kill()
        process.communicate(timeout=5)
    path = next((tmp_path / "launch-attempts" / ("a" * 32)).glob("*.json"))
    saved = attempts.read(tmp_path, "a" * 32, path.stem)
    assert [event["phase"] for event in saved["events"]] == ["resolution-requested"]
