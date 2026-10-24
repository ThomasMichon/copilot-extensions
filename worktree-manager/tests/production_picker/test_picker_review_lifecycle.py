"""Review teardown owns pending reads, not just their ordinary timeout path."""

from __future__ import annotations

import io
import json
import os
from pathlib import Path
import runpy
import subprocess
import sys
import threading
import time

import pytest
from agent_procutil import no_window_flags

from worktree_manager import agent_plugin_runtime, engine_client, engine_process, picker_review
from worktree_manager import engine_posix_process
from worktree_manager.production_picker import engine_group_b
from worktree_manager.production_picker.picker_tui import tasks


@pytest.mark.parametrize("candidate", [["--candidate", "test"], ["--candidate=test"]])
def test_source_launcher_uses_normal_shutdown_workaround(monkeypatch, candidate):
    from worktree_manager import _shutdown_exit, picker_review_cli

    calls = []
    script = Path(__file__).resolve().parents[2] / "scripts" / "picker-review.py"
    monkeypatch.setattr(sys, "dont_write_bytecode", sys.dont_write_bytecode)
    monkeypatch.setattr(sys, "path", list(sys.path))
    monkeypatch.setattr(sys, "argv", [str(script), "example", *candidate])
    monkeypatch.setattr(picker_review_cli, "main", lambda args: calls.append(("main", args)) or 0)
    monkeypatch.setattr(_shutdown_exit, "run_and_exit",
                        lambda main: calls.append(("exit", main())))
    runpy.run_path(str(script), run_name="__main__")
    assert calls == [("main", ["example", *candidate]), ("exit", 0)]


@pytest.mark.parametrize("kind", ["bootstrap", "catalog"])
def test_teardown_cancels_and_drains_real_read_descendants(tmp_path, monkeypatch, kind):
    heartbeat = tmp_path / "heartbeat"
    grandchild = (
        "import time\nfrom pathlib import Path\n"
        f"p=Path({str(heartbeat)!r})\n"
        "while True:\n p.write_text(str(time.monotonic()))\n time.sleep(.05)\n"
    )
    code = (
        "import subprocess,sys,time\n"
        f"subprocess.Popen([sys.executable,'-c',{grandchild!r}],"
        f"creationflags={no_window_flags()})\n"
        "time.sleep(60)\n"
    )
    command = [sys.executable, "-c", code]
    monkeypatch.setattr(picker_review, "_review", None)
    monkeypatch.setattr(agent_plugin_runtime, "resolve_installed_plugin_slot",
                        lambda plugin: tmp_path / "owner" / "versions" / "test")
    monkeypatch.setattr(agent_plugin_runtime, "resolve_installed_plugin_invocation",
                        lambda plugin: (command, tmp_path / "owner"))
    monkeypatch.setattr(picker_review, "list_command",
                        lambda *args: (command, {"AGENT_DISPATCH_INSTALL_DIR": str(tmp_path / "owner")}))
    results = []

    def read():
        try:
            if kind == "bootstrap":
                engine_group_b.picker_bootstrap("example")
            else:
                runtime = tasks.RegisteredPivotRuntime(
                    type("Pivot", (), {"list_cmd": ["catalog"]})(),
                )
                results.append(runtime._exec_list({})[0])
        except engine_client.EngineError:
            results.append("error")

    with picker_review.session("exit-test") as state:
        worker = threading.Thread(target=read, daemon=True)
        worker.start()
        deadline = time.monotonic() + 10
        while not heartbeat.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        assert heartbeat.exists(), "fixture read descendants never became ready"
    worker.join(timeout=1)
    assert not worker.is_alive()
    assert results == ["error"]
    assert state.reads.cancelled.is_set()
    assert not state.state_dir.exists()
    stopped = heartbeat.read_text()
    time.sleep(0.3)
    assert heartbeat.read_text() == stopped
    with pytest.raises(picker_review.ReadOnlyError, match="closed"):
        picker_review.run_read(["must-not-start"], timeout=1, env={})


def test_posix_stderr_survives_owned_transport_and_legacy_bootstrap_fallback(tmp_path, monkeypatch):
    monkeypatch.setattr(picker_review, "_review", picker_review.Review(
        tmp_path / "ui", tmp_path / "owner", "posix-transport", ("installed-engine",),
    ))
    monkeypatch.setattr(engine_process, "_WINDOWS", False)
    monkeypatch.setattr(engine_process, "contained_test_mode", lambda: False)
    monkeypatch.setattr(engine_client, "installed_engine_command", lambda: ["installed-engine"])
    monkeypatch.setattr(engine_posix_process.signal, "SIGKILL", 9, raising=False)
    monkeypatch.setattr(engine_posix_process.os, "killpg", lambda *args: None, raising=False)
    attempts = []

    def spawn(argv, **kwargs):
        command = json.loads(argv[-2])
        attempts.append(command)
        unsupported = "--project-config" in command
        stdout = b"" if unsupported else json.dumps({
            "version": 1, "project": "example", "should_switch_cwd": False,
            "cwd": None, "default_live": False,
        }).encode()
        os.write(int(argv[-1]), stdout)
        if unsupported:
            assert kwargs["stderr"] != subprocess.PIPE
            kwargs["stderr"].write(b"unrecognized arguments: --project-config\n")
            kwargs["stderr"].flush()
        return type("Owner", (), {
            "pid": 123,
            "stdout": io.StringIO(json.dumps({"returncode": 2 if unsupported else 0}) + "\n"),
            "communicate": lambda self, **kw: ("", None),
        })()

    monkeypatch.setattr(engine_posix_process.subprocess, "Popen", spawn)
    monkeypatch.setattr(engine_posix_process.select, "select",
                        lambda readers, *args: (readers, [], []))
    assert engine_group_b.picker_bootstrap("example")["project"] == "example"
    assert attempts == [
        ["installed-engine", "--project", "example", "picker-bootstrap", "--json", "--project-config"],
        ["installed-engine", "--project", "example", "picker-bootstrap", "--json"],
    ]


@pytest.mark.parametrize("inherited_override", [False, True])
def test_catalog_consumer_reads_selected_nondefault_root(tmp_path, monkeypatch, inherited_override):
    module = (
        Path(__file__).resolve().parents[3] / "plugins" / "agent-dispatch" / "src"
        / "agent_dispatch" / "install_paths.py"
    )
    selected = tmp_path / "selected-owner"
    selected.mkdir()
    (selected / "catalog.json").write_text('[{"id":"selected"}]', encoding="utf-8")
    legacy = Path.home() / ".agent-dispatch"
    legacy.mkdir(exist_ok=True)
    (legacy / "catalog.json").write_text('[{"id":"wrong"}]', encoding="utf-8")
    if inherited_override:
        monkeypatch.setenv("AGENT_DISPATCH_INSTALL_DIR", str(legacy))
    else:
        monkeypatch.delenv("AGENT_DISPATCH_INSTALL_DIR", raising=False)
    previous = os.environ.get("AGENT_DISPATCH_INSTALL_DIR")
    script = (
        "import importlib.util\n"
        f"s=importlib.util.spec_from_file_location('owner_paths',{str(module)!r})\n"
        "m=importlib.util.module_from_spec(s);s.loader.exec_module(m)\n"
        "print((m.install_dir()/'catalog.json').read_text())\n"
    )
    command = [sys.executable, "-c", script]
    monkeypatch.setattr(picker_review, "_review", picker_review.Review(
        tmp_path / "ui", tmp_path / "worktrees", "state-root-test", ("unused",),
    ))
    monkeypatch.setattr(agent_plugin_runtime, "resolve_installed_plugin_invocation",
                        lambda plugin: (command, selected))
    runtime = tasks.RegisteredPivotRuntime(type("Pivot", (), {
        "list_cmd": ["agent-dispatch", "workers", "catalog"],
        "source_owner": "agent-dispatch@example", "stream": False,
    })())
    state, rows, error, _summary = runtime._exec_list({})
    assert (state, rows, error) == ("ready", [{"id": "selected"}], "")
    assert os.environ.get("AGENT_DISPATCH_INSTALL_DIR") == previous
