from __future__ import annotations

import importlib.util
import io
import os
import shutil
import subprocess
import json
import sys
from pathlib import Path

import pytest


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


@pytest.fixture
def client(monkeypatch):
    monkeypatch.delenv("COPILOT_EXTENSIONS_CONTEXT", raising=False)
    spec = importlib.util.spec_from_file_location("relevance_client", SCRIPTS / "hook_client.py")
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    return module


def payload(tool, command=None):
    return {"toolName": tool, "cwd": str(SCRIPTS), "toolArgs": {"command": command}}


@pytest.mark.parametrize("event", [
    payload("view"), payload("rg"), payload("glob"),
    payload("powershell", "Get-Date"),
    payload("powershell", "Get-Item README.md"),
    payload("powershell", "Get-Content README.md"),
    payload("bash", "pwd"), payload("bash", "cat README.md"),
])
def test_irrelevant_pre_tool_never_reaches_ipc_or_policy(client, monkeypatch, tmp_path, event):
    def forbidden(*args, **kwargs):
        pytest.fail("irrelevant tool reached IPC, discovery, registry, or subprocess")

    monkeypatch.setattr(client, "_request", forbidden)
    monkeypatch.setattr(client.subprocess, "run", forbidden)
    for name in ("statelessness_guard.py", "cross_repo_guard.py", "anchor_write_guard.py", "pr_supersede_guard.py"):
        guard = client._load_sibling(name)
        assert guard is not None
        monkeypatch.setattr(guard, "decide", forbidden)
        for attr in ("find_repo_root", "load_guarded_roots", "load_worktree_anchors", "resolve_registry_root"):
            if hasattr(guard, attr):
                monkeypatch.setattr(guard, attr, forbidden)
    assert client.decide("preToolUse", event, home=tmp_path) == {}


@pytest.mark.parametrize("event", [
    payload("edit"), payload("create"), payload("apply_patch"),
    payload("powershell", "Set-Content file.txt value"),
    payload("powershell", "git checkout main"),
    payload("powershell", "git reset --hard"),
    payload("powershell", "git clean -fd"),
    payload("powershell", "git worktree add other"),
    payload("powershell", "git log --oneline"),
    payload("powershell", "git -c alias.sweep=clean sweep"),
    payload("powershell", "gh pr close 42 --repo example/project"),
    payload("powershell", "gh api repos/example/project/pulls/42 -f state=closed"),
    payload("powershell", "Get-Date; Set-Content file.txt value"),
    payload("powershell", "Get-Item (git reset --hard)"),
    payload("powershell", 'Get-Item "quoted path"'),
    payload("bash", "cat README.md > copy.txt"),
    payload("bash", "cat $(git clean -fd)"),
    payload("bash", "python -c 'print(1)'"),
    payload("powershell", None), payload("powershell", {"bad": "command"}),
    payload("unknown"), {},
])
def test_uncertain_or_interesting_pre_tool_preserves_resident_decision(client, monkeypatch, tmp_path, event):
    seen = []
    decision = {"permissionDecision": "deny", "permissionDecisionReason": "existing policy"}
    def resident(*args):
        seen.append(args)
        return decision
    monkeypatch.setattr(client, "_request", resident)
    assert client.decide("preToolUse", event, home=tmp_path) is decision
    assert len(seen) == 1


def test_installation_context_preserves_existing_admission(client, monkeypatch, tmp_path):
    monkeypatch.setenv("COPILOT_EXTENSIONS_CONTEXT", str(tmp_path / "context.json"))
    seen = []
    monkeypatch.setattr(client, "_request", lambda *args: seen.append(args) or {"permissionDecision": "deny"})
    assert client.decide("preToolUse", payload("view"), home=tmp_path)["permissionDecision"] == "deny"
    assert len(seen) == 1


def test_legacy_argument_aliases_use_real_predicate(client, monkeypatch, tmp_path):
    monkeypatch.setattr(client, "_request", lambda *args: pytest.fail("unexpected IPC"))
    assert client.decide("preToolUse", {
        "tool_name": "powershell", "tool_input": json.dumps({"command": "Get-Date"}),
    }, home=tmp_path) == {}


def test_actual_main_read_tool_skips_resident(client, monkeypatch, capsys):
    monkeypatch.setattr(client, "_request", lambda *args: pytest.fail("unexpected IPC"))
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload("view"))))
    assert client.main(["preToolUse"]) == 0
    assert capsys.readouterr().out == ""


def test_missing_relevance_helper_retains_policy(client, monkeypatch, tmp_path):
    loader = client._load_sibling
    monkeypatch.setattr(client, "_load_sibling", lambda name: None if name == "hook_pre_relevance.py" else loader(name))
    decision = {"permissionDecision": "deny"}
    monkeypatch.setattr(client, "_request", lambda *args: decision)
    assert client.decide("preToolUse", payload("view"), home=tmp_path) is decision


@pytest.mark.parametrize("event, bypass", [(payload("view"), True), (payload("powershell", "Get-Date"), True), (payload("edit"), False)])
@pytest.mark.skipif(os.name != "nt" or not os.environ.get("COPILOT_TEST_HOOK_PRE_PYTHON"), reason="explicit contained native interpreter fixture")
def test_cached_native_pre_command_uses_real_relevance(client, monkeypatch, tmp_path, event, bypass):
    from agent_procutil import no_window_kwargs
    from test_session_start_hook_fail_open import _hooks, _powershell, _run

    shell = _powershell()
    interpreter = Path(os.environ["COPILOT_TEST_HOOK_PRE_PYTHON"])
    assert interpreter.is_file()
    home = tmp_path / "home"
    cwd = tmp_path / "anchor"
    scripts = home / ".agent-worktrees" / "bin"
    scripts.mkdir(parents=True)
    cwd.mkdir()
    (cwd / ".git").mkdir()
    for name in ("hook_client.py", "hook_runtime.py", "hook_pre_relevance.py", "pretool_killswitch.py", "statelessness_guard.py", "cross_repo_guard.py", "anchor_write_guard.py", "anchor_shell_parser.py", "pr_supersede_guard.py", "registry_root.py"):
        shutil.copyfile(SCRIPTS / name, scripts / name)
    (scripts.parent / "repos.yaml").write_text(
        "repos:\n  fixture:\n    class: worktree\n    windows: '" + str(cwd) + "'\n", encoding="utf-8",
    )
    (scripts.parent / "status-monitor.lock").write_text(json.dumps({
        "hook_transport": "tcp", "hook_endpoint": "127.0.0.1:1", "hook_token": "fixture",
    }), encoding="utf-8")
    probe = tmp_path / "probe"
    probe.mkdir()
    marker = probe / "forbidden.jsonl"
    observed = probe / "interpreter.json"
    (probe / "sitecustomize.py").write_text(
        "import json, socket, subprocess, sys\nfrom pathlib import Path\n"
        + "Path(" + repr(str(observed)) + ").write_text(json.dumps(list(sys.version_info[:2])))\n"
        + "def forbidden(*args, **kwargs):\n"
        + "    with open(" + repr(str(marker)) + ", 'a') as output: output.write('reached\\n')\n"
        + "    raise OSError('fixture forbids IPC and subprocess')\n"
        + "socket.create_connection = forbidden\nsubprocess.run = forbidden\n", encoding="utf-8",
    )
    monkeypatch.setenv("PATH", str(interpreter.parent) + os.pathsep + os.environ.get("PATH", ""))
    monkeypatch.setenv("PYTHONPATH", str(probe))
    event = dict(event, cwd=str(cwd))
    if event["toolName"] == "edit":
        event["toolArgs"] = {"path": str(cwd / "protected.txt")}
    original_run = subprocess.run
    def contained_run(*args, **kwargs):
        return original_run(*args, input=json.dumps(event), timeout=30, **no_window_kwargs(), **kwargs)
    monkeypatch.setattr(subprocess, "run", contained_run)
    result = _run(str(_hooks("preToolUse")[0]["powershell"]), shell, home, cwd)
    assert result.returncode == 0, result.stderr
    assert json.loads(observed.read_text()) == [3, 9]
    assert marker.exists() is not bypass, result.stderr
    if bypass:
        assert result.stdout == ""
    else:
        assert result.stdout, result.stderr
        assert json.loads(result.stdout)["permissionDecision"] == "deny"
