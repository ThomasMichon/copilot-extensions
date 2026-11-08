"""Repair children retain the caller's redirected native standard streams."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from worktree_manager import update_bootstrap


@pytest.mark.timeout(30)
@pytest.mark.skipif(
    os.name != "nt" or os.environ.get("COPILOT_EXTENSIONS_ALLOW_EXPLICIT_TEST_TIERS") != "1",
    reason="opt-in original-budget native Windows Manager queue qualification",
)
def test_native_queued_manager_preserves_original_cohort_budget(tmp_path, tmp_path_factory, monkeypatch):
    import update_driver
    from agent_procutil.owned_process import run_owned
    from worktree_manager import engine_client

    home = tmp_path_factory.mktemp("ud") / "home"
    home.mkdir()
    events = tmp_path / "events.jsonl"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("UPDATE_DRIVER_TEST_EVENTS", str(events))
    accepted = ["synthetic-accepted-provider"]
    monkeypatch.setenv(engine_client.ENGINE_ARGV_ENV, json.dumps(accepted))
    fixture = Path(__file__).parent / "fixtures" / "update_driver_cli.py"
    for override in ("synthetic-first-provider", "synthetic-second-provider", None):
        if override is None:
            monkeypatch.delenv(engine_client.ENGINE_CMD_ENV)
        else:
            monkeypatch.setenv(engine_client.ENGINE_CMD_ENV, override)
        result = run_owned([sys.executable, str(fixture), "submit-only"], timeout=20)
        assert result.returncode == 0, result.stderr
    root = home / ".copilot-extensions" / "update-driver"
    assert len(update_driver.status(root=root)) == 3
    monkeypatch.setenv(engine_client.ENGINE_CMD_ENV, "wrong-worker-provider")
    result = run_owned([sys.executable, "-m", "update_driver", "worker"], timeout=20)
    assert result.returncode == 0, result.stderr
    records = [json.loads(line) for line in events.read_text().splitlines()]
    assert [record["provider"] for record in records if record["phase"] == "start"] == [
        ["synthetic-first-provider"], ["synthetic-second-provider"], accepted,
    ]


@pytest.mark.parametrize("interrupted", [False, True])
def test_repair_wait_forwards_streams_until_child_settles(monkeypatch, tmp_path, interrupted):
    seen, waits = [], []

    def wait():
        waits.append(True)
        if interrupted and len(waits) == 1:
            raise KeyboardInterrupt
        return 17

    class Child:
        def __enter__(self):
            return SimpleNamespace(wait=wait)

        def __exit__(self, *args):
            pass

    def launch(command, **kwargs):
        seen.append((command, kwargs))
        return Child()

    monkeypatch.setattr(update_bootstrap.subprocess, "Popen", launch)
    command, environment = ["fixture-python", "child.py"], {"FIXTURE": "value"}
    if interrupted:
        with pytest.raises(KeyboardInterrupt):
            update_bootstrap._wait(command, environment, tmp_path)
        assert len(waits) == 2
    else:
        assert update_bootstrap._wait(command, environment, tmp_path) == 17
    assert seen == [(command, {
        "env": environment, "cwd": tmp_path,
        "stdin": sys.stdin, "stdout": sys.stdout, "stderr": sys.stderr,
        **update_bootstrap.no_window_kwargs(),
    })]


@pytest.mark.skipif(
    os.name != "nt" or os.environ.get("COPILOT_EXTENSIONS_ALLOW_EXPLICIT_TEST_TIERS") != "1",
    reason="opt-in real native Windows redirected-stream boundary",
)
@pytest.mark.parametrize("code", [0, 17])
@pytest.mark.parametrize("input_text", ["repair input \u00e9", None])
def test_native_repair_child_streams_reach_outer_capture(tmp_path, code, input_text):
    from agent_procutil.owned_process import run_owned

    child = tmp_path / "repair-child.py"
    child.write_text(
        "import json,sys\n"
        "print(json.dumps({'stdin':sys.stdin.read()},ensure_ascii=False),flush=True)\n"
        "print('repair diagnostic \\u00e9',file=sys.stderr,flush=True)\n"
        f"raise SystemExit({code})\n",
        encoding="utf-8",
    )
    base = getattr(sys, "_base_executable", sys.executable)
    command = [base, "-I", "-S", "-B", "-X", "utf8", str(child)]
    outer = (
        "import json,os,pathlib,sys;"
        "from worktree_manager.update_bootstrap import _wait;"
        "raise SystemExit(_wait(json.loads(sys.argv[1]),dict(os.environ),pathlib.Path(sys.argv[2])))"
    )
    for _cycle in range(2):
        result = run_owned(
            [sys.executable, "-B", "-X", "utf8", "-c", outer,
             json.dumps(command), str(tmp_path)],
            timeout=20, input_text=input_text,
        )
        assert result.returncode == code, result.stderr
        assert json.loads(result.stdout) == {"stdin": input_text or ""}
        assert result.stderr == "repair diagnostic \u00e9\n"
