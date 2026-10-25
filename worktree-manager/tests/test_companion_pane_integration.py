"""Bounded entry, runtime ownership, and native CLI pane registration."""

from __future__ import annotations

import base64
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from agent_procutil import no_window_kwargs

from worktree_manager import companion_runtime, mux_daemon, mux_daemon_cli, mux_mapping_registry
from worktree_manager.__main__ import main

from _manager_process_fixture import synthetic_runtime

BIN = Path(__file__).resolve().parents[1] / "bin"


@pytest.mark.parametrize("action", ["show", "return"])
def test_companion_pane_dispatches_explicit_action(monkeypatch, action):
    calls = []
    monkeypatch.setitem(sys.modules, "worktree_manager.companion_pane", SimpleNamespace(
        run=lambda **kwargs: calls.append(kwargs) or 19,
    ))
    assert main(["companion-pane", "--action", action, "--cwd", "workspace with spaces"]) == 19
    assert calls == [{"action": action, "cwd": "workspace with spaces"}]


@pytest.mark.parametrize("arguments", [[], ["--action", "delete"], ["--action", "show", "--other"]])
def test_companion_pane_rejects_unbounded_actions(arguments):
    with pytest.raises(SystemExit) as raised:
        main(["companion-pane", *arguments])
    assert raised.value.code == 2


def test_legacy_popup_dispatch_is_unchanged(monkeypatch):
    calls = []
    monkeypatch.setitem(sys.modules, "worktree_manager.mux_companion", SimpleNamespace(
        run=lambda: calls.append(True) or 7,
    ))
    assert main(["companion"]) == 7
    assert calls == [True]


def test_companion_command_pins_runtime_and_scrubs_credentials(tmp_path, monkeypatch):
    prefix = tmp_path / "versions" / "1.2.3"
    (prefix / ".runtime-bootstrap").mkdir(parents=True)
    (prefix / ".runtime-bootstrap" / "runtime_launcher.py").touch()
    (prefix / ".install-complete.json").touch()
    python = prefix / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    python.parent.mkdir()
    python.touch()
    monkeypatch.setattr(sys, "prefix", str(prefix))
    names = ("GH_TOKEN", "GITHUB_TOKEN", "AGENT_WORKTREES_AHP_AUTH_TOKEN",
             "PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV", "__PYVENV_LAUNCHER__")
    for name in names:
        monkeypatch.setenv(name, "untrusted")
    tail = ["-m", "worktree_manager.mux_companion", "--cli-pane", "%42", "--cwd", "space path"]
    command, env = companion_runtime.companion_command(tail)
    assert command == [str(python), "-I", "-S", "-B", "-X", "utf8",
                       str(prefix / ".runtime-bootstrap" / "runtime_launcher.py"), *tail]
    assert all(name not in env for name in names)
    assert env["WORKTREE_MANAGER_ROOT"] == str(tmp_path)


@pytest.mark.parametrize("source", [False, True])
def test_companion_command_never_falls_back_to_source(tmp_path, monkeypatch, source):
    prefix = tmp_path / (".venv" if source else "versions/1.2.3")
    prefix.mkdir(parents=True)
    monkeypatch.setattr(sys, "prefix", str(prefix))
    with pytest.raises(RuntimeError, match="constructed"):
        companion_runtime.companion_command(["-m", "worktree_manager", "companion-pane"])


def test_companion_shell_command_preserves_quoted_native_argv(monkeypatch):
    command = ["C:\\Program Files\\Python\\python.exe", "-m", "worktree_manager.mux_companion",
               "--cwd", "C:\\O'Brien\\workspace with spaces", "--cli-pane", "%42"]
    monkeypatch.setattr(companion_runtime, "companion_command", lambda arguments: (
        command, {"WORKTREE_MANAGER_ROOT": "C:\\O'Brien\\Manager root"},
    ))
    shell_command = companion_runtime.companion_shell_command(["fixture"])
    if os.name == "nt":
        script = base64.b64decode(shell_command.split()[-1]).decode("utf-16-le")
        assert "'C:\\O''Brien\\workspace with spaces'" in script
        assert "& 'C:\\Program Files\\Python\\python.exe'" in script
        assert "$env:GH_TOKEN=$null" in script
        assert "$env:WORKTREE_MANAGER_ROOT='C:\\O''Brien\\Manager root'" in script
    else:
        import shlex
        assert shlex.split(shell_command)[-len(command):] == command


@pytest.mark.parametrize("pane", ["%42", "%0"])
def test_register_cli_pane_uses_native_identity(tmp_path, monkeypatch, capsys, pane):
    monkeypatch.setattr(mux_mapping_registry, "default_root", lambda: tmp_path)
    monkeypatch.setattr(mux_daemon, "ensure_daemon_running", lambda *args, **kwargs: True)
    monkeypatch.setattr(mux_daemon, "publish_live_observation", lambda *args, **kwargs: {"applied": True})
    assert main(["mux-daemon", "register", "--project=example", "--worktree-id=one",
                 "--mux-session=managed", "--mux-bin=tmux", "--mapping-revision=1",
                 "--worktree-path=workspace", "--session-incarnation=$7:100",
                 f"--cli-pane={pane}"]) == 0
    capsys.readouterr()
    entry = mux_mapping_registry.get_mapping("example", "one", root=tmp_path)
    assert entry["panes"] == [{"pane_id": pane, "role": "cli", "live": True}]
    assert entry["session_incarnation"] == "$7:100"
    assert entry["worktree_path"] == "workspace"


@pytest.mark.parametrize("pane", ["0", "session:0.0", "%bad", "%"])
def test_register_rejects_non_native_cli_identity(pane):
    assert main(["mux-daemon", "register", f"--cli-pane={pane}"]) == 2


PROGRAM = """
import json, os, sys
print(json.dumps({'arguments':sys.argv[1:], 'root':os.environ.get('WORKTREE_MANAGER_ROOT'),
                  'prefix':sys.prefix,
                  'credentials':[key for key in ('GH_TOKEN', 'GITHUB_TOKEN',
                    'AGENT_WORKTREES_AHP_AUTH_TOKEN', 'PYTHONPATH', 'PYTHONHOME',
                    'VIRTUAL_ENV', '__PYVENV_LAUNCHER__') if key in os.environ]}))
raise SystemExit(17)
"""


@pytest.mark.parametrize("action", ["show", "return"])
def test_installed_companion_pane_wrapper_uses_bootstrap_without_uv(tmp_path, action):
    if os.name == "nt":
        shell = shutil.which("pwsh")
    else:
        shell = shutil.which("bash")
    if not shell:
        pytest.skip("native wrapper shell unavailable")
    root = tmp_path / "Manager with spaces"
    slot = synthetic_runtime(root, "1.2.3", program=PROGRAM)
    env = dict(os.environ, GH_TOKEN="fixture", GITHUB_TOKEN="fixture",
               AGENT_WORKTREES_AHP_AUTH_TOKEN="fixture", PYTHONPATH="foreign",
               VIRTUAL_ENV="foreign", __PYVENV_LAUNCHER__=sys.executable)
    # The interpreter and wrapper shell are absolute; no uv is available.
    env["PATH"] = str(Path(os.environ["SystemRoot"]) / "System32") if os.name == "nt" else "/usr/bin:/bin"
    if os.name == "nt":
        command = [shell, "-NoProfile", "-File", str(slot / "payload" / "bin" / "manager-runtime.ps1"),
                   "-CompanionPane", "-Action", action, "-Cwd", str(tmp_path)]
    else:
        command = [shell, "-c", 'source "$1"; aw_manager_companion_pane "$2" "$3" "$4"', "fixture",
                   str(slot / "payload" / "bin" / "manager-runtime.sh"),
                   str(slot / "payload"), action, str(tmp_path)]
    result = subprocess.run(command, env=env, capture_output=True, text=True,
                            timeout=25, **no_window_kwargs())
    assert result.returncode == 17, result.stdout + result.stderr
    captured = json.loads(result.stdout)
    assert captured["arguments"] == ["companion-pane", "--action", action, "--cwd", str(tmp_path)]
    assert Path(captured["prefix"]).resolve() == slot.resolve()
    assert Path(captured["root"]).resolve() == root.resolve()
    assert captured["credentials"] == []


def test_companion_pane_wrapper_refuses_source_runtime(tmp_path):
    shell = shutil.which("pwsh" if os.name == "nt" else "bash")
    if not shell:
        pytest.skip("native wrapper shell unavailable")
    payload = tmp_path / "source payload"
    (payload / "bin").mkdir(parents=True)
    name = "manager-runtime.ps1" if os.name == "nt" else "manager-runtime.sh"
    wrapper = payload / "bin" / name
    shutil.copyfile(BIN / name, wrapper)
    if os.name == "nt":
        command = [shell, "-NoProfile", "-File", str(wrapper),
                   "-CompanionPane", "-Action", "show", "-Cwd", str(tmp_path)]
    else:
        command = [shell, "-c", 'source "$1"; aw_manager_companion_pane "$2" show "$3"',
                   "fixture", str(wrapper), str(payload), str(tmp_path)]
    result = subprocess.run(command, capture_output=True, text=True, timeout=15, **no_window_kwargs())
    assert result.returncode != 0
    if os.name == "nt":
        assert "constructed Manager runtime" in result.stderr


def test_companion_shell_command_executes_payload_with_apostrophes(tmp_path, monkeypatch):
    shell = shutil.which("pwsh" if os.name == "nt" else "bash")
    if not shell:
        pytest.skip("native shell unavailable")
    slot = synthetic_runtime(tmp_path / "O'Brien Manager root", "1.2.3", program=PROGRAM)
    monkeypatch.setattr(sys, "prefix", str(slot))
    monkeypatch.setattr(sys, "executable", str(slot / ("Scripts/python.exe" if os.name == "nt" else "bin/python")))
    monkeypatch.setenv("GH_TOKEN", "fixture")
    tail = ["-m", "worktree_manager", "companion-pane", "--action", "show",
            "--cwd", str(tmp_path / "O'Brien workspace")]
    command = companion_runtime.companion_shell_command(tail)
    if os.name == "nt":
        # Execute the exact encoded payload psmux will give its default shell.
        argv = [shell, *command.split()[1:]]
    else:
        argv = [shell, "-c", command]
    result = subprocess.run(argv, capture_output=True, text=True, timeout=25, **no_window_kwargs())
    assert result.returncode == 17, result.stdout + result.stderr
    captured = json.loads(result.stdout)
    assert captured["arguments"] == tail[2:]
    assert captured["credentials"] == []
    assert Path(captured["prefix"]).resolve() == slot.resolve()


def test_native_cli_registration_does_not_guess_on_reconnect():
    for filename in ("launch-session.ps1", "launch-session.sh"):
        source = (BIN / filename).read_text(encoding="utf-8")
        assert "--cli-pane=" in source
        assert "@aw_cli" not in source
    ps = (BIN / "launch-session.ps1").read_text(encoding="utf-8")
    sh = (BIN / "launch-session.sh").read_text(encoding="utf-8")
    assert ps.count("Invoke-ManagedMuxRegister $sessName $muxStatusPath -Created") == 1
    assert sh.count('_aw_publish_managed_mux_live "$TMUX_SESS" "${STATUS_PATH:-${WORK_DIR:-$PWD}}" created') == 1


@pytest.mark.parametrize(("created", "expected"), [(True, "%42"), (False, None)])
def test_native_launcher_captures_cli_only_at_creation(tmp_path, created, expected):
    shell = shutil.which("pwsh")
    if not shell:
        pytest.skip("PowerShell unavailable")
    fixture_bin = tmp_path / "payload" / "bin"
    fixture_bin.mkdir(parents=True)
    (fixture_bin / "manager-runtime.ps1").write_text(
        "function Invoke-ManagerMuxCallback { param($PayloadRoot, $CallbackArguments)\n"
        "ConvertTo-Json -Compress -InputObject @($CallbackArguments)\n}\n", encoding="utf-8",
    )
    (fixture_bin / "fake-mux.ps1").write_text(
        "switch ($args[0]) {\n"
        " 'display-message' {\n"
        "  if ($args[-1] -eq '#{pane_id}') {\n"
        "   if ($env:FIXTURE_CREATED -ne '1') { throw 'Cannot guess the active CLI pane' }\n"
        "   '%42'\n"
        "  } else { '$7:100' }\n"
        " }\n"
        " default { throw 'Pane options and active-pane inference are unavailable' }\n"
        "}\n", encoding="utf-8",
    )
    launch = (BIN / "launch-session.ps1").read_text(encoding="utf-8")
    function = launch[launch.index("function Invoke-ManagedMuxRegister {"):launch.index("# Deliver a worktree")]
    registration = fixture_bin / "registration.ps1"
    registration.write_text(
        "$ErrorActionPreference='Stop'\n"
        "$script:LaunchProject='example'\n"
        "$plan=@{worktree_id='one'}\n"
        "$script:AwPsmuxBin=Join-Path $PSScriptRoot 'fake-mux.ps1'\n"
        "function Write-SetupLog { throw ($args -join ' ') }\n"
        + function + "\nInvoke-ManagedMuxRegister 'managed' 'workspace'"
        + (" -Created" if created else "") + "\n", encoding="utf-8",
    )
    result = subprocess.run(
        [shell, "-NoProfile", "-File", str(registration)],
        env=dict(os.environ, FIXTURE_CREATED="1" if created else "0"),
        capture_output=True, text=True, timeout=15, **no_window_kwargs(),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    arguments = json.loads(result.stdout)
    assert "--project=example" in arguments
    assert "--session-incarnation=$7:100" in arguments
    assert [arg for arg in arguments if arg.startswith("--cli-pane=")] == (
        [f"--cli-pane={expected}"] if expected else []
    )


@pytest.mark.skipif(os.name == "nt", reason="native POSIX wrapper lane")
@pytest.mark.parametrize(("created", "expected"), [(True, "%42"), (False, None)])
def test_posix_launcher_captures_cli_only_at_creation(tmp_path, created, expected):
    shell = shutil.which("bash")
    if not shell:
        pytest.skip("Bash unavailable")
    fixture_bin = tmp_path / "payload" / "bin"
    fixture_bin.mkdir(parents=True)
    (fixture_bin / "manager-runtime.sh").write_text(
        'aw_manager_callback() { printf "%s\\n" "$@" > "$FIXTURE_CAPTURE"; }\n', encoding="utf-8",
    )
    launch = (BIN / "launch-session.sh").read_text(encoding="utf-8")
    function = launch[launch.index("        _aw_publish_managed_mux_live() {"):
                      launch.index("        # Deliver a worktree")]
    registration = fixture_bin / "registration.sh"
    registration.write_text(
        "set -eu\nLAUNCH_PROJECT=example\nWORKTREE_ID=one\n"
        "tmux() {\n"
        ' case "$1" in\n'
        "  display-message)\n"
        '   if [[ "${@: -1}" == "#{pane_id}" ]]; then\n'
        '    [[ "$FIXTURE_CREATED" == 1 ]] || return 1\n'
        "    printf '%s\\n' '%42'\n"
        "   else printf '%s\\n' '$7:100'; fi ;;\n"
        "  *) return 1 ;;\n"
        " esac\n}\n"
        + function + "\n_aw_publish_managed_mux_live managed workspace"
        + (" created" if created else "") + "\n", encoding="utf-8",
    )
    capture = tmp_path / "callback-arguments"
    result = subprocess.run(
        [shell, str(registration)],
        env=dict(os.environ, FIXTURE_CREATED="1" if created else "0",
                 FIXTURE_CAPTURE=str(capture)),
        capture_output=True, text=True, timeout=15,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    arguments = capture.read_text(encoding="utf-8").splitlines()
    assert "--project=example" in arguments
    assert "--session-incarnation=$7:100" in arguments
    assert [arg for arg in arguments if arg.startswith("--cli-pane=")] == (
        [f"--cli-pane={expected}"] if expected else []
    )


@pytest.mark.parametrize("case", [
    "same", "new-incarnation", "new-session", "new-path", "new-binary",
    "missing", "dead", "duplicate", "native-wrong-incarnation",
    "unavailable", "ambiguous-incumbent", "no-incumbent", "missing-incarnation",
])
def test_reconnect_preserves_only_proven_incumbent_cli(monkeypatch, case):
    payload = {
        "project": "example", "worktree_id": "one", "worktree_path": "workspace",
        "mux_bin": "psmux", "mux_session": "managed", "session_incarnation": "$7:100",
    }
    incumbent = dict(payload, live=True, panes=[{"pane_id": "%42", "role": "cli", "live": True}])
    row = "%42\t0\tmanaged\t$7:100\n"
    if case.startswith("new-"):
        field = {"new-incarnation": "session_incarnation", "new-session": "mux_session",
                 "new-path": "worktree_path", "new-binary": "mux_bin"}[case]
        payload[field] = "$8:200" if case == "new-incarnation" else "different"
    elif case == "missing":
        row = "%99\t0\tmanaged\t$7:100\n"
    elif case == "dead":
        row = row.replace("\t0\t", "\t1\t")
    elif case == "duplicate":
        row += row
    elif case == "native-wrong-incarnation":
        row = row.replace("$7:100", "$8:200")
    elif case == "ambiguous-incumbent":
        incumbent["panes"].append({"pane_id": "%99", "role": "cli", "live": True})
    elif case == "missing-incarnation":
        payload["session_incarnation"] = ""
    monkeypatch.setattr(mux_daemon_cli, "get_mapping", lambda *args: (
        None if case == "no-incumbent" else incumbent
    ))
    calls = []

    def native(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(returncode=1 if case == "unavailable" else 0, stdout=row)

    monkeypatch.setattr(mux_daemon_cli, "run_mux_child", native)
    assert mux_daemon_cli._reconnect_cli_panes(payload) == (
        incumbent["panes"] if case in {"same", "unavailable", "duplicate"} else []
    )
    if calls:
        command, kwargs = calls[0]
        assert command[1:5] == ["list-panes", "-s", "-t", "=managed"]
        assert "session_path" not in command[-1]
        assert kwargs["timeout"] == 2


def test_register_reconnect_retains_cli_but_new_incarnation_clears_it(tmp_path, monkeypatch):
    from worktree_manager import mux_discovery

    monkeypatch.setattr(mux_mapping_registry, "default_root", lambda: tmp_path)
    monkeypatch.setattr(mux_daemon, "ensure_daemon_running", lambda *args, **kwargs: True)
    monkeypatch.setattr(mux_daemon, "publish_live_observation", lambda *args, **kwargs: {"applied": True})
    monkeypatch.setattr(mux_daemon_cli, "run_mux_child", lambda *args, **kwargs: SimpleNamespace(
        returncode=0, stdout="%42\t0\tmanaged\t$7:100\n",
    ))
    base = ["mux-daemon", "register", "--project=example", "--worktree-id=one",
            "--worktree-path=workspace", "--mux-session=managed", "--mux-bin=psmux"]
    assert main([*base, "--session-incarnation=$7:100", "--cli-pane=%42"]) == 0
    assert main([*base, "--session-incarnation=$7:100"]) == 0
    entry = mux_mapping_registry.get_mapping("example", "one", root=tmp_path)
    assert entry["panes"] == [{"pane_id": "%42", "role": "cli", "live": True}]
    monkeypatch.setattr(mux_discovery, "run_mux_child", lambda *args, **kwargs: SimpleNamespace(
        returncode=0, stdout="managed\t1\t$8:200\tworkspace\n",
    ))
    assert main([*base, "--session-incarnation=$8:200"]) == 0
    entry = mux_mapping_registry.get_mapping("example", "one", root=tmp_path)
    assert entry["session_incarnation"] == "$8:200"
    assert entry["panes"] == []
