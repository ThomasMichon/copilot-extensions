"""Real ConPTY handoff; no installed runtime, daemon or product session."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import sys
import time
import venv

import pytest

pytestmark = [
    pytest.mark.skipif(os.name != "nt", reason="Native Windows ConPTY contract"),
    pytest.mark.timeout(90),
]
HELPER = Path(__file__).resolve().parents[2] / "bin" / "terminal-launch.ps1"


def _quote(value):
    return "'" + str(value).replace("'", "''") + "'"


def _query_owner(payload):
    scripts = payload / "scripts"
    scripts.mkdir(exist_ok=True)
    for name in ("daemon_instances.ps1", "daemon_instance_runtime.pyz"):
        shutil.copy2(HELPER.parents[1] / "scripts" / name, scripts / name)


def _aw_front_door(root, entry, fixture):
    source = HELPER.parents[2] / "plugins" / "agent-worktrees"
    payload = root / "payload"
    binary = payload / "bin"
    binary.mkdir(parents=True)
    shutil.copy2(source / "plugin.json", payload / "plugin.json")
    for name in ("agent-worktrees.ps1", "agent-worktrees-update-entry.ps1"):
        shutil.copy2(source / "bin" / name, binary / name)
    shutil.copy2(HELPER, binary / HELPER.name)
    _query_owner(payload)
    target = root / "aw-runtime"
    venv.EnvBuilder(with_pip=False).create(target)
    python = target / "Scripts" / "python.exe"
    package = target / "Lib" / "site-packages" / "agent_worktrees"
    package.mkdir()
    (package / "__init__.py").write_text(
        f"__path__.append({str(source / 'src' / 'agent_worktrees')!r})\n", encoding="utf-8",
    )
    import yaml

    shutil.copytree(Path(yaml.__file__).parent, package.parent / "yaml")
    (package / "__main__.py").write_text(
        "import json,os,runpy,sys\n"
        "if sys.argv[1:]==['--version']:\n"
        " print(json.dumps({'package':'agent_worktrees','capability':'ce-terminal-launch-v1'}))\n"
        " raise SystemExit(0)\n"
        f"sys.path[:0]={[str(path) for path in (HELPER.parents[2] / 'libs').glob('*/src')]!r}\n"
        f"sys.argv={[str(fixture), str(root), 'outer-resident' if entry == 'aw-resident' else 'outer', 'selected']!r}\n"
        f"runpy.run_path({str(fixture)!r},run_name='__main__')\n", encoding="utf-8",
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
    last_error = None
    while time.monotonic() < deadline:
        try:
            return json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError, UnicodeDecodeError) as error:
            last_error = error
        assert not console.exited(console.process.process), console.output.decode("utf-8", "replace")
        time.sleep(.025)
    raise AssertionError(
        f"Missing or incomplete {path.name}: {last_error}; "
        + console.output.decode("utf-8", "replace"),
    )


def _scripts(root, mode, entry):
    fixture = Path(__file__).with_name("_terminal_console_fixture.py").resolve()
    shell = shutil.which("pwsh")
    assert shell, "PowerShell 7 is required for native handoff qualification"
    host = root / "host.ps1"
    nested = root / "nested.ps1"
    selected = root / "selected \u65e5.ps1"
    literal = ["spaces here", 'quote" and \'', "", ";$()&", "\u65e5", "--", "-literal"]
    (root / "literal.json").write_text(json.dumps(literal), encoding="utf-8")
    common = (
        "$ErrorActionPreference='Stop'\n$PSNativeCommandUseErrorActionPreference=$true\n. "
        + _quote(HELPER) + "\n"
    )
    invocation = (
        f"Invoke-WorktreeTerminalLaunch -Executable {_quote(sys.executable)} "
        f"-Arguments @('-I','-B',{_quote(fixture)},{_quote(root)},'inner',{_quote(mode)}) "
        f"-QueryArguments @('-I','-B',{_quote(fixture)},'query') -Package worktree_manager\n"
    )
    from _manager_process_fixture import synthetic_runtime

    manager = root / "manager"
    program = (
        "import runpy,sys\n"
        "from worktree_manager import terminal_launch\n"
        "if terminal_launch.answer_query(): raise SystemExit(0)\n"
        + ("assert sys.argv[1:]==['--resident-picker','--project','fixture']\n"
           if entry == "aw-resident" else "") +
        f"sys.argv={[str(fixture), str(root), 'inner', mode]!r}\n"
        f"runpy.run_path({str(fixture)!r}, run_name='__main__')\n"
    )
    slot = synthetic_runtime(manager, "fixture", program=program)
    selected_command = [
        str(slot / "Scripts" / "python.exe"), "-I", "-B", "-X", "utf8",
        "-S", str(slot / ".runtime-bootstrap" / "runtime_launcher.py"),
        "-m", "worktree_manager",
    ]
    if entry in {"manager-cmd", "manager-standalone"}:
        from worktree_manager import runtime_launchers

        _query_owner(slot / "payload")
        (manager / "current-version").write_text("fixture\n", encoding="utf-8")
        for kind in ("cmd", "ps1"):
            (root / f"worktree-manager.{kind}").write_text(
                getattr(runtime_launchers, f"_{kind}_binstub")(), encoding="utf-8",
            )
        kind = "ps1" if entry == "manager-standalone" else "cmd"
        invocation = f"& {_quote(root / ('worktree-manager.' + kind))}\n"
    nested.write_text(
        common +
        "if(@($args) -contains '--version'){\n"
        + ("'worktree-manager 0.1.0-dev21'\nexit 0\n" if mode == "legacy" else
           f"& {_quote(selected_command[0])} "
           + " ".join(_quote(part) for part in [*selected_command[1:], "--version"])
           + "\nexit $LASTEXITCODE\n") +
        "}\n"
        f"[IO.File]::WriteAllText({_quote(root / 'nested.json')},"
        "(@{pid=$PID}|ConvertTo-Json -Compress))\n"
        + (
            f"& {_quote(sys.executable)} -I -B {_quote(fixture)} {_quote(root)} inner legacy\n"
            if mode == "legacy" else invocation
        ) + "exit $LASTEXITCODE\n", encoding="utf-8",
    )
    if entry == "aw-selector-race":
        text = nested.read_text(encoding="utf-8")
        text = text.replace(
            "\nexit $LASTEXITCODE\n}\n",
            f"\n[IO.File]::WriteAllText({_quote(manager / 'current-version')},'legacy')\n"
            "exit $LASTEXITCODE\n}\n",
            1,
        )
        text = text[:text.index(f"[IO.File]::WriteAllText({_quote(root / 'nested.json')}")] + (
            "throw 'The Manager selector was executed again after capability admission'\n"
        )
        nested.write_text(text, encoding="utf-8")
    outer_invocation = (
        f"Invoke-WorktreeTerminalLaunch -Executable {_quote(sys.executable)} "
        f"-Arguments @('-I','-B',{_quote(fixture)},{_quote(root)},'outer',{_quote(mode)}) "
        f"-QueryArguments @('-I','-B',{_quote(fixture)},'query') -Package worktree_manager\n"
    )
    if entry.startswith("aw-"):
        outer_invocation = f"& {_quote(_aw_front_door(root, entry, fixture))}\n"
    elif entry in {"manager-standalone", "manager-cmd"}:
        outer_invocation = invocation
    if entry == "manager-cmd":
        outer_invocation = (
            "$savedNativePreference=$PSNativeCommandUseErrorActionPreference\n"
            "$PSNativeCommandUseErrorActionPreference=$false\n"
            + outer_invocation +
            "$PSNativeCommandUseErrorActionPreference=$savedNativePreference\n"
        )
    host.write_text(
        common + outer_invocation +
        "$code=$LASTEXITCODE\n"
        f"[IO.File]::WriteAllText({_quote(root / 'host-result.json')},"
        "(@{code=$code;channel=$env:CE_TERMINAL_LAUNCH;pid=$PID;"
        "nativePreference=$PSNativeCommandUseErrorActionPreference;"
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
    ("selected", "manager-standalone"), ("selected", "aw-user"), ("selected", "aw-payload"),
    ("selected", "manager-cmd"), ("selected", "aw-selector-race"), ("selected", "aw-resident"),
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
        assert not (tmp_path / "nested.json").exists(), "Pinned execution must bypass the selector"
        if entry == "aw-selector-race":
            assert (tmp_path / "manager" / "current-version").read_text() == "legacy"
        descendants = console.pin_descendants()
        workers = {ready["pid"], ready["outer"]} - {0}
        assert workers <= descendants.keys()
        assert sum(name.lower().startswith("python") for name, _ in descendants.values()) >= len(workers)
        assert all(not console.exited(handle) for _, handle in descendants.values())
        request = Path(ready["request"])
        (tmp_path / "release").touch()
        if mode in {"selected", "terminal-close"}:
            attached = _wait(tmp_path / "attached.json", console)
            if entry == "manager-cmd":
                assert attached["pid"] in descendants
                assert descendants[attached["pid"]][0].lower() == "pwsh.exe"
            else:
                assert attached["pid"] == console.process.pid
            survivors = {
                pid: name for pid, (name, handle) in descendants.items()
                if not console.exited(handle)
            }
            if entry == "manager-cmd":
                assert survivors[attached["pid"]].lower() == "pwsh.exe"
                assert not any(name.lower().startswith("python") for name in survivors.values())
                assert all(name.lower() in {"cmd.exe", "pwsh.exe"} for name in survivors.values())
            else:
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
        assert console.wait() == {"selected": 23, "cancel": 0, "failed": 7, "malformed": 1}[mode], (
            console.output.decode("utf-8", "replace")
        )
        result = json.loads((tmp_path / "host-result.json").read_text(encoding="utf-8-sig"))
        assert not result["channel"]
        assert result["nativePreference"] is True
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


def test_terminal_host_preserves_empty_literal_preparation_arguments(tmp_path):
    from _terminal_console_fixture import Console

    capture = tmp_path / "captured.json"
    code = (
        "import json,os,pathlib,sys;"
        "pathlib.Path(sys.argv[1]).write_text(json.dumps({"
        "'arguments':sys.argv[2:],'channel':os.environ.get('CE_TERMINAL_LAUNCH')}),"
        "encoding='utf-8');sys.exit(7)"
    )
    literal = ["", "two words", 'quote" here', "\u65e5", "--", ";$()"]
    arguments = ["-I", "-B", "-c", code, str(capture), *literal]
    script = tmp_path / "preparation.ps1"
    script.write_text(
        "$ErrorActionPreference='Stop'\n$PSNativeCommandUseErrorActionPreference=$true\n"
        f". {_quote(HELPER)}\n"
        f"$arguments=@({','.join(_quote(value) for value in arguments)})\n"
        f"Invoke-WorktreeTerminalLaunch -Executable {_quote(sys.executable)} -Arguments $arguments "
        f"-QueryArguments @('-I','-B',{_quote(Path(__file__).with_name('_terminal_console_fixture.py'))},'query') "
        "-Package worktree_manager\n"
        "exit $LASTEXITCODE\n", encoding="utf-8",
    )
    env = dict(os.environ, TEMP=str(tmp_path), TMP=str(tmp_path))
    env.pop("CE_TERMINAL_LAUNCH", None)
    shell = shutil.which("pwsh")
    assert shell, "PowerShell 7 is required"
    with Console([shell, "-NoProfile", "-File", str(script)], env=env, cwd=tmp_path) as console:
        result = _wait(capture, console)
        assert console.wait() == 7
    assert result["arguments"] == literal
    if result["channel"]:
        assert not Path(result["channel"]).parent.exists()


@pytest.mark.parametrize("response", ["current", "legacy", "malformed", "nonzero", "stale"])
def test_query_admission_preserves_legacy_without_leaking_an_offer(tmp_path, response):
    from _terminal_console_fixture import Console

    capture = tmp_path / "preparation.json"
    outcome = tmp_path / "outcome.json"
    code = (
        "import json,os,pathlib;from worktree_manager import terminal_launch;"
        f"pathlib.Path({str(capture)!r}).write_text(json.dumps({{"
        "'private':terminal_launch.available(),"
        "'inherited':os.environ.get('CE_TERMINAL_LAUNCH')}))"
    )
    query = {
        "current": "@('-I','-B','-m','worktree_manager','--version')",
        "legacy": "@('-I','-B','-c','print(\"worktree-manager 0.1.0-dev21\")')",
        "malformed": "@('-I','-B','-c','print(\"{}\")')",
        "nonzero": "@('-I','-B','-c','raise SystemExit(7)')",
        "stale": "@('-I','-B','-m','worktree_manager','--version')",
    }[response]
    host = tmp_path / "admission.ps1"
    host.write_text(
        "$ErrorActionPreference='Stop'\n"
        f". {_quote(HELPER)}\n"
        f"Invoke-WorktreeTerminalLaunch -Executable {_quote(sys.executable)} "
        f"-Arguments @('-I','-B','-c',{_quote(code)}) -QueryArguments {query} -Package worktree_manager\n"
        f"[IO.File]::WriteAllText({_quote(outcome)},"
        "(@{code=$LASTEXITCODE;channel=$env:CE_TERMINAL_LAUNCH}|ConvertTo-Json -Compress))\n"
        "exit $LASTEXITCODE\n", encoding="utf-8",
    )
    environment = dict(os.environ, TEMP=str(tmp_path), TMP=str(tmp_path))
    environment.pop("CE_TERMINAL_LAUNCH", None)
    if response == "stale":
        environment["CE_TERMINAL_LAUNCH"] = str(tmp_path / "absent-request.json")
    shell = shutil.which("pwsh")
    assert shell
    with Console([shell, "-NoProfile", "-File", str(host)], env=environment, cwd=tmp_path) as console:
        assert console.wait() == (0 if response in {"current", "legacy"} else 1)
    result = json.loads(outcome.read_text(encoding="utf-8-sig"))
    assert not result["channel"]
    assert list(tmp_path.glob("ce-terminal-*")) == []
    if response in {"current", "legacy"}:
        observed = json.loads(capture.read_text())
        assert observed == {"private": response == "current", "inherited": None}
    else:
        assert not capture.exists()


def test_current_worktrees_does_not_forward_a_channel_to_a_legacy_manager(tmp_path):
    from _terminal_console_fixture import Console

    argv, _ = _scripts(tmp_path, "legacy", "helper")
    environment = dict(os.environ, TEMP=str(tmp_path), TMP=str(tmp_path))
    environment.pop("CE_TERMINAL_LAUNCH", None)
    with Console(argv, env=environment, cwd=tmp_path) as console:
        assert console.wait() == 0
    assert json.loads((tmp_path / "legacy.json").read_text()) == {"channel": False, "inherited": None}
    assert not (tmp_path / "prepared.json").exists()
    assert list(tmp_path.glob("ce-terminal-*")) == []


def test_retired_runtime_after_query_cannot_reselect_or_execute_the_receiver(tmp_path, monkeypatch):
    from _manager_process_fixture import synthetic_runtime
    from agent_procutil.owned_process import run_caller_owned

    monkeypatch.syspath_prepend(str(HELPER.parents[2] / "plugins" / "agent-worktrees" / "src"))
    from agent_worktrees import terminal_launch

    unexpected = tmp_path / "unexpected-receiver"
    program = (
        "from worktree_manager import terminal_launch\n"
        "if terminal_launch.answer_query(): raise SystemExit(0)\n"
        f"from pathlib import Path; Path({str(unexpected)!r}).touch()\n"
    )
    root = tmp_path / "manager"
    slot = synthetic_runtime(root, "selected", program=program)
    command = [
        str(slot / "Scripts" / "python.exe"), "-I", "-B", "-X", "utf8",
        "-S", str(slot / ".runtime-bootstrap" / "runtime_launcher.py"),
        "-m", "worktree_manager",
    ]
    monkeypatch.setattr(terminal_launch, "_channel", "foreground-request")
    selected, environment = terminal_launch.select_receiver(
        command, dict(os.environ, WORKTREE_MANAGER_ROOT=str(root)),
    )
    assert selected == command
    assert environment["CE_TERMINAL_LAUNCH"] == "foreground-request"
    (slot / ".install-complete.json").unlink()
    result = run_caller_owned(
        [*selected, "--project", "fixture"], env=environment, timeout=10,
        max_stdout_bytes=4096, max_stderr_bytes=4096,
    )
    assert result.returncode != 0
    assert "requires attributable completion" in result.stderr
    assert not unexpected.exists()


def test_legacy_forwarder_can_call_a_current_manager_without_inheriting_its_offer(tmp_path):
    from _terminal_console_fixture import Console

    _scripts(tmp_path, "selected", "manager-standalone")
    shell = shutil.which("pwsh")
    assert shell
    old_pid = tmp_path / "legacy-forwarder.json"
    command = [shell, "-NoProfile", "-File", str(tmp_path / "worktree-manager.ps1")]
    program = (
        "import json,os,pathlib,sys;"
        "from agent_procutil import spawn_sync_in_kill_on_close_job;"
        f"pathlib.Path({str(old_pid)!r}).write_text(json.dumps({{'pid':os.getpid(),"
        "'channel':os.environ.get('CE_TERMINAL_LAUNCH')}));"
        f"process,job=spawn_sync_in_kill_on_close_job({command!r});"
        "code=process.wait();job.close();sys.exit(code)"
    )
    environment = dict(
        os.environ, WORKTREE_MANAGER_ROOT=str(tmp_path / "manager"),
        FIXTURE_KEPT="original", FIXTURE_REMOVED="original", TEMP=str(tmp_path), TMP=str(tmp_path),
    )
    environment.pop("CE_TERMINAL_LAUNCH", None)
    with Console(
        [sys.executable, "-I", "-B", "-c", program], env=environment, cwd=tmp_path,
    ) as console:
        ready = _wait(tmp_path / "prepared.json", console)
        legacy = _wait(old_pid, console)
        assert legacy["channel"] is None
        forwarder = console.pin(legacy["pid"])
        (tmp_path / "release").touch()
        attached = _wait(tmp_path / "attached.json", console)
        assert not attached["channel"]
        assert not Path(ready["request"]).parent.exists()
        # An unsupported outer forwarder intentionally retains its legacy wait.
        assert not console.exited(forwarder)
        console.send(b"legacy input\r")
        assert console.wait() == 23
        assert console.exited(forwarder, 1000)
    assert list(tmp_path.glob("ce-terminal-*")) == []


def test_repeated_foreground_calls_cannot_reuse_a_completed_offer(tmp_path):
    from _terminal_console_fixture import Console

    capture = tmp_path / "offers.jsonl"
    code = (
        "import json,os,pathlib;from worktree_manager import terminal_launch;"
        f"output=pathlib.Path({str(capture)!r}).open('a');"
        "output.write(json.dumps({'request':terminal_launch._channel,"
        "'environment':os.environ.get('CE_TERMINAL_LAUNCH')})+'\\n');output.close()"
    )
    host = tmp_path / "repeated.ps1"
    host.write_text(
        "$ErrorActionPreference='Stop'\n"
        f". {_quote(HELPER)}\n"
        "foreach($iteration in 1..2){\n"
        f"Invoke-WorktreeTerminalLaunch -Executable {_quote(sys.executable)} "
        f"-Arguments @('-I','-B','-c',{_quote(code)}) "
        "-QueryArguments @('-I','-B','-m','worktree_manager','--version') -Package worktree_manager\n"
        "if($LASTEXITCODE -ne 0 -or $env:CE_TERMINAL_LAUNCH){throw 'foreground state leaked'}\n"
        "}\n", encoding="utf-8",
    )
    environment = dict(os.environ, TEMP=str(tmp_path), TMP=str(tmp_path))
    environment.pop("CE_TERMINAL_LAUNCH", None)
    shell = shutil.which("pwsh")
    assert shell
    with Console([shell, "-NoProfile", "-File", str(host)], env=environment, cwd=tmp_path) as console:
        assert console.wait() == 0
    offers = [json.loads(line) for line in capture.read_text().splitlines()]
    assert len(offers) == 2 and offers[0]["request"] != offers[1]["request"]
    assert all(not item["environment"] and not Path(item["request"]).parent.exists() for item in offers)
    assert list(tmp_path.glob("ce-terminal-*")) == []


def test_cold_and_warm_queries_retire_all_observed_transient_children(tmp_path, capsys):
    from _terminal_console_fixture import Console

    outcome = tmp_path / "measurements.json"
    host = tmp_path / "queries.ps1"
    host.write_text(
        "$ErrorActionPreference='Stop'\n"
        f". {_quote(HELPER)}\n"
        "$results=@()\n"
        "foreach($phase in @('cold','warm')){\n"
        "$clock=[Diagnostics.Stopwatch]::StartNew()\n"
        f"$supported=Get-WorktreeTerminalCapability {_quote(sys.executable)} "
        "@('-I','-B','-m','worktree_manager','--version') worktree_manager\n"
        "$results+=@{phase=$phase;supported=$supported;milliseconds=$clock.Elapsed.TotalMilliseconds}\n"
        "}\n"
        f"[IO.File]::WriteAllText({_quote(outcome)},(ConvertTo-Json -InputObject $results -Compress))\n"
        "[Console]::ReadLine()|Out-Null\n", encoding="utf-8",
    )
    environment = dict(os.environ)
    environment.pop("CE_TERMINAL_LAUNCH", None)
    shell = shutil.which("pwsh")
    assert shell
    observations = {}
    with Console([shell, "-NoProfile", "-File", str(host)], env=environment, cwd=tmp_path) as console:
        deadline = time.monotonic() + 30
        while not outcome.exists():
            assert not console.exited(console.process.process), console.output.decode("utf-8", "replace")
            assert time.monotonic() < deadline
            try:
                children = console.pin_descendants()
            except OSError as error:
                if error.winerror not in {87, 1168}:
                    raise
            else:
                for pid, (name, handle) in children.items():
                    observations[(pid, console.birth(handle))] = (name, handle)
            time.sleep(.005)
        measurements = _wait(outcome, console)
        assert len(measurements) == 2 and all(item["supported"] for item in measurements)
        assert observations, "the sampler did not observe the real query workers"
        assert all(console.exited(handle, 1000) for _, handle in observations.values())
        console.send(b"\r")
        assert console.wait() == 0
    with capsys.disabled():
        print(json.dumps({
            "terminal_query_measurements": measurements,
            "observed_transient_processes": [name for name, _ in observations.values()],
            "sampling": "birth-pinned lower bound; short-lived trampolines may be missed",
        }))
