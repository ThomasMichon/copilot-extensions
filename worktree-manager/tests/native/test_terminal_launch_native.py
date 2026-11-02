"""Real ConPTY handoff; no installed runtime, daemon or product session."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import sys
import time

import pytest

pytestmark = [
    pytest.mark.skipif(os.name != "nt", reason="Native Windows ConPTY contract"),
    pytest.mark.timeout(90),
]
HELPER = Path(__file__).resolve().parents[2] / "bin" / "terminal-launch.ps1"


def _quote(value):
    return "'" + str(value).replace("'", "''") + "'"


def _aw_front_door(root, entry, fixture):
    source = HELPER.parents[2] / "plugins" / "agent-worktrees"
    payload = root / "payload"
    binary = payload / "bin"
    binary.mkdir(parents=True)
    shutil.copy2(source / "plugin.json", payload / "plugin.json")
    for name in ("agent-worktrees.ps1", "agent-worktrees-update-entry.ps1"):
        shutil.copy2(source / "bin" / name, binary / name)
    shutil.copy2(HELPER, binary / HELPER.name)
    python = root / "fixture-python.ps1"
    python.write_text(
        "if(@($args) -contains '-c'){\n"
        f"& {_quote(sys.executable)} @args\n"
        "}else{\n"
        f"& {_quote(sys.executable)} -I -B {_quote(fixture)} {_quote(root)} outer selected\n"
        "}\nexit $LASTEXITCODE\n", encoding="utf-8",
    )
    runtime = root / "profile" / ".agent-worktrees"
    (runtime / "bin").mkdir(parents=True)
    (runtime / "bin" / "resolve-runtime.ps1").write_text(
        f"$AwPy={_quote(python)}\n", encoding="utf-8",
    )
    if entry == "aw-user":
        return binary / "agent-worktrees.ps1"
    scripts = payload / "scripts"
    (scripts / "installation-context").mkdir(parents=True)
    shutil.copy2(source / "scripts" / "invoke-payload-runtime.ps1", scripts)
    (scripts / "resolve-runtime.ps1").write_text(
        f"param([string]$TraceMode)\n$AgentRtPy={_quote(python)}\n", encoding="utf-8",
    )
    (scripts / "install.ps1").write_text("throw 'Fixture must not provision'\n", encoding="utf-8")
    (scripts / "installation-context" / "installation-context.ps1").write_text(
        "'{\"status\":\"ready\",\"reason\":\"policy-default-false\","
        "\"actualMode\":\"legacy\",\"desiredMode\":\"legacy\"}'\nexit 0\n", encoding="utf-8",
    )
    return scripts / "invoke-payload-runtime.ps1"


def _wait(path, console):
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8-sig"))
        assert not console.exited(console.process.process), console.output.decode("utf-8", "replace")
        time.sleep(.025)
    raise AssertionError(f"Missing {path.name}: {console.output.decode('utf-8', 'replace')}")


def _scripts(root, mode, entry):
    fixture = Path(__file__).with_name("_terminal_console_fixture.py").resolve()
    shell = shutil.which("pwsh")
    assert shell, "PowerShell 7 is required for native handoff qualification"
    host = root / "host.ps1"
    nested = root / "nested.ps1"
    selected = root / "selected \u65e5.ps1"
    literal = ["spaces here", 'quote" and \'', "", ";$()&", "\u65e5", "--", "-literal"]
    (root / "literal.json").write_text(json.dumps(literal), encoding="utf-8")
    common = "$ErrorActionPreference='Stop'\n. " + _quote(HELPER) + "\n"
    invocation = (
        f"Invoke-WorktreeTerminalLaunch -Executable {_quote(sys.executable)} "
        f"-Arguments @('-I','-B',{_quote(fixture)},{_quote(root)},'inner',{_quote(mode)})\n"
    )
    if entry in {"ps1", "cmd", "manager-standalone"}:
        from _manager_process_fixture import synthetic_runtime
        from worktree_manager import runtime_launchers

        manager = root / "manager"
        program = (
            "import runpy,sys\n"
            f"sys.argv={[str(fixture), str(root), 'inner', mode]!r}\n"
            f"runpy.run_path({str(fixture)!r}, run_name='__main__')\n"
        )
        synthetic_runtime(manager, "fixture", program=program)
        (manager / "current-version").write_text("fixture\n", encoding="utf-8")
        for kind in ("cmd", "ps1"):
            (root / f"worktree-manager.{kind}").write_text(
                getattr(runtime_launchers, f"_{kind}_binstub")(), encoding="utf-8",
            )
        kind = "ps1" if entry == "manager-standalone" else entry
        invocation = f"& {_quote(root / ('worktree-manager.' + kind))}\n"
    nested.write_text(
        common +
        f"[IO.File]::WriteAllText({_quote(root / 'nested.json')},"
        "(@{pid=$PID}|ConvertTo-Json -Compress))\n"
        + invocation + "exit $LASTEXITCODE\n", encoding="utf-8",
    )
    outer_invocation = (
        f"Invoke-WorktreeTerminalLaunch -Executable {_quote(sys.executable)} "
        f"-Arguments @('-I','-B',{_quote(fixture)},{_quote(root)},'outer',{_quote(mode)})\n"
    )
    if entry.startswith("aw-"):
        outer_invocation = f"& {_quote(_aw_front_door(root, entry, fixture))}\n"
    elif entry == "manager-standalone":
        outer_invocation = invocation
    host.write_text(
        common + outer_invocation +
        "$code=$LASTEXITCODE\n"
        f"[IO.File]::WriteAllText({_quote(root / 'host-result.json')},"
        "(@{code=$code;channel=$env:CE_TERMINAL_LAUNCH;pid=$PID;"
        "kept=$env:FIXTURE_KEPT;added=$env:FIXTURE_ADDED;removed=$env:FIXTURE_REMOVED;"
        "root=$env:WORKTREE_MANAGER_ROOT;cwd=(Get-Location).Path}|ConvertTo-Json -Compress))\n"
        "exit $code\n", encoding="utf-8",
    )
    selected.write_text(
        "$ErrorActionPreference='Stop'\n"
        f"[IO.File]::WriteAllText({_quote(root / 'attached.json')},"
        "(@{pid=$PID;arguments=@($args);redirected=[Console]::IsInputRedirected;"
        "channel=$env:CE_TERMINAL_LAUNCH;cwd=(Get-Location).Path;"
        "engine=$env:WORKTREE_MANAGER_ENGINE_ARGV}|ConvertTo-Json -Compress))\n"
        "$env:FIXTURE_KEPT='changed-in-launcher'\n"
        "$env:FIXTURE_ADDED='launch-only'\n"
        "Remove-Item Env:FIXTURE_REMOVED\n"
        "[Console]::WriteLine('HANDOFF-RENDERED')\n"
        "$line=[Console]::ReadLine()\n"
        f"[IO.File]::WriteAllText({_quote(root / 'input.json')},"
        "(@{line=$line;width=[Console]::WindowWidth;height=[Console]::WindowHeight}"
        "|ConvertTo-Json -Compress))\n"
        "$global:LASTEXITCODE=23\n", encoding="utf-8",
    )
    return [shell, "-NoProfile", "-NoLogo", "-File", str(host)], literal


@pytest.mark.parametrize(("mode", "entry"), [
    ("selected", "helper"), ("terminal-close", "helper"),
    ("cancel", "helper"), ("failed", "helper"), ("malformed", "helper"),
    ("selected", "ps1"), ("selected", "cmd"),
    ("selected", "manager-standalone"), ("selected", "aw-user"), ("selected", "aw-payload"),
])
def test_shell_handoff_settles_both_python_jobs_before_real_terminal_attachment(tmp_path, mode, entry):
    from _terminal_console_fixture import Console

    argv, literal = _scripts(tmp_path, mode, entry)
    temporary = tmp_path / "temporary"
    temporary.mkdir()
    env = dict(os.environ, TEMP=str(temporary), TMP=str(temporary))
    for name in ("CE_TERMINAL_LAUNCH", "WORKTREE_MANAGER_ENGINE_ARGV", "WORKTREE_NO_MUX"):
        env.pop(name, None)
    env["WORKTREE_MANAGER_ROOT"] = str(tmp_path / "manager")
    env["FIXTURE_KEPT"] = env["FIXTURE_REMOVED"] = "original"
    env.pop("FIXTURE_ADDED", None)
    if entry.startswith("aw-"):
        env["USERPROFILE"] = env["HOME"] = str(tmp_path / "profile")
        env["AGENT_WORKTREES_PAYLOAD_ROOT"] = str(tmp_path / "payload")
        env.pop("COPILOT_EXTENSIONS_CONTEXT", None)
    with Console(argv, env=env, cwd=tmp_path) as console:
        ready = _wait(tmp_path / "prepared.json", console)
        if entry != "manager-standalone":
            _wait(tmp_path / "nested.json", console)
        descendants = console.pin_descendants()
        workers = {ready["pid"], ready["outer"]} - {0}
        assert workers <= descendants.keys()
        assert sum(name.lower().startswith("python") for name, _ in descendants.values()) >= len(workers)
        assert all(not console.exited(handle) for _, handle in descendants.values())
        request = Path(ready["request"])
        (tmp_path / "release").touch()
        if mode in {"selected", "terminal-close"}:
            attached = _wait(tmp_path / "attached.json", console)
            assert attached["pid"] == console.process.pid
            survivors = {
                pid: name for pid, (name, handle) in descendants.items()
                if not console.exited(handle)
            }
            assert not survivors, survivors
            assert attached["arguments"] == literal
            assert attached["redirected"] is False
            assert not attached["channel"]
            assert not request.parent.exists()
            assert Path(attached["cwd"]) == tmp_path
            assert json.loads(attached["engine"]) == ["fixture-engine", "literal engine"]
            if mode == "terminal-close":
                console.hangup()
                assert not (tmp_path / "host-result.json").exists()
                assert not (tmp_path / "input.json").exists()
                return
            console.resize(118, 37)
            console.send(b"typed input\r")
            typed = _wait(tmp_path / "input.json", console)
            assert typed == {"line": "typed input", "width": 118, "height": 37}
        assert console.wait() == {"selected": 23, "cancel": 0, "failed": 7, "malformed": 1}[mode]
        result = json.loads((tmp_path / "host-result.json").read_text(encoding="utf-8-sig"))
        assert not result["channel"]
        assert result["root"] == env["WORKTREE_MANAGER_ROOT"]
        assert result["kept"] == result["removed"] == "original"
        assert not result["added"]
        assert Path(result["cwd"]) == tmp_path
        assert not request.parent.exists()
        assert list(temporary.glob("ce-terminal-*")) == []
        assert (tmp_path / "attached.json").exists() is (mode == "selected")
        if mode == "selected":
            from worktree_manager.launch_attempts import LaunchAttempts

            events = LaunchAttempts(Path(env["WORKTREE_MANAGER_ROOT"])).detail("d" * 32)["events"]
            assert [event["phase"] for event in events] == [
                "confirmed", "launch-requested", "launcher-returned",
            ]
            assert events[-1]["details"]["exit_code"] == 23
    assert console.handles == []
    if mode == "selected":
        assert b"HANDOFF-RENDERED" in console.output


def test_real_launcher_refusal_preserves_exit_journal_and_original_diagnostics(tmp_path):
    from _terminal_console_fixture import Console
    from test_launch_admission_ordering import _calls, _fixture
    from worktree_manager.launch_attempts import LaunchAttempts

    env, _target, logs = _fixture(tmp_path)
    env["WORKTREE_MANAGER_ROOT"] = str(tmp_path / "manager")
    env.pop("CE_TERMINAL_LAUNCH", None)
    before = {path.name: path.read_bytes() for path in logs.iterdir()}
    argv, _ = _scripts(tmp_path, "production-refusal", "helper")
    with Console(argv, env=env, cwd=tmp_path) as console:
        ready = _wait(tmp_path / "prepared.json", console)
        pinned = console.pin_descendants()
        (tmp_path / "release").touch()
        assert console.wait() == 3
        assert all(console.exited(handle) for _, handle in pinned.values())
        assert not Path(ready["request"]).parent.exists()
        assert json.loads((tmp_path / "host-result.json").read_text(encoding="utf-8-sig"))["code"] == 3
    assert {path.name: path.read_bytes() for path in logs.iterdir()} == before
    calls = _calls(env)
    assert any(call[:2] == ["agent-policy", "check"] for call in calls)
    assert not (tmp_path / "unexpected-preparation").exists()
    events = LaunchAttempts(Path(env["WORKTREE_MANAGER_ROOT"])).detail("d" * 32)["events"]
    assert events[-1]["phase"] == "launcher-returned"
    assert events[-1]["details"]["exit_code"] == 3
