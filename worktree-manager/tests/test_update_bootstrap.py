"""Fresh bootstrap repair precedes, and does not replace, suite orchestration."""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from itertools import count
from pathlib import Path

import pytest
import agent_procutil
from agent_procutil import no_window_kwargs
from update_telemetry import human
from update_telemetry.human import HumanOperation, current_operation

from worktree_manager import __main__ as manager
from worktree_manager import core_install, update_bootstrap


@pytest.fixture
def repair_clock(monkeypatch):
    ticks = count()
    monkeypatch.setattr(
        update_bootstrap, "HumanOperation",
        lambda label: HumanOperation(
            label, wall=lambda: datetime(2026, 1, 2, 3, 4, 5, 678000, tzinfo=timezone.utc),
            monotonic=lambda: next(ticks) / 4,
        ),
    )


def test_native_repair_lifecycle_starts_without_site_packages(monkeypatch, tmp_path):
    installation = update_bootstrap._Installation(
        root=tmp_path / "runtime", payload=tmp_path / "payload", identity={},
        sdk=tmp_path / "sdk",
    )
    observed = []
    monkeypatch.setattr(
        update_bootstrap, "_wait",
        lambda command, environment, cwd: observed.append((command, environment, cwd)) or 17,
    )
    assert update_bootstrap._invoke(installation, "provision", {"PATH": ""}) == 17
    command, environment, cwd = observed[0]
    assert command[1:6] == ["-I", "-S", "-B", "-X", "utf8"]
    assert command[6] == str(installation.sdk / "scripts/command_binding.py")
    native = command[command.index("--") + 1:]
    assert "provision" in native
    assert "-BootstrapPython" not in native
    assert "--runtime-publication-python" not in native
    assert cwd == installation.payload
    assert environment["COPILOT_PLUGIN_STAGED_FROM"] == str(installation.payload)


@pytest.mark.parametrize(
    ("exit_code", "create_bin"), [(0, True), (17, False), (0, False)],
)
def test_missing_bin_repair_uses_one_update_lifecycle(
    monkeypatch, tmp_path, capsys, exit_code, create_bin,
):
    root = tmp_path / "runtime"
    root.mkdir()
    installation = update_bootstrap._Installation(root, tmp_path / "payload", {}, tmp_path)
    identity = {"captured": "owner"}
    monkeypatch.setattr(
        update_bootstrap, "_refresh_source",
        lambda source, environment: update_bootstrap._RefreshedSource(
            identity, installation.payload,
        ),
    )
    monkeypatch.setattr(update_bootstrap, "_incumbent", lambda *args: installation)
    monkeypatch.setattr(update_bootstrap, "_identity", lambda selected: identity)
    actions = []

    def invoke(selected, action, environment):
        actions.append(action)
        if create_bin:
            (selected.root / "bin").mkdir(exist_ok=True)
        return exit_code

    monkeypatch.setattr(update_bootstrap, "_invoke", invoke)
    assert update_bootstrap.repair_engine(tmp_path / "source") == (
        exit_code or (0 if create_bin else 1)
    )
    assert actions == ["update"]
    if exit_code == 0 and not create_bin:
        assert "success without its runtime bin" in capsys.readouterr().err


@pytest.mark.parametrize("has_bin", [True, False])
def test_update_repair_requests_missing_bin_only_for_its_captured_root(
    monkeypatch, tmp_path, has_bin,
):
    root = tmp_path / "runtime"
    if has_bin:
        (root / "bin").mkdir(parents=True)
    installation = update_bootstrap._Installation(root, tmp_path / "payload", {}, tmp_path)
    commands = []
    monkeypatch.setattr(
        update_bootstrap, "_wait",
        lambda command, environment, cwd: commands.append(command) or 0,
    )
    assert update_bootstrap._invoke(installation, "update", {}) == 0
    flag = "-ProvisionMissingRuntime" if os.name == "nt" else "--provision-missing-runtime"
    assert (flag in commands[0]) is not has_bin


def test_update_bootstrap_ordinary_prefix_opens_no_human_scope(monkeypatch, capsys):
    monkeypatch.setattr(
        update_bootstrap, "HumanOperation",
        lambda label: pytest.fail("ordinary updates do not open a repair scope"),
    )
    arguments = ["--force", "--project", "sample"]
    assert update_bootstrap.prepare_update(arguments) == update_bootstrap.PreparedUpdate(arguments)
    captured = capsys.readouterr()
    assert captured.out == "" and captured.err == ""
    assert current_operation() is None


@pytest.mark.parametrize("rc", [0, 17])
def test_update_bootstrap_frames_own_scope_and_preserves_result(
    monkeypatch, capsys, repair_clock, rc,
):
    flags = ["--project", "sample", "--skip-modules", "a b"]
    environment = {"FIXTURE": "unchanged"}
    seen = []

    def repair(source):
        seen.append((source, current_operation().path))
        return update_bootstrap.RepairResult(rc, environment)

    monkeypatch.setattr(update_bootstrap, "_repair_installation", repair)
    result = update_bootstrap.prepare_update(["--repair-engine", "/fresh/source", *flags])
    assert result == update_bootstrap.PreparedUpdate(flags, True, rc, environment)
    assert result.environment is environment
    assert seen == [(Path("/fresh/source"), "update/manager/engine_repair")]
    captured = capsys.readouterr()
    assert captured.out == ""
    state = "done" if rc == 0 else "failed"
    assert captured.err.splitlines() == [
        "[03:04:05.678Z] update/manager/engine_repair starting [elapsed 0.2s]",
        f"[03:04:05.678Z] update/manager/engine_repair {state} exit {rc} [duration 0.5s]",
    ]
    assert current_operation() is None


def test_update_bootstrap_frames_missing_source_without_aggregate_run(
    monkeypatch, capsys, repair_clock,
):
    from worktree_manager import update_events

    monkeypatch.setattr(
        update_events, "UpdateRun",
        lambda: pytest.fail("invalid bootstrap must not open an aggregate update run"),
    )
    assert manager._cmd_update(["--repair-engine"]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.splitlines() == [
        "[03:04:05.678Z] update/manager/engine_repair starting [elapsed 0.2s]",
        "[03:04:05.678Z] update/manager/engine_repair failed "
        "Update repair requires its fresh source checkout. [elapsed 0.5s]",
        "[03:04:05.678Z] update/manager/engine_repair failed exit 2 [duration 0.8s]",
    ]
    assert current_operation() is None


def test_update_bootstrap_frames_and_redacts_each_refusal_line(
    monkeypatch, capsys, repair_clock,
):
    def refuse(source, environment):
        raise RuntimeError("source refused\nhttps://fixture-secret@example.test/source")

    monkeypatch.setattr(update_bootstrap, "_refresh_source", refuse)
    assert update_bootstrap.prepare_update(["--repair-engine", "/fresh/source"]).exit_code == 1
    captured = capsys.readouterr()
    assert captured.out == "" and "fixture-secret" not in captured.err
    assert captured.err.splitlines() == [
        "[03:04:05.678Z] update/manager/engine_repair starting [elapsed 0.2s]",
        "[03:04:05.678Z] update/manager/engine_repair failed "
        "Engine repair refused: source refused [elapsed 0.5s]",
        "[03:04:05.678Z] update/manager/engine_repair failed "
        "https://***@example.test/source [elapsed 0.8s]",
        "[03:04:05.678Z] update/manager/engine_repair failed exit 1 [duration 1.0s]",
    ]
    assert current_operation() is None


@pytest.mark.parametrize("exception", [KeyboardInterrupt, TypeError])
def test_update_bootstrap_unhandled_exception_closes_human_scope(
    monkeypatch, capsys, repair_clock, exception,
):
    def fail(source):
        raise exception("fixture interrupted")

    monkeypatch.setattr(update_bootstrap, "_repair_installation", fail)
    with pytest.raises(exception, match="fixture interrupted"):
        update_bootstrap.prepare_update(["--repair-engine", "/fresh/source"])
    captured = capsys.readouterr()
    assert captured.out == ""
    assert f"failed {exception.__name__}: fixture interrupted [duration 0.5s]" in captured.err
    assert " done " not in captured.err and "exit 0" not in captured.err
    assert current_operation() is None


@pytest.mark.parametrize("missing_source", [False, True])
def test_update_bootstrap_without_formatter_retains_original_diagnostic(
    monkeypatch, capsys, missing_source,
):
    monkeypatch.setattr(update_bootstrap, "HumanOperation", None)

    def refuse(source, environment):
        raise RuntimeError("fixture refused")

    monkeypatch.setattr(update_bootstrap, "_refresh_source", refuse)
    args = ["--repair-engine"] + ([] if missing_source else ["/fresh/source"])
    result = update_bootstrap.prepare_update(args)
    captured = capsys.readouterr()
    assert result.exit_code == (2 if missing_source else 1)
    assert captured.out == ""
    assert captured.err == (
        "Update repair requires its fresh source checkout.\n" if missing_source
        else "Engine repair refused: fixture refused\n"
    )
    assert current_operation() is None


@pytest.mark.parametrize("rc", [0, 17])
@pytest.mark.parametrize("input_kind", ["pipe", "devnull"])
def test_update_bootstrap_leaves_real_native_child_streams_and_exit_untouched(
    tmp_path, rc, input_kind,
):
    root = tmp_path / "runtime"
    (root / "bin").mkdir(parents=True)
    native = (
        "import json,os,sys; data=os.read(0,1024); "
        "os.write(1,(json.dumps({'native':True,'input':data.decode('utf-8')},"
        "ensure_ascii=False)+'\\n').encode('utf-8')); "
        "os.write(2,'native diagnostic caf\\u00e9\\n'.encode('utf-8')); "
        "sys.exit(int(sys.argv[1]))"
    )
    script = """
import sys
from pathlib import Path
sys.path[:0] = sys.argv[1:4]
from update_telemetry.human import current_operation
from worktree_manager import update_bootstrap as update
root = Path(sys.argv[4])
installation = update._Installation(root, root / "payload", {}, root)
update._refresh_source = lambda source, env: update._RefreshedSource({}, installation.payload)
update._identity = lambda selected: {}
update._incumbent = lambda source, env, payload: installation
calls = []
def invoke(selected, action, environment):
    calls.append((selected, action))
    python = str(Path(getattr(sys, "_base_executable", sys.executable)).resolve())
    command = [python, "-I", "-B", "-X", "utf8", "-c", sys.argv[6], sys.argv[5]]
    return update._wait(command, environment, root)
update._invoke = invoke
result = update.prepare_update(["--repair-engine", str(root)])
assert calls == [(installation, "update")]
assert current_operation() is None
sys.exit(result.exit_code)
"""
    captured = subprocess.run(
        [
            str(Path(getattr(sys, "_base_executable", sys.executable)).resolve()),
            "-I", "-B", "-X", "utf8", "-c", script,
            str(Path(update_bootstrap.__file__).parents[1]),
            str(Path(human.__file__).parents[1]),
            str(Path(agent_procutil.__file__).parents[1]),
            str(root), str(rc), native,
        ],
        capture_output=True, text=True, encoding="utf-8", timeout=15, **no_window_kwargs(),
        **({"input": "native input caf\u00e9"} if input_kind == "pipe"
           else {"stdin": subprocess.DEVNULL}),
    )
    assert captured.returncode == rc, captured.stderr
    assert captured.stdout == json.dumps({
        "native": True, "input": "native input caf\u00e9" if input_kind == "pipe" else "",
    }, ensure_ascii=False) + "\n", captured.stderr
    lines = captured.stderr.splitlines()
    assert lines[1] == "native diagnostic caf\u00e9"
    assert len(lines) == 3
    assert re.fullmatch(
        r"\[\d{2}:\d{2}:\d{2}\.\d{3}Z\] update/manager/engine_repair "
        r"starting \[elapsed \d+\.\ds\]", lines[0],
    )
    state = "done" if rc == 0 else "failed"
    assert re.fullmatch(
        r"\[\d{2}:\d{2}:\d{2}\.\d{3}Z\] update/manager/engine_repair "
        + state + rf" exit {rc} \[duration \d+\.\ds\]", lines[2],
    )
    assert current_operation() is None


@pytest.mark.parametrize("rc", [0, 17])
def test_update_bootstrap_wait_uses_callers_selected_native_streams(tmp_path, rc):
    incoming = tmp_path / "selected-input"
    outgoing = tmp_path / "selected-output"
    diagnostic = tmp_path / "selected-diagnostic"
    incoming.write_text("selected input caf\u00e9", encoding="utf-8")
    native = (
        "import json,os,sys; data=sys.stdin.read(); "
        "os.write(1,(json.dumps({'input':data},ensure_ascii=False)+'\\n').encode('utf-8')); "
        "os.write(2,'selected diagnostic caf\\u00e9\\n'.encode('utf-8')); "
        "sys.exit(int(sys.argv[1]))"
    )
    script = """
import os, sys
from pathlib import Path
sys.path[:0] = sys.argv[1:4]
from worktree_manager import update_bootstrap as update
root = Path(sys.argv[4])
original = sys.stdin, sys.stdout, sys.stderr
try:
    with open(sys.argv[5], encoding='utf-8') as incoming, \\
            open(sys.argv[6], 'w', encoding='utf-8') as outgoing, \\
            open(sys.argv[7], 'w', encoding='utf-8') as diagnostic:
        sys.stdin, sys.stdout, sys.stderr = incoming, outgoing, diagnostic
        base = str(Path(getattr(sys, '_base_executable', sys.executable)).resolve())
        command = [base, '-I', '-B', '-X', 'utf8', '-c', sys.argv[9], sys.argv[8]]
        code = update._wait(command, dict(os.environ), root)
finally:
    sys.stdin, sys.stdout, sys.stderr = original
raise SystemExit(code)
"""
    result = subprocess.run(
        [
            str(Path(getattr(sys, "_base_executable", sys.executable)).resolve()),
            "-I", "-B", "-X", "utf8", "-c", script,
            str(Path(update_bootstrap.__file__).parents[1]),
            str(Path(human.__file__).parents[1]),
            str(Path(agent_procutil.__file__).parents[1]),
            str(tmp_path), str(incoming), str(outgoing), str(diagnostic), str(rc), native,
        ],
        input="unselected native input", capture_output=True,
        text=True, encoding="utf-8", timeout=15, **no_window_kwargs(),
    )
    assert result.returncode == rc, result.stderr
    assert result.stdout == result.stderr == ""
    assert json.loads(outgoing.read_text(encoding="utf-8")) == {"input": "selected input caf\u00e9"}
    assert diagnostic.read_text(encoding="utf-8") == "selected diagnostic caf\u00e9\n"


def test_update_bootstrap_exit_only_entry_stays_unframed(monkeypatch, capsys):
    def refuse(source, environment):
        raise RuntimeError("fixture refused")

    monkeypatch.setattr(update_bootstrap, "_refresh_source", refuse)
    assert update_bootstrap.repair_engine(Path("/fresh/source")) == 1
    captured = capsys.readouterr()
    assert captured.out == "" and captured.err == "Engine repair refused: fixture refused\n"
    assert current_operation() is None


@pytest.mark.parametrize("interrupted", [False, True])
def test_update_bootstrap_native_wait_retains_custody_and_arguments(
    monkeypatch, tmp_path, interrupted,
):
    command, environment = ["fixture", "a b", "--flag"], {"FIXTURE": "unchanged"}
    calls = []

    class Process:
        def __enter__(self):
            return self

        def wait(self):
            calls.append("wait")
            if interrupted and calls.count("wait") == 1:
                raise KeyboardInterrupt
            return 17

        def __exit__(self, *args):
            calls.append("close")

    def popen(argv, **kwargs):
        assert argv is command
        assert kwargs == {
            "env": environment, "cwd": tmp_path,
            "stdin": sys.stdin, "stdout": sys.stdout, "stderr": sys.stderr,
            **no_window_kwargs(),
        }
        assert kwargs["env"] is environment
        return Process()

    monkeypatch.setattr(update_bootstrap.subprocess, "Popen", popen)
    if interrupted:
        with pytest.raises(KeyboardInterrupt):
            update_bootstrap._wait(command, environment, tmp_path)
        assert calls == ["wait", "wait", "close"]
    else:
        assert update_bootstrap._wait(command, environment, tmp_path) == 17
        assert calls == ["wait", "close"]


def test_update_bootstrap_delivery_paths_are_sandboxed():
    home = Path(os.environ["USERPROFILE"])
    assert home == Path.home() and home.name == "fake-home"
    assert "WORKTREE_MANAGER_ROOT" not in os.environ
    assert "AGENT_WORKTREES_CONTROL_PLANE_PROVIDERS_DIR" not in os.environ


@pytest.mark.parametrize("rc", [0, 17])
def test_update_bootstrap_repairs_before_orchestration(monkeypatch, rc):
    calls = []
    monkeypatch.setattr(
        update_bootstrap, "_repair_installation",
        lambda source: calls.append("repair") or update_bootstrap.RepairResult(rc),
    )
    monkeypatch.setattr(
        manager, "_cmd_update_steps",
        lambda rest, run, **kwargs: calls.append(rest) or 5,
    )
    flags = ["--project", "sample", "--force", "--skip-modules", "a b"]
    assert manager._cmd_update(["--repair-engine", "/fresh/source", *flags]) == (rc or 5)
    assert calls == (["repair"] if rc else ["repair", flags])


def test_update_bootstrap_acquires_source_before_native_repair(monkeypatch, tmp_path):
    root = tmp_path / "runtime"
    (root / "bin").mkdir(parents=True)
    identity = {
        "installationId": "fixture", "pluginId": "agent-worktrees", "mode": "legacy",
        "runtimeRoot": str(root), "sourceFingerprint": "fixture", "environment": {},
    }
    installation = update_bootstrap._Installation(root, tmp_path / "payload", identity, tmp_path)
    calls = []
    monkeypatch.setattr(
        update_bootstrap, "_refresh_source",
        lambda source, env: calls.append("acquire") or update_bootstrap._RefreshedSource(
            identity, installation.payload,
        ),
    )
    monkeypatch.setattr(
        update_bootstrap, "_incumbent",
        lambda source, env, payload: calls.append(("admit", payload)) or installation,
    )
    monkeypatch.setattr(
        update_bootstrap, "_invoke",
        lambda target, action, env: calls.append(("native", target, action)) or 19,
    )
    assert update_bootstrap.repair_engine(tmp_path / "fresh-source") == 19
    assert calls == [
        "acquire", ("admit", installation.payload), ("admit", installation.payload),
        ("native", installation, "update"),
    ]


def test_update_bootstrap_missing_installer_is_failure(monkeypatch, capsys):
    monkeypatch.setattr(core_install, "install_script", lambda path: None)
    assert update_bootstrap.repair_engine(Path("/missing/fresh-source")) == 1
    assert "unavailable" in capsys.readouterr().err


def test_update_bootstrap_does_not_route_ordinary_update_through_repair(monkeypatch):
    monkeypatch.setattr(
        update_bootstrap, "_repair_installation",
        lambda source: pytest.fail("ordinary Manager update must not require bootstrap staging"),
    )
    monkeypatch.setattr(manager, "_cmd_update_steps", lambda rest, run, **kwargs: 5)
    assert manager._cmd_update(["--force"]) == 5


def test_update_bootstrap_missing_source_never_repairs_or_orchestrates(monkeypatch, capsys):
    monkeypatch.setattr(
        update_bootstrap, "_repair_installation",
        lambda source: pytest.fail("missing bootstrap source must not repair"),
    )
    monkeypatch.setattr(
        manager, "_cmd_update_steps",
        lambda *args, **kwargs: pytest.fail("missing bootstrap source must not orchestrate"),
    )
    assert manager._cmd_update(["--repair-engine"]) == 2
    assert "requires its fresh source checkout" in capsys.readouterr().err


def test_update_bootstrap_fresh_manager_does_not_reselect_source(monkeypatch):
    from worktree_manager import engine_client, self_install

    monkeypatch.setattr(
        update_bootstrap, "_repair_installation",
        lambda source: update_bootstrap.RepairResult(0),
    )
    monkeypatch.setattr(
        self_install, "self_update",
        lambda **kwargs: pytest.fail("fresh Manager must not fetch a second source"),
    )
    calls = []
    monkeypatch.setattr(
        engine_client, "run_engine_passthrough",
        lambda project, args: calls.append((project, args)) or 0,
    )
    assert manager._cmd_update([
        "--repair-engine", "/fresh/source", "--project", "sample", "--force",
    ]) == 0
    assert calls == [("sample", ["update", "--no-manager", "--force"])]
