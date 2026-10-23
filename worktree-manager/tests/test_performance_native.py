"""Explicit source-process consumer proof, not a headed or installed budget test."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tomllib
import time

import pytest

pytestmark = [
    pytest.mark.portfolio_tier("T2"),
    pytest.mark.effect("process"),
    pytest.mark.effect("filesystem"),
    pytest.mark.timeout(90),
]

def probe_module():
    import importlib.util

    root = Path(__file__).resolve().parents[2]
    spec = importlib.util.spec_from_file_location(
        "manager_probe_contract", root / "tools/performance/manager_native_probe.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_signal_readback_cannot_claim_a_signal_the_sender_did_not_request(tmp_path):
    from types import SimpleNamespace
    probe = probe_module()
    args = SimpleNamespace(
        endpoint=None, logs_endpoint=None, metrics_endpoint=None,
        logs_receiver_jsonl=str(tmp_path / "logs.jsonl"), metrics_receiver_jsonl=None,
    )
    with pytest.raises(ValueError, match="configured sender endpoint"):
        probe.run(args)


@pytest.mark.parametrize("bad_row", [
    None, [], 7, True, "text", "deep-json",
    {"schema": "manager.signal-batch.v1", "trace_id": "a" * 32, "signals": ["traces", "logs", "metrics"], "metrics": []},
    {"schema": "manager.signal-batch.v1", "trace_id": "a" * 32,
     "signals": {name: {"status": "not_requested"} for name in ("traces", "logs", "metrics")},
     "metrics": []},
    {"schema": "manager.signal-batch.v1", "trace_id": "a" * 32,
     "signals": {name: {"status": "not_requested"} for name in ("traces", "logs", "metrics")},
     "metrics": [None]},
])
def test_malformed_sender_rows_preserve_both_samples_and_final_report(tmp_path, monkeypatch, bad_row):
    from types import SimpleNamespace
    module = probe_module()
    monkeypatch.setattr(module, "source_identity", lambda root: ("a" * 40, True))

    def child(argv, **kwargs):
        config = json.loads(Path(argv[-1]).read_text())
        payload = bad_row
        if type(bad_row) is dict and bad_row.get("metrics") == [None]:
            from dataclasses import asdict
            from process_telemetry.signals import SignalResult
            payload = {**bad_row, "signals": {
                name: asdict(SignalResult("not_requested")) for name in ("traces", "logs", "metrics")
            }}
        raw = "[" * 1100 + "0" + "]" * 1100 if bad_row == "deep-json" else json.dumps(payload)
        Path(config["events"]).write_text(raw + "\n", encoding="utf-8")
        result = {
            "mode": config["mode"], "outcome": "ok",
            "points_ns": {key: 0 for key in ("source_entry", "first_refresh", "input_ack", "fixture_rows")},
            "telemetry": {
                "trace_id": "a" * 32, "counts": {"queued": 0, "written": 0},
                "sampled": True, "writer_started": True, "writer_alive": False,
            },
        }
        return subprocess.CompletedProcess(argv, 0, "MANAGER_OBSERVATION " + json.dumps(result), "")

    monkeypatch.setattr(module, "captured_child", child)
    args = SimpleNamespace(
        run_dir=str(tmp_path / "result"), endpoint=None, samples=1, timeout=1,
        receiver_jsonl=None, readback_timeout=0,
    )
    report = module.run(args)
    assert (Path(args.run_dir) / "report.json").is_file()
    for mode in ("enabled", "disabled"):
        assert report["modes"][mode]["completed"] == 1
        assert report["modes"][mode]["samples"][0]["event_file_invalid"]
    assert report["modes"]["enabled"]["telemetry_incomplete"] == 1


def test_key_receipt_excludes_later_handler_settling():
    import asyncio
    from types import SimpleNamespace

    module = probe_module()
    clock = [200]
    pending = {"sent": 100, "received": None, "handled": False}
    async def original(app, event):
        clock[0] = 1_000_000
        await asyncio.sleep(0)
    wrapped = module.key_receipt_observer(
        original, SimpleNamespace, pending, clock=lambda: clock[0],
    )
    asyncio.run(wrapped(None, SimpleNamespace(key="x")))
    assert pending["received"] == 200 and pending["handled"]
    assert pending["received"] - pending["sent"] == 100


def test_posix_capture_keeps_startup_stderr(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from worktree_manager import engine_posix_process

    module = probe_module()
    monkeypatch.setattr(module, "sys", SimpleNamespace(platform="linux"))
    def failed(argv, **kwargs):
        kwargs["stderr"].write(b"synthetic startup traceback\n")
        return subprocess.CompletedProcess(argv, 1, "", None)
    monkeypatch.setattr(engine_posix_process, "run", failed)
    result = module.captured_child(["sample"], cwd=tmp_path, env={}, timeout=1)
    assert result.returncode == 1
    assert result.stderr == "synthetic startup traceback\n"


@pytest.mark.parametrize("failure", ["RuntimeError", "UnicodeDecodeError"])
def test_job_admission_failure_keeps_prior_samples_and_final_report(tmp_path, monkeypatch, failure):
    from types import SimpleNamespace

    module = probe_module()
    monkeypatch.setattr(module, "source_identity", lambda _: ("a" * 40, True))
    def capture(argv, *, cwd, **kwargs):
        if cwd.name.endswith("enabled"):
            if failure == "UnicodeDecodeError":
                raise UnicodeDecodeError("utf-8", b"\xff", 0, 1, "synthetic")
            raise RuntimeError("synthetic denied Job")
        value = {
            "mode": "disabled", "outcome": "ok",
            "points_ns": {"source_entry": 0, "first_refresh": 1, "input_ack": 1, "fixture_rows": 2},
        }
        return subprocess.CompletedProcess(argv, 0, "MANAGER_OBSERVATION " + json.dumps(value), "")
    monkeypatch.setattr(module, "captured_child", capture)
    root = tmp_path / "failed-run"
    report = module.run(SimpleNamespace(
        run_dir=str(root), endpoint=None, samples=1, timeout=1,
        receiver_jsonl=None, readback_timeout=0,
    ))
    saved = json.loads((root / "report.json").read_text())
    assert saved == report
    assert saved["modes"]["disabled"]["completed"] == 1
    assert saved["modes"]["disabled"]["failed"] == 0
    assert saved["modes"]["enabled"]["completed"] == saved["modes"]["enabled"]["failed"] == 1
    assert saved["modes"]["enabled"]["samples"][0]["error_code"] == failure


def test_bad_child_duration_still_saves_complete_run_accounting(tmp_path, monkeypatch):
    from types import SimpleNamespace

    module = probe_module()
    monkeypatch.setattr(module, "source_identity", lambda _: ("a" * 40, True))
    def capture(argv, *, cwd, **kwargs):
        mode = "enabled" if cwd.name.endswith("enabled") else "disabled"
        value = {
            "mode": mode, "outcome": "ok",
            "points_ns": {"source_entry": 0, "first_refresh": "invalid" if mode == "enabled" else 1,
                          "input_ack": 1, "fixture_rows": 2},
        }
        return subprocess.CompletedProcess(argv, 0, "MANAGER_OBSERVATION " + json.dumps(value), "")
    monkeypatch.setattr(module, "captured_child", capture)
    root = tmp_path / "bad-duration-run"
    module.run(SimpleNamespace(
        run_dir=str(root), endpoint=None, samples=1, timeout=1,
        receiver_jsonl=None, readback_timeout=0,
    ))
    saved = json.loads((root / "report.json").read_text())
    assert saved["modes"]["disabled"]["completed"] == 1
    assert saved["modes"]["disabled"]["failed"] == 0
    assert saved["modes"]["enabled"]["completed"] == saved["modes"]["enabled"]["failed"] == 1
    assert saved["modes"]["enabled"]["samples"][0]["error_code"] == "invalid_child_report"


def test_receiver_decode_failure_preserves_collected_samples(tmp_path, monkeypatch):
    from types import SimpleNamespace

    module = probe_module()
    monkeypatch.setattr(module, "source_identity", lambda _: ("a" * 40, True))
    def capture(argv, *, cwd, **kwargs):
        value = {
            "mode": "enabled" if cwd.name.endswith("enabled") else "disabled", "outcome": "ok",
            "points_ns": {"source_entry": 0, "first_refresh": 1, "input_ack": 1, "fixture_rows": 2},
        }
        return subprocess.CompletedProcess(argv, 0, "MANAGER_OBSERVATION " + json.dumps(value), "")
    monkeypatch.setattr(module, "captured_child", capture)
    receiver = tmp_path / "invalid-receiver.jsonl"
    receiver.write_bytes(b"\xff\n")
    root = tmp_path / "receiver-failed-run"
    module.run(SimpleNamespace(
        run_dir=str(root), endpoint=None, samples=1, timeout=1,
        receiver_jsonl=str(receiver), readback_timeout=0,
    ))
    saved = json.loads((root / "report.json").read_text())
    assert saved["modes"]["disabled"]["completed"] == saved["modes"]["enabled"]["completed"] == 1
    assert not saved["readback"]["complete"]
    assert saved["readback"]["reason"] == "UnicodeDecodeError"


def test_git_overrides_cannot_redirect_source_identity(tmp_path, monkeypatch):
    from agent_procutil import no_window_kwargs

    module = probe_module()
    root = Path(__file__).resolve().parents[2]
    expected = module.source_identity(root)
    other = tmp_path / "other"
    other.mkdir()
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    subprocess.run(["git", "init", "-q", str(other)], env=env, check=True, timeout=15, **no_window_kwargs())
    subprocess.run(
        ["git", "-C", str(other), "-c", "user.name=Sample", "-c", "user.email=sample@example.com",
         "-c", "commit.gpgsign=false", "-c", f"core.hooksPath={tmp_path / 'empty-hooks'}",
         "commit", "--allow-empty", "-qm", "fixture"],
        env=env, check=True, timeout=15, **no_window_kwargs(),
    )
    monkeypatch.setenv("GIT_DIR", str(other / ".git"))
    monkeypatch.setenv("GIT_WORK_TREE", str(other))
    monkeypatch.setenv("GIT_INDEX_FILE", str(other / ".git/index"))
    assert module.source_identity(root) == expected


def test_real_source_picker_observation_processes(tmp_path):
    root = Path(__file__).resolve().parents[2]
    destination = tmp_path / "source-run"
    result = subprocess.run(
        [
            sys.executable, str(root / "tools/performance/manager_native_probe.py"),
            "--run-dir", str(destination), "--samples", "1", "--timeout", "30",
        ],
        cwd=root, capture_output=True, text=True, timeout=85,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads((destination / "report.json").read_text())
    assert report["qualification"] == "not-qualified-for-headed-or-installed-budgets"
    assert report["unavailable"]["authoritative_rows"] == "fixture roster is not production authority"
    for mode in ("disabled", "enabled"):
        evidence = report["modes"][mode]
        assert evidence["expected"] == evidence["completed"] == 1
        assert evidence["failed"] == evidence["drops"] == evidence["telemetry_incomplete"] == 0
        run = evidence["samples"][0]
        assert run["exit_code"] == 0 and run["serving_source_matches"]
        assert run["blocked_child_attempts"] == 0
        assert run["points_ns"]["first_refresh"] > 0
        assert run["points_ns"]["fixture_rows"] >= run["points_ns"]["first_refresh"]
        assert run["points_ns"]["input_ack"] > 0
        assert run["process_duration_ns"] > run["points_ns"]["first_refresh"]
        assert evidence["points"]["first_refresh"]["p99_ns"] is None
    enabled = report["modes"]["enabled"]["samples"][0]
    assert enabled["sender_records"] == 5
    assert set(enabled["sender_stage_counts"]) == {
        "source_entry", "first_refresh", "input_ack", "fixture_rows", "ui_stop",
    }
    assert enabled["telemetry"]["counts"]["queued"] == enabled["telemetry"]["counts"]["written"] == 5
    assert report["modes"]["disabled"]["samples"][0]["telemetry"] is None


def test_telemetry_dependency_materializes_without_the_authoring_tree(tmp_path):
    from worktree_manager.self_install_payload import _materialize_payload_pointers

    root = Path(__file__).resolve().parents[2]
    metadata = tomllib.loads((root / "worktree-manager/pyproject.toml").read_text())
    declaration = metadata["tool"]["uv"]["sources"]["agent-process-telemetry"]
    source = tmp_path / "authoring" / "worktree-manager"
    source.mkdir(parents=True)
    shutil.copytree(root / "libs/process-telemetry", source.parent / "libs/process-telemetry",
                    ignore=shutil.ignore_patterns("__pycache__", "*.egg-info", ".ruff_cache", "build"))
    (source / "pyproject.toml").write_text(
        '[project]\nname="sample-manager"\nversion="1.0"\n'
        '[tool.uv.sources]\nagent-process-telemetry = { path = "'
        + declaration["path"] + '", editable = true }\n',
        encoding="utf-8",
    )
    slot = tmp_path / "standalone"
    shutil.copytree(source, slot)
    _materialize_payload_pointers(source, slot)
    rewritten = tomllib.loads((slot / "pyproject.toml").read_text())
    assert rewritten["tool"]["uv"]["sources"]["agent-process-telemetry"] == {
        "path": "libs/process-telemetry",
    }
    shutil.move(str(source.parent), str(tmp_path / "authoring-unavailable"))
    result = subprocess.run(
        [sys.executable, "-I", "-S", "-c",
         "import sys;sys.path.insert(0,sys.argv[1]);"
         "from process_telemetry import SinkSlot;assert not SinkSlot().enabled();"
         "assert 'opentelemetry' not in sys.modules",
         str(slot / "libs/process-telemetry/src")],
        capture_output=True, text=True, timeout=15,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.skipif(sys.platform != "win32", reason="Windows Job lifetime control")
def test_probe_timeout_reaps_its_owned_descendant(tmp_path):
    import importlib.util
    from mux_windows_observer import processes

    root = Path(__file__).resolve().parents[2]
    spec = importlib.util.spec_from_file_location(
        "manager_probe_timeout", root / "tools/performance/manager_native_probe.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    receipt = tmp_path / "child.json"
    script = (
        "import json,pathlib,subprocess,sys,time;"
        "from agent_procutil import no_window_kwargs;"
        "p=subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)'],**no_window_kwargs());"
        "pathlib.Path(sys.argv[1]).write_text(json.dumps({'pid':p.pid}));time.sleep(30)"
    )
    with pytest.raises(subprocess.TimeoutExpired) as failure:
        module.captured_child(
            [sys.executable, "-c", script, str(receipt)],
            cwd=tmp_path, env=dict(os.environ), timeout=10,
        )
    assert receipt.exists(), failure.value.stderr
    pid = json.loads(receipt.read_text())["pid"]
    deadline = time.monotonic() + 3
    while pid in processes() and time.monotonic() < deadline:
        time.sleep(.02)
    assert pid not in processes()


@pytest.mark.skipif(
    sys.platform != "win32" or os.environ.get("MANAGER_OBSERVATION_WINDOWS_E2E") != "1",
    reason="explicit interactive Windows window/foreground observation",
)
def test_source_probe_two_cycles_from_windowless_parent(tmp_path):
    import ctypes
    from agent_procutil import (
        no_window_kwargs, spawn_sync_in_kill_on_close_job,
        windowless_python, windowless_python_env,
    )
    from mux_windows_observer import descendants, processes, windows

    session = ctypes.c_ulong()
    assert ctypes.WinDLL("kernel32").ProcessIdToSessionId(os.getpid(), ctypes.byref(session))
    if session.value == 0:
        pytest.skip("desktop observation unavailable in Windows session zero")
    root = Path(__file__).resolve().parents[2]
    destination = tmp_path / "windowless-run"
    wrapper = (
        "import ctypes,runpy,sys;"
        "assert not ctypes.WinDLL('kernel32').GetConsoleWindow();"
        "python,entry,destination=sys.argv[1:];sys.executable=python;"
        "sys.argv=[entry,'--run-dir',destination,'--samples','1','--timeout','30'];"
        "runpy.run_path(entry,run_name='__main__')"
    )
    env = {**os.environ, **windowless_python_env(sys.executable)}
    baseline_windows, _ = windows()
    owned, visible, focused = set(), set(), set()
    with (tmp_path / "windowless.log").open("w", encoding="utf-8") as log:
        process, job = spawn_sync_in_kill_on_close_job(
            [
                windowless_python(sys.executable, require_windowless=True), "-s", "-c", wrapper,
                sys.executable, str(root / "tools/performance/manager_native_probe.py"), str(destination),
            ],
            cwd=root, env=env, stdout=log, stderr=log, require_job=True,
            allow_breakaway=False, **no_window_kwargs(),
        )
        try:
            deadline = time.monotonic() + 75
            while process.poll() is None and time.monotonic() < deadline:
                table = processes()
                owned.update(descendants(process.pid, table))
                shown, foreground = windows()
                for hwnd, pid in shown.items():
                    name = table.get(pid, ("", 0))[0].lower()
                    if pid in owned or (
                        hwnd not in baseline_windows
                        and name in {"conhost.exe", "openconsole.exe", "windowsterminal.exe"}
                    ):
                        visible.add(hwnd)
                if foreground in owned:
                    focused.add(foreground)
                time.sleep(.02)
            assert process.wait(timeout=5) == 0
        finally:
            if job is not None:
                job.close()
            process.wait(timeout=5)
    report = json.loads((destination / "report.json").read_text())
    assert all(mode["completed"] == 1 and mode["failed"] == 0 for mode in report["modes"].values())
    assert len(owned) >= 3
    assert not visible and not focused
