from __future__ import annotations

import importlib.util
import io
import json
import os
from pathlib import Path

import pytest

PLUGIN = Path(__file__).resolve().parents[1]


def load_client():
    spec = importlib.util.spec_from_file_location("killswitch_hook", PLUGIN / "scripts/hook_client.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_running_client_hot_toggle_never_contacts_ipc_or_fallback_when_active(
    tmp_path, monkeypatch, capsys,
):
    path = tmp_path / "pretool.json"
    monkeypatch.setenv("PRETOOL_KILLSWITCH_STATE_FILE", str(path))
    monkeypatch.delenv("COPILOT_EXTENSIONS_CONTEXT", raising=False)
    client = load_client()
    calls = []
    monkeypatch.setattr(client, "_request", lambda *a: calls.append("ipc") or None)
    monkeypatch.setattr(client, "_fallback_pre", lambda *a: calls.append("guard") or {
        "permissionDecision": "deny", "permissionDecisionReason": "workflow policy",
    })
    for active, expected in ((False, ["ipc", "guard"]), (True, []), (False, ["ipc", "guard"])):
        path.write_text(json.dumps({"active": active}), encoding="utf-8")
        calls.clear()
        monkeypatch.setattr(client.sys, "stdin", io.StringIO('{"toolName":"powershell"}'))
        assert client.main(["preToolUse"]) == 0
        output = capsys.readouterr().out
        assert calls == expected
        assert output == "" if active else json.loads(output)["permissionDecision"] == "deny"


def test_active_switch_is_not_consulted_for_other_events(tmp_path, monkeypatch, capsys):
    path = tmp_path / "pretool.json"
    path.write_text('{"active": true}', encoding="utf-8")
    monkeypatch.setenv("PRETOOL_KILLSWITCH_STATE_FILE", str(path))
    client = load_client()
    monkeypatch.setattr(client, "_enrich_session_payload", lambda p: p)
    monkeypatch.setattr(client, "_enrich_session_end_payload", lambda p: p)
    monkeypatch.setattr(client, "_write_session_guidance", lambda *a, **k: None)
    calls = []
    monkeypatch.setattr(client, "decide", lambda kind, payload: calls.append(kind) or {})
    for event in ("postToolUse", "sessionStart", "sessionEnd"):
        monkeypatch.setattr(client.sys, "stdin", io.StringIO("{}"))
        assert client.main([event]) == 0
    assert calls == ["postToolUse", "sessionStart", "sessionEnd"]
    assert capsys.readouterr().out == "{}"


def test_invalid_switch_and_cell_keep_workflow_guards_enabled(tmp_path, monkeypatch, capsys):
    path = tmp_path / "pretool.json"
    monkeypatch.setenv("PRETOOL_KILLSWITCH_STATE_FILE", str(path))
    client = load_client()
    calls = []
    monkeypatch.setattr(client, "decide", lambda kind, p: calls.append(kind) or {})
    for context, raw in (("", "broken"), ("other-cell", '{"active": true}')):
        monkeypatch.setenv("COPILOT_EXTENSIONS_CONTEXT", context)
        path.write_text(raw, encoding="utf-8")
        monkeypatch.setattr(client.sys, "stdin", io.StringIO("{}"))
        assert client.main(["preToolUse"]) == 0
    assert calls == ["preToolUse", "preToolUse"]
    assert "invalid state" in capsys.readouterr().err


@pytest.mark.portfolio_tier("T2")
@pytest.mark.effect("process")
@pytest.mark.effect("network")
@pytest.mark.effect("filesystem")
@pytest.mark.skipif(
    os.environ.get("AGENT_WORKTREES_SHELL_HOOK_INTEGRATION") != "1",
    reason="explicit native cached-hook integration",
)
def test_cached_manifest_command_hot_stop_and_restore_without_restart(tmp_path):
    import os
    import shutil
    import subprocess
    import sys

    from agent_procutil import no_window_kwargs
    from agent_worktrees.hook_ipc import HookIpcServer

    shell = (shutil.which("pwsh") or shutil.which("powershell")) if os.name == "nt" else shutil.which("bash")
    if shell is None:
        pytest.skip("native hook shell unavailable")
    root = tmp_path / ".agent-worktrees"
    scripts = root / "bin"
    scripts.mkdir(parents=True)
    for name in ("hook_client.py", "hook_runtime.py", "pretool_killswitch.py", "hook_pre_policy.py"):
        shutil.copyfile(PLUGIN / "scripts" / name, scripts / name)
    env = os.environ.copy()
    env.update(HOME=str(tmp_path), USERPROFILE=str(tmp_path),
               PRETOOL_KILLSWITCH_STATE_FILE=str(tmp_path / "pretool.json"))
    env.pop("COPILOT_EXTENSIONS_CONTEXT", None)
    env["PATH"] = str(Path(sys.executable).parent) + os.pathsep + env["PATH"]
    hooks = json.loads((PLUGIN / "hooks.json").read_text())["hooks"]
    key = "powershell" if os.name == "nt" else "bash"
    cached = {event: hooks[event][0][key] for event in ("preToolUse", "postToolUse")}
    seen = []
    server = HookIpcServer(lambda kind, payload, deadline: seen.append(kind) or {
        "permissionDecision": "deny", "permissionDecisionReason": "resident workflow policy",
    })
    server.start()
    try:
        (root / "status-monitor.lock").write_text(json.dumps(server.rendezvous()), encoding="utf-8")
        def invoke(event):
            args = ([shell, "-NoProfile", "-NonInteractive", "-Command", cached[event]]
                    if os.name == "nt" else [shell, "-c", cached[event]])
            result = subprocess.run(args, input='{"toolName":"powershell"}',
                                    capture_output=True, text=True, env=env, timeout=15,
                                    **no_window_kwargs())
            assert result.returncode == 0, result.stderr
            return result
        def control(verb):
            result = subprocess.run([sys.executable, str(scripts / "pretool_killswitch.py"),
                                     "pretool-killswitch", verb, "--json"],
                                    capture_output=True, text=True, env=env, timeout=10,
                                    **no_window_kwargs())
            assert result.returncode == 0, result.stderr
            return json.loads(result.stdout)
        assert json.loads(invoke("preToolUse").stdout)["permissionDecision"] == "deny"
        assert seen == ["preToolUse"]
        assert control("on")["active"] is True
        assert invoke("preToolUse").stdout == ""
        assert seen == ["preToolUse"]
        assert json.loads(invoke("postToolUse").stdout)["permissionDecision"] == "deny"
        assert seen == ["preToolUse", "postToolUse"]
        assert control("off")["active"] is False
        assert json.loads(invoke("preToolUse").stdout)["permissionDecision"] == "deny"
        assert seen == ["preToolUse", "postToolUse", "preToolUse"]
        (root / "status-monitor.lock").unlink()
        marker = tmp_path / "fallback-ran"
        (scripts / "statelessness_guard.py").write_text(
            "from pathlib import Path\n"
            "def decide(payload, *, home):\n"
            "    (home / 'fallback-ran').write_text('ran')\n"
            "    return {'permissionDecision': 'deny', 'permissionDecisionReason': 'fallback policy'}\n",
            encoding="utf-8",
        )
        assert json.loads(invoke("preToolUse").stdout)["permissionDecisionReason"] == "fallback policy"
        assert marker.exists()
        marker.unlink()
        control("on")
        assert invoke("preToolUse").stdout == ""
        assert not marker.exists()
        control("off")
        assert json.loads(invoke("preToolUse").stdout)["permissionDecisionReason"] == "fallback policy"
        assert marker.exists()
    finally:
        server.close()


def test_relative_override_diagnostic_does_not_skip_normal_guard_evaluation(monkeypatch, capsys):
    monkeypatch.delenv("COPILOT_EXTENSIONS_CONTEXT", raising=False)
    monkeypatch.setenv("PRETOOL_KILLSWITCH_STATE_FILE", "relative-stop.json")
    client = load_client()
    calls = []
    monkeypatch.setattr(client, "decide", lambda kind, payload: calls.append(kind) or {
        "permissionDecision": "deny", "permissionDecisionReason": "workflow policy",
    })
    monkeypatch.setattr(client.sys, "stdin", io.StringIO("{}"))
    assert client.main(["preToolUse"]) == 0
    result = capsys.readouterr()
    assert calls == ["preToolUse"]
    assert json.loads(result.out)["permissionDecision"] == "deny"
    assert "absolute path" in result.err


@pytest.mark.parametrize("selected", ["central", "legacy", "override"])
def test_first_install_honors_each_bootstrap_namespace_before_stamp(
    tmp_path, monkeypatch, selected,
):
    from types import SimpleNamespace
    client = load_client()
    scripts = tmp_path / "payload/scripts"
    scripts.mkdir(parents=True)
    for name in ("bootstrap-check.ps1", "install.ps1", "command_binding.py"):
        (scripts / name).touch()
    home = tmp_path / "home"
    home.mkdir()
    primary = home / ".copilot-extensions/killSwitches/bootstrap-killswitch.json"
    legacy = home / ".copilot-extensions/bootstrap-killswitch.json"
    explicit = tmp_path / "explicit.json"
    chosen = {"central": primary, "legacy": legacy, "override": explicit}[selected]
    chosen.parent.mkdir(parents=True, exist_ok=True)
    chosen.write_text(json.dumps({"active": True, "reason": selected + " pause"}), encoding="utf-8")
    environment = dict(os.environ)
    environment.pop("COPILOT_EXTENSIONS_CONTEXT", None)
    environment.pop("BOOTSTRAP_KILLSWITCH_STATE_FILE", None)
    if selected == "override":
        environment["BOOTSTRAP_KILLSWITCH_STATE_FILE"] = str(explicit)
    monkeypatch.setattr(client, "os", SimpleNamespace(name="nt", environ=environment))
    monkeypatch.setattr(client, "__file__", str(scripts / "hook_client.py"))
    monkeypatch.setattr(client.Path, "home", classmethod(lambda cls: home))
    monkeypatch.setattr(client.shutil, "which", lambda name: str(tmp_path / "powershell.exe"))
    monkeypatch.setattr(client.subprocess, "run", lambda *a, **k: pytest.fail("paused bootstrap reached stamp"))
    result = client._bootstrap_first_install({"sessionId": "namespace-control"})
    assert selected + " pause" in result["_stderr"]


def test_first_install_central_inactive_does_not_revive_legacy_pause(tmp_path, monkeypatch):
    from types import SimpleNamespace
    client = load_client()
    scripts = tmp_path / "payload/scripts"
    scripts.mkdir(parents=True)
    for name in ("bootstrap-check.ps1", "install.ps1", "command_binding.py"):
        (scripts / name).touch()
    home = tmp_path / "home"
    primary = home / ".copilot-extensions/killSwitches/bootstrap-killswitch.json"
    primary.parent.mkdir(parents=True)
    primary.write_text('{"active":false}', encoding="utf-8")
    primary.parent.parent.joinpath("bootstrap-killswitch.json").write_text('{"active":true}', encoding="utf-8")
    environment = dict(os.environ)
    environment.pop("COPILOT_EXTENSIONS_CONTEXT", None)
    environment.pop("BOOTSTRAP_KILLSWITCH_STATE_FILE", None)
    monkeypatch.setattr(client, "os", SimpleNamespace(name="nt", environ=environment))
    monkeypatch.setattr(client, "__file__", str(scripts / "hook_client.py"))
    monkeypatch.setattr(client.Path, "home", classmethod(lambda cls: home))
    monkeypatch.setattr(client.shutil, "which", lambda name: str(tmp_path / "powershell.exe"))
    calls = []
    monkeypatch.setattr(client.subprocess, "run", lambda *a, **k: calls.append(a[0]) or SimpleNamespace(
        returncode=0, stdout="", stderr="",
    ))
    assert client._bootstrap_first_install({"sessionId": "namespace-off"}) == {}
    assert len(calls) == 1
    assert "--run-lifecycle" in calls[0]
