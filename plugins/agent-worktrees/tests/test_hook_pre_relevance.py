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

def _pretool_recovery_fixture(tmp_path, fault):
    home = tmp_path / "home"
    cwd = home / "consumer"
    scripts = home / ".agent-worktrees" / "bin"
    scripts.mkdir(parents=True)
    (cwd / ".git").mkdir(parents=True)
    (cwd / ".agent-worktrees").mkdir()
    (cwd / ".agent-worktrees" / "config.yaml").write_text("stateless: true\n", encoding="utf-8")
    for path in SCRIPTS.glob("*.py"):
        shutil.copyfile(path, scripts / path.name)
    shutil.copyfile(SCRIPTS / "pre-tool-hook.ps1", scripts / "pre-tool-hook.ps1")
    interpreter = sys.executable.replace("'", "''")
    (scripts / "resolve-runtime.ps1").write_text(
        "param([string]$TraceMode)\n$AwPy = '" + interpreter + "'\n", encoding="utf-8-sig",
    )
    if fault == "missing-entry":
        (scripts / "pre-tool-hook.ps1").unlink()
        (scripts / "hook_client.py").unlink()
    elif fault in ("compat-deny-failure", "compat-partial-failure"):
        (scripts / "pre-tool-hook.ps1").unlink()
        output = '{"permissionDecision":"deny","permissionDecisionReason":"confirmed fixture policy"}'
        if fault == "compat-partial-failure":
            output = '{"permissionDecision":'
        (scripts / "hook_client.py").write_text(
            "import sys\nsys.stdout.write(" + repr(output) + ")\nsys.stdout.flush()\nraise SystemExit(3)\n",
            encoding="utf-8",
        )
    elif fault == "missing-sibling-protected":
        (scripts / "pr_supersede_guard.py").unlink()
    elif fault == "unsafe-sibling-protected":
        (scripts / "pr_supersede_guard.py").write_bytes(b" " * 131073)
    elif fault == "missing-policy":
        (scripts / "hook_pre_policy.py").unlink()
    elif fault == "broken-policy":
        (scripts / "hook_pre_policy.py").write_text(
            "raise RuntimeError('fixture policy import failed')\n", encoding="utf-8",
        )
    if fault.startswith("failed-component-"):
        (scripts / "statelessness_guard.py").write_text(
            "def decide(*args, **kwargs):\n    raise RuntimeError('fixture component failure')\n",
            encoding="utf-8",
        )
        (scripts.parent / "repos.yaml").write_text(
            "repos:\n  fixture:\n    class: worktree\n    windows: '" + str(cwd) + "'\n",
            encoding="utf-8",
        )
    env = dict(os.environ, HOME=str(home), USERPROFILE=str(home), AGENT_HOME=str(home))
    if fault == "failed-component-ask":
        env["ANCHOR_WRITE_GUARD_MODE"] = "ask"
    for name in (
        "COPILOT_EXTENSIONS_CONTEXT", "COPILOT_PLUGIN_ROOT", "AGENT_RT_ROOT",
        "AGENT_WORKTREES_PAYLOAD_ROOT", "AGENT_WORKTREES_PRE_TOOL_IMPLEMENTATION",
    ):
        env.pop(name, None)
    env["PRETOOL_KILLSWITCH_STATE_FILE"] = str(home / "absent-stop.json")
    return home, cwd, scripts, env


def _assert_pretool_recovery_outcome(result):
    assert "permissionDecision" not in result, result
    context = result.get("additionalContext")
    assert isinstance(context, str) and context, result
    for field in ("agent-worktrees", "effect:", "next:", "retry:"):
        assert field in context.lower(), context


def _run_pretool_manifest(tmp_path, consumer, fault, protected=False, read=False, raw=False):
    import shlex

    from agent_procutil import no_window_kwargs
    from test_session_start_hook_fail_open import _bash, _hooks, _powershell

    home, cwd, scripts, env = _pretool_recovery_fixture(tmp_path, fault)
    target = cwd / "efforts" / "active" / "fixture.md"
    event = {
        "toolName": "create" if protected else "view" if read else "powershell",
        "cwd": str(cwd),
        "toolArgs": {"path": str(target)} if protected or read else {
            "command": "agent-worktrees doctor --json",
        },
    }
    payload_text = "[]" if fault == "malformed-event" else json.dumps(event)
    binaries = home / "bin"
    binaries.mkdir()
    if consumer == "powershell":
        shell = _powershell()
        argv = [shell, "-NoLogo", "-NoProfile", "-Command", _hooks("preToolUse")[0][consumer]]
        if fault == "missing-runtime":
            (scripts / "resolve-runtime.ps1").write_text(
                "param([string]$TraceMode)\n$AwPy = $null\n", encoding="utf-8-sig",
            )
            env["PATH"] = str(binaries)
    else:
        shell = _bash()
        if fault == "missing-runtime":
            assert (scripts / "hook_client.py").is_file()
            env["PATH"] = str(binaries)
        else:
            shim = binaries / "python3"
            shim.write_text(
                "#!/bin/sh\nexec " + shlex.quote(Path(sys.executable).as_posix()) + ' "$@"\n',
                encoding="utf-8", newline="\n",
            )
            shim.chmod(0o755)
            env["PATH"] = str(binaries) + os.pathsep + env.get("PATH", "")
        argv = [shell, "-c", _hooks("preToolUse")[0][consumer]]
    completed = subprocess.run(
        argv, input=payload_text, capture_output=True, text=True, encoding="utf-8",
        cwd=cwd, env=env, timeout=35, **no_window_kwargs(),
    )
    assert completed.returncode == 0, (completed.stdout, completed.stderr)
    assert not target.exists()
    if raw:
        return completed
    return json.loads(completed.stdout or "{}")


@pytest.mark.parametrize("consumer", ["powershell", "bash"])
@pytest.mark.parametrize("fault", [
    "missing-entry", "missing-policy", "broken-policy", "missing-runtime", "malformed-event",
])
def test_pretool_manifest_unavailable_keeps_repair_usable(tmp_path, consumer, fault):
    _assert_pretool_recovery_outcome(_run_pretool_manifest(tmp_path, consumer, fault))


@pytest.mark.parametrize("consumer", ["powershell", "bash"])
def test_pretool_manifest_unavailable_keeps_read_usable(tmp_path, consumer):
    _assert_pretool_recovery_outcome(_run_pretool_manifest(tmp_path, consumer, "missing-runtime", read=True))


@pytest.mark.parametrize("consumer", ["powershell", "bash"])
def test_pretool_manifest_confirmed_violation_still_denied(tmp_path, consumer):
    result = _run_pretool_manifest(tmp_path, consumer, "none", protected=True)
    assert result.get("permissionDecision") == "deny", result
    assert "personal" in result.get("permissionDecisionReason", "").lower(), result


@pytest.mark.parametrize("consumer", ["powershell", "bash"])
@pytest.mark.parametrize("decision", ["deny", "ask"])
def test_pretool_manifest_component_failure_retains_protected_decision(tmp_path, consumer, decision):
    result = _run_pretool_manifest(tmp_path, consumer, "failed-component-" + decision, protected=True)
    assert result.get("permissionDecision") == decision, result
    assert "anchor" in result.get("permissionDecisionReason", "").lower(), result
    assert "statelessness_guard.py evaluation failed" in result.get("additionalContext", ""), result


@pytest.mark.parametrize("consumer", ["powershell", "bash"])
def test_pretool_missing_sibling_retains_confirmed_violation(tmp_path, consumer):
    result = _run_pretool_manifest(tmp_path, consumer, "missing-sibling-protected", protected=True)
    assert result.get("permissionDecision") == "deny", result
    assert "personal" in result.get("permissionDecisionReason", "").lower(), result


@pytest.mark.parametrize("consumer", ["powershell", "bash"])
def test_pretool_compat_failed_entry_preserves_streamed_deny(tmp_path, consumer):
    completed = _run_pretool_manifest(tmp_path, consumer, "compat-deny-failure", raw=True)
    assert json.loads(completed.stdout) == {
        "permissionDecision": "deny", "permissionDecisionReason": "confirmed fixture policy",
    }
    assert "output completeness is unknown" in completed.stderr


@pytest.mark.parametrize("consumer", ["powershell", "bash"])
def test_pretool_compat_partial_failure_does_not_append_replacement_json(tmp_path, consumer):
    completed = _run_pretool_manifest(tmp_path, consumer, "compat-partial-failure", raw=True)
    assert completed.stdout == '{"permissionDecision":'
    assert "output completeness is unknown" in completed.stderr


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
    for name in ("hook_client.py", "hook_runtime.py", "hook_pre_relevance.py", "hook_pre_policy.py", "hook_policy_process.py", "pretool_killswitch.py", "statelessness_guard.py", "cross_repo_guard.py", "anchor_write_guard.py", "anchor_shell_parser.py", "pr_supersede_guard.py", "registry_root.py"):
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
