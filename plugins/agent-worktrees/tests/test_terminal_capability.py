"""Selected-runtime capability responses, without session or daemon effects."""

import json
import os
import sys
from types import SimpleNamespace

import pytest

from agent_worktrees import terminal_launch
from agent_procutil import owned_process


@pytest.fixture
def channel(monkeypatch, tmp_path):
    request = tmp_path / "request.json"
    request.write_text(json.dumps({"version": 1, "nonce": "a" * 32, "host_pid": os.getpid()}))
    monkeypatch.setattr(terminal_launch, "_channel", str(request))
    return str(request)


def test_actual_query_entry_exits_before_roster_and_cli_imports():
    code = (
        "import builtins,runpy,sys\n"
        "original=builtins.__import__\n"
        "def guarded(name,*args,**kwargs):\n"
        " if name in ('current_roster_entry','argparse'): "
        "raise AssertionError('query entered the ordinary dispatcher')\n"
        " return original(name,*args,**kwargs)\n"
        "builtins.__import__=guarded\n"
        "sys.argv=['agent-worktrees','--version']\n"
        "runpy.run_module('agent_worktrees',run_name='__main__')\n"
    )
    result = owned_process.run_caller_owned(
        [sys.executable, "-I", "-B", "-c", code],
        env=dict(os.environ, CE_TERMINAL_CAPABILITY_QUERY="1"),
        timeout=10, max_stdout_bytes=4096, max_stderr_bytes=4096,
    )
    assert result.returncode == 0, result.stderr
    assert not result.stderr
    assert json.loads(result.stdout) == {
        "package": "agent_worktrees", "capability": terminal_launch.CAPABILITY,
    }


@pytest.mark.parametrize("response", ["current", "legacy", "malformed", "foreign", "failed"])
@pytest.mark.parametrize("resident", [False, True])
def test_foreground_channel_only_reaches_a_supported_selected_receiver(monkeypatch, response, resident, tmp_path, channel):
    monkeypatch.setattr(terminal_launch, "os", SimpleNamespace(name="nt"))
    command = ["selected-manager", "fixed-prefix"]
    selector = ["--resident-picker"] if resident else []
    invocation = [*command, *selector]
    pinned = [sys.executable, "-I", "-B", "-X", "utf8", "-m", "worktree_manager"]
    root = str(tmp_path / "selected-root")
    environment = {"PATH": "unchanged", "CE_TERMINAL_LAUNCH": "stale-request"}
    outputs = {
        "current": json.dumps({
            "package": "worktree_manager", "capability": terminal_launch.CAPABILITY,
            "runtime": {"command": pinned, "root": root},
        }),
        "legacy": "worktree-manager 0.1.0-dev21\n",
        "malformed": "{",
        "foreign": json.dumps({"package": "other", "capability": terminal_launch.CAPABILITY}),
        "failed": "",
    }

    def query(argv, **options):
        assert argv == [*command, "--version"]
        assert "CE_TERMINAL_LAUNCH" not in options["env"]
        assert options["env"]["UV_NO_SYNC"] == "1"
        assert options["timeout"] == 10
        assert options["max_stdout_bytes"] == options["max_stderr_bytes"] == 4096
        return SimpleNamespace(returncode=int(response == "failed"), stderr="", stdout=outputs[response])

    monkeypatch.setattr(owned_process, "run_caller_owned", query)
    if response in {"current", "legacy"}:
        selected, result = terminal_launch.select_receiver(invocation, environment)
        assert selected == ([*pinned, *selector] if response == "current" else invocation)
        assert result.get("CE_TERMINAL_LAUNCH") == (
            channel if response == "current" else None
        )
        assert result["PATH"] == "unchanged"
        assert environment["CE_TERMINAL_LAUNCH"] == "stale-request"
        assert result.get("WORKTREE_MANAGER_ROOT") == (root if response == "current" else None)
    else:
        with pytest.raises(RuntimeError):
            terminal_launch.select_receiver(invocation, environment)


def test_no_foreground_offer_does_not_probe_or_reuse_an_inherited_channel(monkeypatch):
    monkeypatch.setattr(terminal_launch, "_channel", None)
    monkeypatch.setattr(
        owned_process, "run_caller_owned",
        lambda *args, **kwargs: pytest.fail("non-foreground query"),
    )
    assert terminal_launch.select_receiver(["selected"], {"CE_TERMINAL_LAUNCH": "stale"}) == (["selected"], {})


@pytest.mark.parametrize("damage", ["no-binding", "shell", "extra-argument", "missing-python", "retired-slot", "root"])
def test_unavailable_or_unbound_runtime_cannot_receive_an_offer(monkeypatch, tmp_path, damage, channel):
    monkeypatch.setattr(terminal_launch, "os", SimpleNamespace(name="nt"))
    command = [sys.executable, "-I", "-B", "-X", "utf8", "-m", "worktree_manager"]
    response = {
        "package": "worktree_manager", "capability": terminal_launch.CAPABILITY,
        "runtime": {"command": command, "root": None},
    }
    if damage == "no-binding":
        response.pop("runtime")
    elif damage == "shell":
        command[-2:] = ["-c", "launch()"]
    elif damage == "extra-argument":
        command.append("--project")
    elif damage == "missing-python":
        command[0] = str(tmp_path / "removed-python")
    elif damage == "retired-slot":
        bootstrap = tmp_path / "versions" / "retired" / ".runtime-bootstrap" / "runtime_launcher.py"
        bootstrap.parent.mkdir(parents=True)
        bootstrap.write_text("", encoding="utf-8")
        command[5:5] = ["-S", str(bootstrap)]
    else:
        response["runtime"]["root"] = "relative-root"
    monkeypatch.setattr(
        owned_process, "run_caller_owned",
        lambda *args, **kwargs: SimpleNamespace(returncode=0, stderr="", stdout=json.dumps(response)),
    )
    with pytest.raises(RuntimeError):
        terminal_launch.select_receiver(["changing-selector"], {})


@pytest.mark.parametrize("damage", ["missing", "malformed", "consumed"])
def test_invalid_offer_never_queries_a_legacy_receiver_or_reaches_launch(channel, monkeypatch, damage):
    from pathlib import Path
    from agent_worktrees import __main__ as main

    request = Path(channel)
    if damage == "missing":
        request.unlink()
    elif damage == "malformed":
        request.write_text("{")
    else:
        (request.parent / "result.json").write_text("{}")
    monkeypatch.setattr(terminal_launch, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(
        owned_process, "run_caller_owned",
        lambda *_args, **_kwargs: pytest.fail("Invalid offer queried a legacy receiver"),
    )
    for consumer in (
        lambda: terminal_launch.select_receiver(["legacy-manager"], {}),
        lambda: main.cmd_launch([]),
    ):
        with pytest.raises(SystemExit) as stopped:
            consumer()
        assert stopped.value.code == 1
